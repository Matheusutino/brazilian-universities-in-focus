"""Article listing and hybrid search over the site bundle.

Lexical search uses SQLite FTS5 (BM25), semantic search uses the LanceDB ANN
index, and Reciprocal Rank Fusion merges both. Only the query embedding runs a
model; it is cached, and the site falls back to lexical results whenever the
model is unavailable, so search never fails because of it.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from ..search.hybrid import RRF_K, SearchFilters, lance_filter, rrf_fuse

# Candidates taken from each retriever before fusion.
CANDIDATES = 60
# BM25 column weights: title, subtitle, summary, keywords.
BM25_WEIGHTS = (4.0, 1.5, 1.0, 2.0)
TOKEN = re.compile(r"\w+", re.UNICODE)


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

    @property
    def uses_analysis(self) -> bool:
        return bool(self.category or self.field or self.content_type or self.analyzed_only)


@dataclass(slots=True)
class SearchPage:
    total: int
    results: list[dict[str, Any]]
    mode: str
    elapsed_ms: float = 0.0
    notes: list[str] = field(default_factory=list)
    # Per-stage milliseconds (lexical, embedding, ann), to tune small servers.
    timings: dict[str, float] = field(default_factory=dict)


class QueryEncoder(Protocol):
    def encode_query(self, query: str) -> list[float]: ...


class CachedEncoder:
    """LRU cache in front of the query model: repeated searches are free."""

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


def fts_query(text: str) -> str | None:
    """OR of quoted tokens, so punctuation never breaks the FTS5 syntax."""
    tokens = [token for token in TOKEN.findall(text.lower()) if len(token) > 1][:12]
    return " OR ".join(f'"{token}"' for token in dict.fromkeys(tokens)) or None


def filter_sql(query: ArticleQuery) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if query.region:
        clauses.append("a.region = ?")
        params.append(query.region)
    if query.university:
        clauses.append("a.journal = ?")
        params.append(query.university.upper())
    if query.start:
        clauses.append("a.date >= ?")
        params.append(query.start.isoformat())
    if query.end:
        clauses.append("a.date <= ?")
        params.append(query.end.isoformat())
    if query.analyzed_only:
        clauses.append("a.annotated = 1")
    if query.category:
        clauses.append("a.category = ?")
        params.append(query.category)
    if query.content_type:
        clauses.append("a.content_type = ?")
        params.append(query.content_type)
    if query.field:
        clauses.append("a.fields LIKE ?")
        params.append(f"%|{query.field}|%")
    return (" AND ".join(clauses) or "1"), params


CARD_COLUMNS = "a.id, a.url, a.title, a.subtitle, a.author, a.date, a.journal, a.region, a.annotated, a.annotation"


def card(row: sqlite3.Row) -> dict[str, Any]:
    annotation = json.loads(row["annotation"]) if row["annotation"] else None
    return {
        "id": row["id"],
        "url": row["url"],
        "title": row["title"],
        "subtitle": row["subtitle"],
        "author": row["author"],
        "date": row["date"],
        "journal": row["journal"],
        "region": row["region"],
        "analysis": (
            {
                "category": annotation.get("classification.news_category"),
                "fields": annotation.get("topics.scientific_fields") or [],
                "summary": annotation.get("summary.one_sentence"),
            }
            if annotation
            else None
        ),
    }


class ArticleIndex:
    """Read-only access to the bundle; one SQLite connection per thread."""

    def __init__(
        self,
        database: Path,
        *,
        semantic: Callable[[list[float], int, SearchFilters], list[str]] | None = None,
        encoder: QueryEncoder | None = None,
    ) -> None:
        self.database = database
        self.semantic = semantic
        self.encoder = encoder
        self._local = threading.local()

    def connection(self) -> sqlite3.Connection:
        # Publication replaces the file; reopen so readers see the new bundle.
        stamp = self.database.stat().st_mtime_ns
        connection = getattr(self._local, "connection", None)
        if connection is None or getattr(self._local, "stamp", None) != stamp:
            if connection is not None:
                connection.close()
            connection = sqlite3.connect(
                f"file:{self.database}?mode=ro", uri=True, check_same_thread=False
            )
            connection.row_factory = sqlite3.Row
            self._local.connection = connection
            self._local.stamp = stamp
        return connection

    def meta(self, key: str) -> Any:
        row = self.connection().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def article(self, identifier: str) -> dict[str, Any] | None:
        row = self.connection().execute(
            "SELECT * FROM articles WHERE id = ?", (identifier,)
        ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["annotation"] = json.loads(record["annotation"]) if record["annotation"] else None
        return record

    def latest(self, query: ArticleQuery, *, offset: int, limit: int) -> SearchPage:
        where, params = filter_sql(query)
        connection = self.connection()
        total = connection.execute(f"SELECT count(*) FROM articles AS a WHERE {where}", params).fetchone()[0]
        rows = connection.execute(
            f"SELECT {CARD_COLUMNS} FROM articles AS a WHERE {where} "
            "ORDER BY a.date IS NULL, a.date DESC, a.rowid LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return SearchPage(total=total, results=[card(row) for row in rows], mode="latest")

    def lexical(self, query: ArticleQuery) -> list[str]:
        match = fts_query(query.text)
        if not match:
            return []
        where, params = filter_sql(query)
        weights = ", ".join(map(str, BM25_WEIGHTS))
        rows = self.connection().execute(
            f"SELECT a.id FROM articles_fts JOIN articles AS a ON a.rowid = articles_fts.rowid "
            f"WHERE articles_fts MATCH ? AND {where} "
            f"ORDER BY bm25(articles_fts, {weights}) LIMIT ?",
            [match, *params, CANDIDATES],
        ).fetchall()
        return [row["id"] for row in rows]

    def search(
        self, query: ArticleQuery, *, offset: int, limit: int, semantic_enabled: bool = True
    ) -> SearchPage:
        started = time.perf_counter()
        lexical = self.lexical(query)
        timings = {"lexical": round((time.perf_counter() - started) * 1000, 1)}
        semantic: list[str] = []
        notes: list[str] = []
        if not semantic_enabled:
            notes.append("lexical only (requested)")
        elif self.semantic is not None and self.encoder is not None:
            try:
                stage = time.perf_counter()
                vector = self.encoder.encode_query(query.text)
                timings["embedding"] = round((time.perf_counter() - stage) * 1000, 1)
                stage = time.perf_counter()
                semantic = self.semantic(
                    vector,
                    CANDIDATES,
                    SearchFilters(
                        journals=(query.university.upper(),) if query.university else (),
                        regions=(query.region,) if query.region else (),
                        start_date=query.start,
                        end_date=query.end,
                    ),
                )
                timings["ann"] = round((time.perf_counter() - stage) * 1000, 1)
            except Exception as exc:  # noqa: BLE001 - degrade to lexical, never fail
                notes.append(f"semantic search unavailable: {type(exc).__name__}")
        else:
            notes.append("semantic search not loaded")
        if semantic and query.uses_analysis:
            # The vector index has no annotation columns; filter its hits here.
            semantic = self._keep_matching(semantic, query)
        scores = rrf_fuse(semantic, lexical, k=RRF_K)
        ranked = sorted(scores, key=scores.get, reverse=True)
        page_ids = ranked[offset : offset + limit]
        rows = {}
        if page_ids:
            marks = ",".join("?" * len(page_ids))
            rows = {
                row["id"]: row
                for row in self.connection().execute(
                    f"SELECT {CARD_COLUMNS} FROM articles AS a WHERE a.id IN ({marks})", page_ids
                )
            }
        return SearchPage(
            total=len(ranked),
            results=[card(rows[identifier]) for identifier in page_ids if identifier in rows],
            mode="hybrid" if semantic else "lexical",
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            notes=notes,
            timings=timings,
        )

    def _keep_matching(self, identifiers: list[str], query: ArticleQuery) -> list[str]:
        where, params = filter_sql(query)
        marks = ",".join("?" * len(identifiers))
        allowed = {
            row["id"]
            for row in self.connection().execute(
                f"SELECT a.id FROM articles AS a WHERE a.id IN ({marks}) AND {where}",
                [*identifiers, *params],
            )
        }
        return [identifier for identifier in identifiers if identifier in allowed]


def lancedb_semantic(table: Any) -> Callable[[list[float], int, SearchFilters], list[str]]:
    """ANN search with the metadata filters pushed into LanceDB as a prefilter."""

    def search(vector: list[float], limit: int, filters: SearchFilters) -> list[str]:
        request = table.search(vector, vector_column_name="vector").distance_type("cosine")
        condition = lance_filter(filters)
        if condition:
            request = request.where(condition, prefilter=True)
        response = request.select(["article_id"]).limit(limit).to_arrow()
        return [str(value) for value in response.column("article_id").to_pylist()]

    return search
