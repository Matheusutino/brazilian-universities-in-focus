"""Live PostgreSQL article queries, BM25 retrieval and LanceDB rank fusion."""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..search.hybrid import RRF_K, SearchFilters, rrf_fuse
from ..shared.articles import flatten_annotation
from ..sources.catalog import SITE_REGIONS, region_for_site
from ..search.semantic import SemanticHealth

CANDIDATES = 60
MAX_SEMANTIC_CANDIDATES = 1920
TOKEN = re.compile(r"\w+", re.UNICODE)
CARD_COLUMNS = "a.id, a.url, a.title, a.subtitle, a.author, a.date, a.site, a.annotation"


@dataclass(frozen=True, slots=True)
class ArticleQuery:
    text: str = ""
    region: str | None = None
    university: str | None = None
    start: date | None = None
    end: date | None = None
    category: str | None = None
    field: str | None = None
    content_type: str | None = None
    analyzed_only: bool = False

@dataclass(slots=True)
class SearchPage:
    total: int
    results: list[dict[str, Any]]
    mode: str
    elapsed_ms: float = 0.0
    notes: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    # Hybrid retrieval is bounded; this is never advertised as corpus-wide count.
    total_is_exact: bool = True
    retrieval: dict[str, int] = field(default_factory=dict)


class QueryEncoder(Protocol):
    def encode_query(self, query: str) -> list[float]: ...


class CachedEncoder:
    """Thread-safe LRU in front of the query embedding model."""

    def __init__(self, encoder: QueryEncoder, size: int = 2048) -> None:
        self.encoder = encoder
        self.size = size
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def encode_query(self, query: str) -> list[float]:
        key = " ".join(query.lower().split())
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        vector = self.encoder.encode_query(key)
        with self._lock:
            self._cache[key] = vector
            while len(self._cache) > self.size:
                self._cache.popitem(last=False)
        return vector


def search_text(text: str) -> str:
    """Pass only literal words to the tokenizer, never query-parser syntax."""
    return " ".join(dict.fromkeys(
        token for token in TOKEN.findall(text.lower()) if len(token) > 1
    ))[:200]


def filter_sql(query: ArticleQuery) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if query.region:
        clauses.append("a.site = ANY(%s)")
        params.append([site for site, region in SITE_REGIONS.items() if region == query.region])
    if query.university:
        clauses.append("a.site = %s")
        params.append(query.university.lower())
    if query.start:
        clauses.append("a.date >= %s")
        params.append(query.start)
    if query.end:
        clauses.append("a.date <= %s")
        params.append(query.end)
    if query.analyzed_only:
        clauses.append("a.annotation IS NOT NULL")
    if query.category:
        clauses.append("a.category = %s")
        params.append(query.category)
    if query.content_type:
        clauses.append("a.content_type = %s")
        params.append(query.content_type)
    if query.field:
        clauses.append("a.fields @> %s::text[]")
        params.append([query.field])
    return (" AND ".join(clauses) or "TRUE"), params


def article_record(row: dict[str, Any]) -> dict[str, Any]:
    record = dict(row)
    record["id"] = str(row["id"])
    record["date"] = row["date"].isoformat() if row["date"] else None
    record["journal"] = row["site"].upper()
    record["region"] = region_for_site(row["site"])
    record["annotated"] = row["annotation"] is not None
    record["annotation"] = flatten_annotation(row["annotation"]) if row["annotation"] else None
    record.pop("site", None)
    return record


def card(row: dict[str, Any]) -> dict[str, Any]:
    record = article_record(row)
    annotation = record.pop("annotation")
    record.pop("annotated")
    record["analysis"] = (
        {
            "category": annotation.get("classification.news_category"),
            "fields": annotation.get("topics.scientific_fields") or [],
            "summary": annotation.get("summary.one_sentence"),
        } if annotation else None
    )
    return record


class ArticleIndex:
    """Bounded, read-only PostgreSQL connections shared by API threads.

    The web process never creates extensions, applies migrations or writes
    articles. Database initialization is an explicit deployment operation.
    """

    def __init__(
        self,
        dsn: str,
        *,
        semantic: Callable[[list[float], int, SearchFilters], list[str]] | None = None,
        encoder: QueryEncoder | None = None,
        max_connections: int = 4,
        statement_timeout_ms: int = 10000,
    ) -> None:
        if not dsn:
            raise ValueError("Set SITE_DATABASE_URL or DATABASE_URL")
        if max_connections < 1 or statement_timeout_ms < 1:
            raise ValueError("Connection and timeout limits must be positive")
        self.semantic = semantic
        self.encoder = encoder
        self.pool = ConnectionPool(
            dsn, min_size=1, max_size=max_connections, timeout=10,
            kwargs={
                "autocommit": True, "row_factory": dict_row,
                "connect_timeout": 5,
                "options": f"-c default_transaction_read_only=on -c statement_timeout={statement_timeout_ms}",
            },
            open=False,
        )
        self.semantic_health: SemanticHealth | None = None

    def open(self) -> None:
        self.pool.open(wait=True, timeout=10)

    def close(self) -> None:
        self.pool.close()

    def health(self) -> None:
        with self.pool.connection() as connection:
            connection.execute("SELECT id FROM site_documents LIMIT 1").fetchone()
            available = connection.execute(
                "SELECT to_regclass('public.site_documents_bm25') AS name"
            ).fetchone()
            if not available["name"]:
                raise RuntimeError("Run university-news-site init-search before serving")

    def article(self, identifier: str) -> dict[str, Any] | None:
        try:
            identifier = uuid.UUID(identifier)
        except ValueError:
            return None
        with self.pool.connection() as connection:
            row = connection.execute(
                f"SELECT {CARD_COLUMNS} FROM site_documents a WHERE a.id = %s",
                (identifier,),
            ).fetchone()
        return article_record(row) if row else None

    def latest(self, query: ArticleQuery, *, offset: int, limit: int) -> SearchPage:
        where, params = filter_sql(query)
        with self.pool.connection() as connection, connection.transaction():
            # Count and rows see the same committed data.
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            total = connection.execute(
                f"SELECT count(*) AS total FROM site_documents a WHERE {where}", params
            ).fetchone()["total"]
            rows = connection.execute(
                f"SELECT {CARD_COLUMNS} FROM site_documents a WHERE {where} "
                "ORDER BY a.date DESC NULLS LAST, a.id LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()
        return SearchPage(total=total, results=[card(row) for row in rows], mode="latest")

    def lexical(self, query: ArticleQuery) -> list[str]:
        text = search_text(query.text)
        if not text:
            return []
        where, params = filter_sql(query)
        with self.pool.connection() as connection:
            rows = connection.execute(
                "SELECT a.id, pdb.score(a.id) AS score FROM site_documents a "
                "WHERE (a.title ||| %s::pdb.boost(4) OR a.keywords ||| %s::pdb.boost(2) "
                "OR a.subtitle ||| %s::pdb.boost(1.5) OR a.summary ||| %s) "
                f"AND {where} ORDER BY score DESC, a.id LIMIT %s",
                [text, text, text, text, *params, CANDIDATES],
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def _matching_rows(
        self, identifiers: list[str], query: ArticleQuery
    ) -> dict[str, dict[str, Any]]:
        if not identifiers:
            return {}
        where, params = filter_sql(query)
        valid_ids = []
        for identifier in identifiers:
            try:
                valid_ids.append(uuid.UUID(identifier))
            except ValueError:
                continue
        if not valid_ids:
            return {}
        with self.pool.connection() as connection:
            rows = connection.execute(
                f"SELECT {CARD_COLUMNS} FROM site_documents a "
                f"WHERE a.id = ANY(%s) AND {where}", [valid_ids, *params]
            ).fetchall()
        return {str(row["id"]): row for row in rows}

    def search(
        self, query: ArticleQuery, *, offset: int, limit: int, semantic_enabled: bool = True
    ) -> SearchPage:
        started = time.perf_counter()
        lexical = self.lexical(query)
        timings = {"lexical": round((time.perf_counter() - started) * 1000, 1)}
        semantic: list[str] = []
        notes: list[str] = []
        encoder, semantic_search = self.encoder, self.semantic
        verified = self.semantic_health is None or self.semantic_health.snapshot()["ready"]
        if not semantic_enabled:
            notes.append("lexical only (requested)")
        elif semantic_search is not None and encoder is not None and verified:
            try:
                stage = time.perf_counter()
                vector = encoder.encode_query(query.text)
                timings["embedding"] = round((time.perf_counter() - stage) * 1000, 1)
                stage = time.perf_counter()
                filters = SearchFilters(
                    journals=(query.university.upper(),) if query.university else (),
                    regions=(query.region,) if query.region else (),
                    start_date=query.start, end_date=query.end,
                )
                # Grow the ANN window when annotation filters or stale vectors
                # remove hits. Every hit is checked against authoritative rows.
                count = CANDIDATES
                while True:
                    hits = semantic_search(vector, count, filters)
                    matching = self._matching_rows(hits, query)
                    semantic = [identifier for identifier in hits if identifier in matching]
                    if len(semantic) >= CANDIDATES or len(hits) < count or count >= MAX_SEMANTIC_CANDIDATES:
                        break
                    count *= 2
                semantic = semantic[:CANDIDATES]
                timings["ann"] = round((time.perf_counter() - stage) * 1000, 1)
            except Exception as exc:
                if self.semantic_health is not None:
                    self.semantic_health.failure(exc)
                semantic = []
                notes.append(f"semantic search unavailable: {type(exc).__name__}")
        else:
            notes.append("semantic search not loaded")
        scores = rrf_fuse(semantic, lexical, k=RRF_K)
        ranked = sorted(scores, key=lambda identifier: (-scores[identifier], identifier))
        rows = self._matching_rows(ranked, query)
        ranked = [identifier for identifier in ranked if identifier in rows]
        return SearchPage(
            total=len(ranked),
            results=[card(rows[identifier]) for identifier in ranked[offset:offset + limit]],
            mode="hybrid" if semantic else "lexical",
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            notes=notes, timings=timings, total_is_exact=False,
            retrieval={"lexical_candidates": len(lexical), "semantic_candidates": len(semantic)},
        )

    def publication_snapshot(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Read metadata for static pages, without exporting or loading bodies."""
        with self.pool.connection() as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            rows = connection.execute(
                f"SELECT {CARD_COLUMNS} FROM site_documents a "
                "ORDER BY a.date DESC NULLS LAST, a.id"
            ).fetchall()
            profiles = connection.execute(
                "SELECT p.id, p.provider, p.model, p.schema_version, p.prompt_sha256, "
                "count(d.id) AS annotations FROM enrichment_profiles p "
                "JOIN site_documents d ON d.annotation->>'profile_id' = p.id "
                "GROUP BY p.id ORDER BY p.id"
            ).fetchall()
        return [article_record(row) for row in rows], list(profiles)
