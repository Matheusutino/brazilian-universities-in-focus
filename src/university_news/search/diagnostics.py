"""Read-only vector diagnostics: actual model, ANN, exact recall and timings."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .hybrid import SearchFilters
from .semantic import SemanticRuntime


def check_search(
    runtime: SemanticRuntime, query: str, *, top_k: int = 10, repeat: int = 3,
    concurrency: int = 1, minimum_recall: float = 0.9,
    expected_ids: list[str] | None = None,
    resolve: Callable[[list[str]], Any] | None = None,
) -> dict[str, Any]:
    if top_k < 1 or repeat < 1 or concurrency < 1 or not 0 <= minimum_recall <= 1:
        raise ValueError("Invalid diagnostic limits")
    probe = runtime.probe(resolve=resolve, query=query)
    if not probe["ready"]:
        return {"ok": False, "probe": probe, "reason": probe["state"]}

    vector = runtime.encoder.encode_query(query)
    exact_started = time.perf_counter()
    exact = runtime.search_rows(vector, top_k, SearchFilters(), exact=True)
    exact_ms = round((time.perf_counter() - exact_started) * 1000, 1)
    exact_ids = {str(row["article_id"]) for row in exact}

    def run(_number):
        started = time.perf_counter()
        embedded = runtime.encoder.encode_query(query)  # Deliberately bypass query cache.
        encoded = time.perf_counter()
        rows = runtime.search_rows(embedded, top_k, SearchFilters())
        searched = time.perf_counter()
        ids = [str(row["article_id"]) for row in rows]
        matched = set(resolve(ids)) if resolve is not None else set(ids)
        return {
            "embedding_ms": round((encoded - started) * 1000, 1),
            "ann_ms": round((searched - encoded) * 1000, 1),
            "total_ms": round((time.perf_counter() - started) * 1000, 1),
            "ids": ids, "published_ids": sorted(matched),
            "recall_at_k": len(set(ids) & exact_ids) / len(exact_ids) if exact_ids else 0,
        }

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        samples = list(executor.map(run, range(repeat)))
    totals = sorted(sample["total_ms"] for sample in samples)
    reference = set(expected_ids or [])
    reference_found = all(reference <= set(sample["ids"]) for sample in samples)
    min_recall = min(sample["recall_at_k"] for sample in samples)
    resolves = all(set(sample["ids"]) == set(sample["published_ids"]) for sample in samples)
    return {
        "ok": bool(exact_ids) and min_recall >= minimum_recall and reference_found and resolves
              and probe["efficient"],
        "query": query, "top_k": top_k, "repeat": repeat, "concurrency": concurrency,
        "query_cache": False, "probe": probe, "exact_search_ms": exact_ms,
        "minimum_recall_at_k": min_recall, "minimum_required_recall": minimum_recall,
        "reference_checked": bool(reference),
        "reference_found": reference_found if reference else None,
        "all_results_published": resolves,
        "p50_ms": totals[math.ceil(len(totals) * 0.5) - 1],
        "p95_ms": totals[math.ceil(len(totals) * 0.95) - 1], "samples": samples,
    }
