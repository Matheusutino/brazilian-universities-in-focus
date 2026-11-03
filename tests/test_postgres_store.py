"""Integration tests against a real PostgreSQL (set TEST_DATABASE_URL to run).

The database is wiped before each test, so never point this at real data.
"""

import json
import os
import uuid

import pandas as pd
import pytest

from university_news.operations.database_schema import BASE_SCHEMA_SQL
from university_news.operations.enrichment_export import export_enrichments
from university_news.operations.store import PostgresStore
from university_news.operations.worker import work

DSN = os.getenv("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not set")


def profile(identifier: str) -> dict:
    return {
        "id": identifier,
        "provider": "nvidia_nim",
        "model": "test/model",
        "schema_version": "2.1.0",
        "prompt_sha256": "abc",
        "settings": {"temperature": 0.2},
    }


def annotation(url: str, profile_id: str) -> dict:
    return {
        "url": url,
        "university": "USP",
        "model": "test/model",
        "schema_version": "2.1.0",
        "content_hash": "hash",
        "enriched_at": "2026-11-02T12:00:00+00:00",
        "analysis": {
            "schema_version": "2.1.0",
            "document": {"title": "Notícia", "publication_date": "2026-11-01"},
            "summary": {"one_sentence": f"Resumo {profile_id}", "abstract": "Texto"},
            "entities": [{"name": "USP", "type": "university"}],
        },
        "generation_metrics": {
            "duration_seconds": 10.0,
            "api_requests": 2,
            "validation_retries": 1,
            "prompt_tokens": 1000,
            "completion_tokens": 800,
            "rate_limit_wait_seconds": 1.5,
        },
    }


@pytest.fixture()
def store():
    opened = PostgresStore.open(DSN)
    with opened.connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA public CASCADE")
        cursor.execute("CREATE SCHEMA public")
    yield opened
    opened.close()


def seed_articles(store: PostgresStore, count: int) -> list[uuid.UUID]:
    store.initialize()
    store.register_source("usp", "https://jornal.usp.br/")
    urls = [f"https://jornal.usp.br/news/{number}" for number in range(count)]
    store.bootstrap_urls("usp", urls)
    store.bootstrap_articles(
        "usp", [{"url": url, "title": "Notícia", "text": "Texto"} for url in urls]
    )
    with store.connection.cursor() as cursor:
        cursor.execute("SELECT id FROM articles ORDER BY url")
        return [row["id"] for row in cursor.fetchall()]


def test_migrations_apply_once_and_record_their_versions(store) -> None:
    assert store.initialize() == [1, 2, 3]
    assert store.initialize() == []
    assert store.schema_version() == 3


def test_profiles_keep_exact_prompts_and_contract(store) -> None:
    store.initialize()
    # Registered first without prompts (as before migration 3), then completed.
    store.register_enrichment_profile(profile("first"))
    store.register_enrichment_profile(
        profile("first") | {"prompts": ["Você extrai..."], "contract": {"summary": {}}}
    )

    summary = store.enrichment_profiles()[0]
    full = store.enrichment_profiles(full=True)[0]

    assert "prompts" not in summary
    assert full["prompts"] == ["Você extrai..."]
    assert full["contract"] == {"summary": {}}


def test_migration_converts_legacy_single_profile_enrichments(store) -> None:
    with store.connection.cursor() as cursor:
        cursor.execute(BASE_SCHEMA_SQL)
        cursor.execute("INSERT INTO sources (site, base_url) VALUES ('usp', 'x')")
        article = uuid.uuid4()
        cursor.execute(
            "INSERT INTO discovered_urls (id, site, url, canonical_url) "
            "VALUES (%s, 'usp', 'u', 'u')",
            (article,),
        )
        cursor.execute(
            "INSERT INTO articles (id, site, url, status, payload) "
            "VALUES (%s, 'usp', 'u', 'accepted', '{}')",
            (article,),
        )
        cursor.execute(
            "INSERT INTO enrichments (article_id, provider, model, schema_version, payload) "
            "VALUES (%s, 'openwebui', 'old', '1.0.0', '{}')",
            (article,),
        )

    store.initialize()

    profiles = store.enrichment_profiles()
    assert len(profiles) == 1
    assert profiles[0]["id"].startswith("legacy-")
    assert profiles[0]["model"] == "old"
    assert profiles[0]["annotations"] == 1


def test_backfill_save_and_progress_are_tracked_per_profile(store) -> None:
    articles = seed_articles(store, 3)
    store.register_enrichment_profile(profile("first"))
    store.register_enrichment_profile(profile("second"))

    assert store.enqueue_enrichment_backfill("first", limit=None) == {
        "created": 3,
        "requeued": 0,
    }
    job = store.claim_job(["enrich"], worker_id="test", lease_seconds=60)
    assert job is not None
    run_id = store.start_run("worker:enrich", config={"profile": "first"})
    assert store.save_enrichment(
        job.article_id,
        annotation(job.url, "first"),
        profile_id="first",
        run_id=run_id,
    )
    assert not store.save_enrichment(
        job.article_id, annotation(job.url, "first"), profile_id="first"
    )
    store.complete_job(job)

    progress = store.enrichment_progress("first")
    assert progress["annotated"] == 1
    assert progress["missing"] == 2
    assert progress["enrich_jobs"] == {"completed": 1, "queued": 2}
    assert progress["cost"]["completion_tokens"] == 800
    assert progress["cost"]["rate_limit_wait_seconds"] == 1.5
    assert progress["throughput"]["articles_per_minute"] > 0
    assert store.has_enrichment(job.article_id, "first")
    assert not store.has_enrichment(job.article_id, "second")

    # By default, an article done by any profile is not queued again.
    assert store.enqueue_enrichment_backfill("second", limit=None) == {
        "created": 0,
        "requeued": 0,
    }
    # Reannotation requeues it so the second profile can be compared.
    assert store.enqueue_enrichment_backfill(
        "second", limit=None, reannotate=True
    ) == {"created": 0, "requeued": 1}
    assert set(articles) >= {job.article_id}


def test_overview_counts_each_article_once_across_profiles(store) -> None:
    articles = seed_articles(store, 3)
    store.register_enrichment_profile(profile("nvidia"))
    store.register_enrichment_profile(profile("openwebui"))
    store.save_enrichment(articles[0], annotation("a", "nvidia"), profile_id="nvidia")
    store.save_enrichment(articles[1], annotation("b", "openwebui"), profile_id="openwebui")
    # A comparison annotation of the same article must not inflate coverage.
    store.save_enrichment(articles[1], annotation("b", "nvidia"), profile_id="nvidia")

    overview = store.enrichment_overview(["nvidia", "openwebui"])

    assert overview["annotated_by_any_profile"] == 2
    assert overview["missing"] == 1
    assert [item["annotated"] for item in overview["by_profile"]] == [2, 1]


def test_failures_keep_profile_and_spent_metrics(store) -> None:
    seed_articles(store, 1)
    store.enqueue_enrichment_backfill("first", limit=None)
    store.register_enrichment_profile(profile("first"))
    job = store.claim_job(["enrich"], worker_id="test", lease_seconds=60)
    run_id = store.start_run("worker:enrich")

    store.fail_job(
        job,
        ValueError("invalid output"),
        transient=False,
        max_attempts=3,
        run_id=run_id,
        details={"profile_id": "first", "generation_metrics": {"total_tokens": 99}},
    )

    error = store.recent_errors(stage="enrich")[0]
    assert error["job_status"] == "failed"
    assert error["run_id"] == run_id
    assert error["details"]["generation_metrics"]["total_tokens"] == 99
    assert store.enrichment_progress("first")["errors_last_24h"][0]["errors"] == 1


def test_worker_records_its_run_and_stops_when_requested(store) -> None:
    seed_articles(store, 2)
    store.enqueue_enrichment_backfill("first", limit=None)
    run_id = store.start_run("worker:enrich", config={"stages": ["enrich"]})

    class Handlers:
        def __init__(self, stop):
            self.stop = stop

        def process(self, _job):
            self.stop.set()  # Simulate SIGTERM during the first job.
            return ()

    import threading

    stop = threading.Event()
    stats = work(
        store,
        Handlers(stop),
        stages=["enrich"],
        worker_id="test",
        lease_seconds=60,
        max_attempts=3,
        poll_seconds=0.1,
        heartbeat_seconds=5,
        max_jobs=None,
        exit_when_empty=True,
        run_id=run_id,
        stop=stop,
    )
    store.finish_run(run_id, stats=stats, status="interrupted")

    assert stats["completed"] == 1
    assert store.enrichment_progress("first")["enrich_jobs"] == {
        "completed": 1,
        "queued": 1,
    }
    run = store.operational_status()["recent_runs"][0]
    assert run["status"] == "interrupted"
    assert run["stats"] == {"completed": 1, "failed": 0, "retried": 0}


def test_export_writes_annotations_table_and_checksummed_manifest(
    store, tmp_path
) -> None:
    seed_articles(store, 2)
    store.register_enrichment_profile(profile("first"))
    with store.connection.cursor() as cursor:
        cursor.execute("SELECT id, url FROM articles ORDER BY url")
        rows = cursor.fetchall()
    for row in rows:
        store.save_enrichment(
            row["id"], annotation(row["url"], "first"), profile_id="first"
        )

    result = export_enrichments(
        store.iter_enrichments(["first"]),
        profiles=[profile("first")],
        directory=tmp_path / "first",
    )

    assert result["annotations"] == 2
    lines = (tmp_path / "first" / "annotations.jsonl").read_text(encoding="utf-8")
    records = [json.loads(line) for line in lines.splitlines()]
    assert [record["article_id"] for record in records] == [str(row["id"]) for row in rows]
    table = pd.read_parquet(tmp_path / "first" / "annotations.parquet")
    assert list(table["summary.one_sentence"]) == ["Resumo first", "Resumo first"]
    assert json.loads(table.loc[0, "entities"])[0]["name"] == "USP"
    manifest = json.loads((tmp_path / "first" / "manifest.json").read_text())
    assert manifest["annotations"] == 2
    assert manifest["profiles"][0]["id"] == "first"
    assert set(table["profile_id"]) == {"first"}
    assert len(manifest["files"]["annotations.jsonl"]["sha256"]) == 64
