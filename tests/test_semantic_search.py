"""Real LanceDB checks with a small deterministic embedding fixture."""

import time
import math

import pytest

from university_news.search.diagnostics import check_search
from university_news.search.hybrid import SearchFilters
from university_news.search.semantic import SemanticHealth, SemanticRuntime, validate_vector
from university_news.search.vector_index import (
    connect_database, embedding_contract, ensure_indexes, index_records, lance_schema,
)


class Encoder:
    dimension = 3
    context_length = 1024
    model_repo = "test/model"
    model_file = "test.gguf"
    model_path = None
    model_sha256 = "test-fingerprint"

    def encode_query(self, _text):
        return [1.0, 0.0, 0.0]


@pytest.fixture()
def runtime(tmp_path):
    table = connect_database(tmp_path / "lance").create_table("articles", schema=lance_schema(3))
    records = [
        {"article_id": "first", "content_hash": "a", "journal": "USP", "region": "Sudeste",
         "date": "2026-11-01", "truncated": False, "vector": [1.0, 0.0, 0.0]},
        {"article_id": "second", "content_hash": "b", "journal": "UFAM", "region": "Norte",
         "date": "2026-10-01", "truncated": False, "vector": [0.0, 1.0, 0.0]},
    ]
    index_records(table, records)
    return SemanticRuntime(table, Encoder(), metadata=embedding_contract(Encoder(), "articles"),
                           table_name="articles", document_context_length=1024)


@pytest.mark.parametrize("field,value", [
    ("dimension", 9), ("model_file", "another.gguf"), ("model_sha256", "wrong"),
    ("query_instruction", "wrong"), ("distance", "l2"), ("table", "another"),
])
def test_incompatible_index_is_rejected_even_if_dimensions_match(runtime, field, value):
    metadata = dict(runtime.metadata, **{field: value})
    with pytest.raises(ValueError, match=field):
        SemanticRuntime(runtime.table, Encoder(), metadata=metadata,
                        table_name="articles", document_context_length=1024)


@pytest.mark.parametrize("vector", [[0.0, 0.0, 0.0], [1.0], [float("nan"), 1, 0]])
def test_invalid_query_embeddings_fail_explicitly(vector):
    with pytest.raises(ValueError):
        validate_vector(vector, 3)


def test_probe_executes_lancedb_and_refuses_orphan_hits(runtime):
    report = runtime.probe(resolve=lambda ids: {key: {} for key in ids})
    assert report["ready"] and report["hits"] == 2
    assert report["retrieval"] == "flat" and report["efficient"]
    with pytest.raises(ValueError, match="published_articles"):
        runtime.probe(resolve=lambda ids: {})


def test_fresh_empty_database_is_awaiting_data_not_healthy(runtime):
    runtime.table.delete("true")
    assert runtime.probe()["state"] == "awaiting_data"
    assert not runtime.probe()["ready"]


def test_vector_filters_are_applied_in_real_lancedb(runtime):
    assert runtime.search([1.0, 0.0, 0.0], 10, SearchFilters(regions=("Norte",))) == ["second"]


def test_api_reader_sees_vectors_committed_by_another_connection(runtime, tmp_path):
    path = tmp_path / "lance"
    another = connect_database(path).open_table("articles")
    another.add([{"article_id": "new", "content_hash": "c", "journal": "USP", "region": "Sudeste",
                  "date": "2026-11-02", "truncated": False, "vector": [0.0, 0.0, 1.0]}])
    assert runtime.search([0.0, 0.0, 1.0], 1, SearchFilters()) == ["new"]


def test_diagnostic_checks_exact_recall_concurrency_and_gold_ids(runtime):
    report = check_search(runtime, "pesquisa", top_k=1, repeat=4, concurrency=2,
                          expected_ids=["first"], resolve=lambda ids: ids)
    assert report["ok"] and report["minimum_recall_at_k"] == 1
    assert report["reference_found"] and report["query_cache"] is False
    assert report["p95_ms"] >= report["p50_ms"]
    report = check_search(runtime, "pesquisa", top_k=1, expected_ids=["second"])
    assert not report["ok"] and not report["reference_found"]


def test_health_expires_and_request_failures_mark_it_unhealthy():
    health = SemanticHealth(enabled=True, max_age=0.001)
    health.record(ready=True, state="ready")
    time.sleep(0.003)
    assert health.snapshot()["state"] == "stale"
    health.failure(RuntimeError("unavailable"))
    assert not health.snapshot()["ready"]


def test_monitor_retries_failed_probes_and_only_installs_verified_backend(monkeypatch):
    import threading
    from types import SimpleNamespace

    from university_news.site.api import monitor_semantic_search

    health = SemanticHealth(enabled=True)
    observed = []
    outcomes = ["awaiting_data", "error", "ready"]
    index = SimpleNamespace(encoder=None, semantic=None, _matching_rows=lambda ids, query: ids,
                            published_count=lambda: 10)

    class Runtime:
        encoder = Encoder()

        def probe(self, **kwargs):
            state = outcomes.pop(0)
            if state == "error":
                raise RuntimeError("temporary storage failure")
            return {"ready": state == "ready", "state": state, "efficient": True, "rows": 2}

        def search(self, *args):
            return ["first"]

    class Stop(threading.Event):
        def wait(self, timeout=None):
            observed.append(health.snapshot()["state"])
            if len(observed) == 3:
                self.set()

    monkeypatch.setattr(SemanticRuntime, "load", classmethod(lambda cls, settings: Runtime()))
    monitor_semantic_search({"health_interval_seconds": 1}, index, health, Stop())
    assert observed == ["awaiting_data", "error", "ready"]
    assert index.encoder is not None and index.semantic is not None
    assert health.snapshot()["coverage_estimate"] == 0.2


def test_real_ann_index_reports_coverage_and_matches_exact_top_results(runtime):
    records = [
        {"article_id": f"v-{n}", "content_hash": str(n), "journal": "USP", "region": "Sudeste",
         "date": "2026-11-01", "truncated": False,
         "vector": [math.cos(n * 0.1), math.sin(n * 0.1), 0.2]}
        for n in range(300)
    ]
    runtime.table.add(records)
    ensure_indexes(runtime.table, rebuild=False)
    report = check_search(runtime, "pesquisa", top_k=10, repeat=1)
    assert report["probe"]["ann_ready"]
    assert report["probe"]["indexed_rows"] == 302
    assert report["minimum_recall_at_k"] >= 0.9
