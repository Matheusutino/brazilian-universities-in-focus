"""Executable checks for the vector search contract and actual retrieval."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .embeddings import QUERY_INSTRUCTION, QwenGGUFEmbedder
from .hybrid import SearchFilters, lance_filter
from .vector_index import ANN_MIN_ROWS, connect_database, load_index_metadata

PROBE_QUERY = "pesquisas e descobertas científicas nas universidades brasileiras"


def validate_vector(vector: list[float], dimension: int) -> None:
    if len(vector) != dimension or not all(math.isfinite(value) for value in vector):
        raise ValueError("invalid_query_vector")
    if not any(value != 0 for value in vector):
        raise ValueError("zero_query_vector")


class SemanticHealth:
    """Shared status; a loaded model alone never marks retrieval ready."""

    def __init__(self, *, enabled: bool, max_age: float = 150) -> None:
        self.enabled = enabled
        self.max_age = max_age
        self._lock = threading.Lock()
        self._checked = 0.0
        self._status: dict[str, Any] = {
            "state": "loading" if enabled else "disabled", "ready": False,
            "checked_at": None, "last_success_at": None,
        }

    def record(self, *, ready: bool, state: str, **details: Any) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._checked = time.monotonic()
            last_success = now if ready else self._status.get("last_success_at")
            self._status = {
                **details, "state": state, "ready": ready,
                "checked_at": now, "last_success_at": last_success,
            }

    def failure(self, error: Exception) -> None:
        self.record(ready=False, state="error", error_type=type(error).__name__)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            status = dict(self._status)
            if status["ready"] and time.monotonic() - self._checked > self.max_age:
                status.update(ready=False, state="stale")
            return {"enabled": self.enabled, **status}


class SemanticRuntime:
    """Read-only retrieval with explicit compatibility and health checks."""

    def __init__(
        self, table: Any, encoder: QwenGGUFEmbedder, *,
        metadata: dict[str, Any], table_name: str, document_context_length: int,
    ) -> None:
        self.table = table
        self.encoder = encoder
        self.metadata = metadata
        self.metadata_path: Path | None = None
        expected = {
            "table": table_name, "model_repo": encoder.model_repo,
            "model_file": encoder.model_file, "dimension": encoder.dimension,
            "context_length": document_context_length,
            "distance": "cosine", "normalized": False,
            "query_instruction": QUERY_INSTRUCTION, "pooling": "last",
            "format_version": 1,
            "model_sha256": getattr(encoder, "model_sha256", None),
        }
        mismatches = [key for key, value in expected.items() if metadata.get(key) != value]
        if mismatches:
            raise ValueError("incompatible_vector_index: " + ", ".join(mismatches))
        dimension = getattr(table.schema.field("vector").type, "list_size", None)
        if dimension != encoder.dimension:
            raise ValueError("incompatible_vector_dimension")

    @classmethod
    def load(cls, settings: dict[str, Any]) -> SemanticRuntime:
        path = Path(settings["lancedb_path"])
        if not path.is_dir():
            raise FileNotFoundError("vector_database_missing")
        metadata = load_index_metadata(path)
        if not metadata:
            raise ValueError("vector_contract_missing")
        table = connect_database(path).open_table(settings["lancedb_table"])
        model_path = settings.get("model_path")
        encoder = QwenGGUFEmbedder(
            model_path=Path(model_path) if model_path else None,
            model_repo=settings["model_repo"], model_file=settings["model_file"],
            context_length=int(settings.get("query_context_length", 1024)),
            gpu_layers=int(settings.get("gpu_layers", 0)),
            threads=int(settings.get("threads", 2)),
        )
        runtime = cls(table, encoder, metadata=metadata,
                      table_name=settings["lancedb_table"],
                      document_context_length=int(settings["document_context_length"]))
        runtime.metadata_path = path
        return runtime

    def search_rows(
        self, vector: list[float], limit: int, filters: SearchFilters, *, exact: bool = False
    ) -> list[dict[str, Any]]:
        validate_vector(vector, self.encoder.dimension)
        request = self.table.search(vector, vector_column_name="vector").distance_type("cosine")
        if exact:
            request = request.bypass_vector_index()
        else:
            # Re-score a wider shortlist using the original vectors, reducing
            # ranking loss from the scalar-quantized HNSW index.
            request = request.refine_factor(4)
        condition = lance_filter(filters)
        if condition:
            request = request.where(condition, prefilter=True)
        rows = request.select(["article_id", "_distance"]).limit(limit).to_arrow().to_pylist()
        for row in rows:
            if not math.isfinite(row["_distance"]):
                raise ValueError("invalid_vector_distance")
        return rows

    def search(self, vector: list[float], limit: int, filters: SearchFilters) -> list[str]:
        return [str(row["article_id"]) for row in self.search_rows(vector, limit, filters)]

    def index_statistics(self) -> dict[str, Any]:
        rows = self.table.count_rows()
        indexes = [index for index in self.table.list_indices() if "vector" in index.columns]
        if not indexes:
            return {"rows": rows, "ann_ready": False, "indexed_rows": 0,
                    "unindexed_rows": rows, "retrieval": "flat"}
        stats = self.table.index_stats(indexes[0].name)
        def value(name, default=None):
            return stats.get(name, default) if isinstance(stats, dict) else getattr(stats, name, default)

        metric = value("distance_type")
        if metric and metric.lower() != "cosine":
            raise ValueError("incompatible_ann_distance")
        indexed = value("num_indexed_rows", 0)
        return {"rows": rows, "ann_ready": bool(indexed), "indexed_rows": indexed,
                "unindexed_rows": value("num_unindexed_rows"),
                "retrieval": "ann" if indexed else "flat"}

    def probe(
        self, *, resolve: Callable[[list[str]], Any] | None = None,
        query: str = PROBE_QUERY,
    ) -> dict[str, Any]:
        if self.metadata_path is not None:
            current = load_index_metadata(self.metadata_path)
            if any(current.get(key) != value for key, value in self.metadata.items() if key != "model_path"):
                raise ValueError("vector_contract_changed")
        stats = self.index_statistics()
        if not stats["rows"]:
            return {**stats, "ready": False, "state": "awaiting_data"}
        started = time.perf_counter()
        vector = self.encoder.encode_query(query)
        embedded_at = time.perf_counter()
        hits = self.search(vector, 5, SearchFilters())
        searched_at = time.perf_counter()
        if not hits:
            raise ValueError("empty_vector_probe")
        if resolve is not None and not resolve(hits):
            raise ValueError("vector_probe_has_no_published_articles")
        return {
            **stats, "ready": True, "state": "ready",
            "efficient": stats["ann_ready"] or stats["rows"] < ANN_MIN_ROWS,
            "dimension": self.encoder.dimension, "hits": len(hits),
            "embedding_ms": round((embedded_at - started) * 1000, 1),
            "ann_ms": round((searched_at - embedded_at) * 1000, 1),
        }
