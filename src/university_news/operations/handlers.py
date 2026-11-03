"""Stage implementations used by durable workers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from ..enrichment.client import ClippingEnricher, EnrichmentSettings
from ..enrichment.schema import ClippingSchema
from ..paths import indexes_directory, sites_directory
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
)
from ..settings import config_section
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
        raw_directory = sites_directory(self.data_root) / job.site / "raw"
        fetcher = ContentFetcher(site, raw_directory, concurrency=1)
        result = asyncio.run(fetcher.fetch([job.url]))
        if result.errors:
            error = result.errors[0]
            error_type = str(error.get("error") or "")
            raise WorkerStageError(
                str(error.get("message") or error),
                transient=error_type
                in {"ConnectError", "ReadTimeout", "RetryableHttpStatus"},
            )
        raw_path = fetcher.path_for_url(job.url)
        digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        status_code = (
            int(result.manifest_records[0]["status_code"])
            if result.manifest_records
            else None
        )
        self.store.save_raw_document(
            job.article_id,
            path=str(raw_path.relative_to(self.data_root)),
            sha256=digest,
            status_code=status_code,
        )
        return ("extract",)

    def extract(self, job: Job) -> tuple[str, ...]:
        root = sites_directory(self.data_root) / job.site
        extractor = StructuredExtractor(
            root / "raw",
            root / "articles.jsonl",
            site=SITES[job.site],
        )
        raw_path = root / "raw" / ContentFetcher.filename_for_url(job.url)
        article = extractor.extract_file(raw_path, job.url)
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
        self._load_embedding_backend()
        assert self._embedder is not None
        assert self._lance_table is not None
        embedded = self._embedder.encode_documents([content])
        digest = indexed_hash(article, content)
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
        # Each single-row write adds a table version; compact periodically so
        # the index never grows into thousands of versions again.
        self._embedded_since_compaction += 1
        if self._embedded_since_compaction >= EMBED_COMPACTION_INTERVAL:
            compact_table(self._lance_table)
            self._embedded_since_compaction = 0
        return ()

    def _load_embedding_backend(self) -> None:
        if self._embedder is not None:
            return
        model_path = str(self._embedding_settings.get("model_path", "")).strip()
        self._embedder = QwenGGUFEmbedder(
            model_path=Path(model_path) if model_path else None,
            model_repo=str(self._embedding_settings.get("model_repo")),
            model_file=str(self._embedding_settings.get("model_file")),
            context_length=int(self._embedding_settings.get("context_length", 32_768)),
            threads=int(self._embedding_settings.get("threads", 8)),
            gpu_layers=int(self._embedding_settings.get("gpu_layers", -1)),
        )
        database = connect_database(self._lance_path)
        self._lance_table = open_or_create_table(
            database,
            self._lance_table_name,
            self._embedder.dimension,
            recreate=False,
        )
        metadata = load_index_metadata(self._lance_path)
        expected = {
            "model_repo": self._embedder.model_repo,
            "model_file": self._embedder.model_file,
            "context_length": self._embedder.context_length,
            "dimension": self._embedder.dimension,
        }
        mismatched = [
            key for key, value in expected.items() if metadata.get(key) != value
        ]
        if self._lance_table.count_rows() and mismatched:
            raise RuntimeError(
                "Embedding configuration changed for the active LanceDB table "
                f"({', '.join(mismatched)}). Select a new table explicitly; "
                "existing articles will not be reprocessed automatically."
            )
        write_metadata(
            self._lance_path,
            table=self._lance_table_name,
            embedder=self._embedder,
        )
