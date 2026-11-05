"""Stage implementations used by durable workers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import httpx

from ..enrichment.client import ClippingEnricher, EnrichmentSettings
from ..enrichment.schema import ClippingSchema
from ..paths import indexes_directory
from ..pipeline.extraction import StructuredExtractor
from ..pipeline.fetching import ContentFetcher
from ..pipeline.preprocessing import (
    load_portuguese_detector,
    preprocess_records_with_rejections,
)
from ..search.embeddings import QwenGGUFEmbedder
from ..search.vector_index import (
    DEFAULT_TABLE,
    article_content,
    connect_database,
    compact_table,
    content_hash,
    index_records,
    indexed_hash,
    load_index_metadata,
    open_or_create_table,
    valid_date,
    write_metadata,
    embedding_contract,
    ensure_indexes,
    ANN_MIN_ROWS,
)
from ..settings import config_section
from ..shared.http_client import RetryableHttpStatus
from ..sources.catalog import SITES, region_for_site
from .store import Job, PostgresStore


EMBED_COMPACTION_INTERVAL = 1000


class WorkerStageError(RuntimeError):
    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


def load_enricher(provider: str | None = None) -> ClippingEnricher:
    """Build the enricher for a provider; share one per process so pacing is global."""
    return ClippingEnricher(
        EnrichmentSettings.from_config(provider=provider), ClippingSchema.load()
    )


class StageHandlers:
    def __init__(
        self,
        store: PostgresStore,
        data_root: Path,
        *,
        enricher: ClippingEnricher | None = None,
        run_id: uuid.UUID | None = None,
    ) -> None:
        self.store = store
        self.data_root = data_root
        self.run_id = run_id
        self._detector = None
        self._enricher = enricher
        self._embedder: QwenGGUFEmbedder | None = None
        self._lance_table = None
        search = config_section("search")
        self._embedding_settings = config_section("embedding")
        self._lance_path = Path(
            str(search.get("lancedb_path", indexes_directory(data_root) / "lancedb"))
        )
        self._lance_table_name = str(search.get("lancedb_table", DEFAULT_TABLE))
        self._embedded_since_compaction = 0

    def process(self, job: Job) -> tuple[str, ...]:
        return {
            "fetch": self.fetch,
            "extract": self.extract,
            "preprocess": self.preprocess,
            "enrich": self.enrich,
            "embed": self.embed,
        }[job.stage](job)

    def fetch(self, job: Job) -> tuple[str, ...]:
        site = SITES[job.site]
        fetcher = ContentFetcher(site, concurrency=1)
        try:
            response = asyncio.run(fetcher.fetch_document(job.url))
        except (httpx.HTTPError, RetryableHttpStatus) as error:
            raise WorkerStageError(
                str(error),
                transient=isinstance(error, (httpx.TransportError, RetryableHttpStatus)),
            ) from error
        with self.store.lease_guard(job):
            self.store.save_raw_document(
                job.article_id,
                content=response.content,
                status_code=response.status_code,
                encoding=response.encoding,
                content_type=response.headers.get("content-type"),
                final_url=str(response.url),
            )
        return ("extract",)

    def extract(self, job: Job) -> tuple[str, ...]:
        document = self.store.get_raw_document(job.article_id)
        html = bytes(document["content"]).decode(
            document["encoding"] or "utf-8", errors="replace"
        )
        extractor = StructuredExtractor(site=SITES[job.site])
        article = extractor.extract_html(html, job.url)
        with self.store.lease_guard(job):
            self.store.save_article(
                job.article_id,
                site=job.site,
                url=job.url,
                status="extracted",
                payload=article,
            )
        return ("preprocess",)

    def preprocess(self, job: Job) -> tuple[str, ...]:
        article = self.store.get_article(job.article_id)
        if self._detector is None:
            self._detector = load_portuguese_detector()
        accepted, rejected, _stats = preprocess_records_with_rejections(
            [article], job.site, self._detector, workers=1
        )
        if rejected:
            rejection = rejected[0]
            with self.store.lease_guard(job):
                self.store.save_article(
                    job.article_id,
                    site=job.site,
                    url=job.url,
                    status="rejected",
                    payload=dict(rejection["article"]),
                    rejection_reason=str(rejection["reason"]),
                )
            return ()

        processed = accepted[0]
        digest = hashlib.sha256(
            json.dumps(
                processed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with self.store.lease_guard(job):
            self.store.save_article(
                job.article_id,
                site=job.site,
                url=job.url,
                status="accepted",
                payload=processed,
                content_hash=digest,
            )
        return ("enrich", "embed")

    def enrich(self, job: Job) -> tuple[str, ...]:
        if self._enricher is None:
            self._enricher = load_enricher()
            self.store.register_enrichment_profile(self._enricher.profile.as_dict())
        profile_id = self._enricher.profile.id
        # Idempotent: a job requeued after a crash never pays for a second call.
        if self.store.has_enrichment(job.article_id, profile_id):
            return ()
        result = self._enricher.enrich(
            self.store.get_article(job.article_id), university=job.site
        )
        with self.store.lease_guard(job):
            self.store.save_enrichment(
                job.article_id, result, profile_id=profile_id, run_id=self.run_id
            )
        return ()

    def error_details(self, job: Job, error: Exception) -> dict[str, Any]:
        """Context persisted with a failure, including spent request metrics."""
        details: dict[str, Any] = {}
        if job.stage == "enrich" and self._enricher is not None:
            details["profile_id"] = self._enricher.profile.id
        metrics = getattr(error, "generation_metrics", None)
        if isinstance(metrics, dict):
            details["generation_metrics"] = metrics
        return details

    def embed(self, job: Job) -> tuple[str, ...]:
        article = self.store.get_article(job.article_id) | {"journal": job.site.upper()}
        content = article_content(article)
        if not content:
            raise ValueError("Article has no indexable content")
        self._load_embedding_backend(job)
        assert self._embedder is not None
        assert self._lance_table is not None
        embedded = self._embedder.encode_documents([content])
        digest = indexed_hash(article, content)
        with self.store.lease_guard(job):
            index_records(
                self._lance_table,
                [
                    {
                        "article_id": str(job.article_id),
                        "content_hash": digest,
                        "journal": job.site.upper(),
                        "region": region_for_site(job.site),
                        "date": valid_date(article.get("date")),
                        "truncated": embedded.truncated[0],
                        "vector": embedded.vectors[0],
                    }
                ],
            )
            self.store.save_embedding_state(
                job.article_id,
                content_hash=content_hash(content),
                model=f"{self._embedder.model_repo}/{self._embedder.model_file}",
            )
            # Keep the job row locked during LanceDB mutations so another
            # attempt cannot claim the job before this write completes.
            self._embedded_since_compaction += 1
            if self._lance_table.count_rows() == ANN_MIN_ROWS:
                ensure_indexes(self._lance_table, rebuild=False)
            if self._embedded_since_compaction >= EMBED_COMPACTION_INTERVAL:
                ensure_indexes(self._lance_table, rebuild=False)
                compact_table(self._lance_table)
                self._embedded_since_compaction = 0
        return ()

    def _load_embedding_backend(self, job: Job) -> None:
        if self._embedder is not None:
            return
        model_path = str(self._embedding_settings.get("model_path", "")).strip()
        embedder = QwenGGUFEmbedder(
            model_path=Path(model_path) if model_path else None,
            model_repo=str(self._embedding_settings.get("model_repo")),
            model_file=str(self._embedding_settings.get("model_file")),
            context_length=int(self._embedding_settings.get("context_length", 32_768)),
            threads=int(self._embedding_settings.get("threads", 8)),
            gpu_layers=int(self._embedding_settings.get("gpu_layers", -1)),
        )
        with self.store.lease_guard(job):
            database = connect_database(self._lance_path)
            table = open_or_create_table(
                database,
                self._lance_table_name,
                embedder.dimension,
                recreate=False,
            )
            metadata = load_index_metadata(self._lance_path)
            expected = embedding_contract(embedder, self._lance_table_name)
            mismatched = [
                key for key, value in expected.items() if metadata.get(key) != value
            ]
            if table.count_rows() and mismatched:
                raise RuntimeError(
                    "Embedding configuration changed for the active LanceDB table "
                    f"({', '.join(mismatched)}). Select a new table explicitly; "
                    "existing articles will not be reprocessed automatically."
                )
            if table.count_rows():
                ensure_indexes(table, rebuild=False)
            write_metadata(
                self._lance_path, table=self._lance_table_name, embedder=embedder,
            )
        # Publish only a fully initialized and validated backend.
        self._lance_table = table
        self._embedder = embedder
