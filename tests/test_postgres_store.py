"""Integration tests against a real PostgreSQL (set TEST_DATABASE_URL to run).

The database is wiped before each test, so never point this at real data.
"""

import json
import os
import uuid

import pandas as pd
import pytest

from university_news.operations.database_schema import BASE_SCHEMA_SQL
from university_news.operations.database_schema import MIGRATIONS
from university_news.operations.enrichment_export import export_enrichments
from university_news.operations.store import LeaseLostError, PostgresStore
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
    assert store.initialize() == [1, 2, 3, 4, 5, 6]
    assert store.initialize() == []
    assert store.schema_version() == 6


def test_lease_migration_preserves_existing_jobs_and_reclaims_expired_claims(store):
    store.connection.execute(
        "CREATE TABLE schema_migrations (version integer PRIMARY KEY, description text)"
    )
    for version, description, sql in MIGRATIONS[:5]:
        store.connection.execute(sql)
        store.connection.execute(
            "INSERT INTO schema_migrations VALUES (%s, %s)", (version, description)
        )
    store.register_source("usp", "https://jornal.usp.br/")
    store.add_discovered_urls("usp", ["https://jornal.usp.br/old"], run_id=None, page=1)
    store.connection.execute(
        "UPDATE processing_jobs SET status = 'running', worker_id = 'old', "
        "lease_expires_at = clock_timestamp() - interval '1 second'"
    )
    assert store.initialize() == [6]
    job = store.claim_job(["fetch"], worker_id="new", lease_seconds=60)
    assert job.worker_id == "new" and job.lease_token is not None
    assert store.complete_job(job, ["extract"])


def test_stale_attempt_cannot_renew_write_fail_or_complete_even_with_same_worker_id(
    store, tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from university_news.operations import handlers as handlers_module

    identifier = seed_articles(store, 1)[0]
    assert store.enqueue_embedding_backfill(limit=None) == 1
    stale = store.claim_job(["embed"], worker_id="same", lease_seconds=60)
    store.connection.execute(
        "UPDATE processing_jobs SET lease_expires_at = clock_timestamp() - interval '1 second' "
        "WHERE id = %s", (stale.id,),
    )
    assert not store.renew_lease(stale, 60)
    current = store.claim_job(["embed"], worker_id="same", lease_seconds=60)
    assert current.lease_token != stale.lease_token
    assert current.attempts == stale.attempts + 1
    assert not store.renew_lease(stale, 60)
    assert not store.complete_job(stale, ["enrich"])
    assert not store.fail_job(stale, ValueError("stale"), transient=False, max_attempts=3)
    with pytest.raises(LeaseLostError):
        with store.lease_guard(stale):
            store.save_article(identifier, site="usp", url=stale.url,
                               status="rejected", payload={})
    assert store.get_article(identifier)["title"] == "Notícia"
    assert store.recent_errors() == []
    assert store.connection.execute(
        "SELECT count(*) AS n FROM processing_jobs WHERE stage = 'enrich'"
    ).fetchone()["n"] == 0

    calls = []
    handler = handlers_module.StageHandlers(store, tmp_path)
    handler._embedder = SimpleNamespace(
        encode_documents=lambda docs: SimpleNamespace(vectors=[[1.0]], truncated=[False]),
        model_repo="repo", model_file="model",
    )
    handler._lance_table = object()
    monkeypatch.setattr(handlers_module, "index_records", lambda *_args: calls.append(True))
    with pytest.raises(LeaseLostError):
        handler.embed(stale)
    assert calls == []
    assert store.complete_job(current)


def test_lease_guard_blocks_reclaim_until_fenced_side_effect_finishes(store):
    seed_articles(store, 1)
    store.enqueue_embedding_backfill(limit=None)
    job = store.claim_job(["embed"], worker_id="first", lease_seconds=1)
    other = PostgresStore.open(DSN)
    try:
        with store.lease_guard(job):
            # Let the committed lease expire while a fenced write holds its row lock.
            store.connection.execute("SELECT pg_sleep(1.1)")
            assert other.claim_job(["embed"], worker_id="second", lease_seconds=60) is None
        claimed = other.claim_job(["embed"], worker_id="second", lease_seconds=60)
        assert claimed is not None and claimed.lease_token != job.lease_token
    finally:
        other.close()


def test_heartbeat_waiting_for_row_lock_cannot_revive_expired_lease(store):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    seed_articles(store, 1)
    store.enqueue_embedding_backfill(limit=None)
    job = store.claim_job(["embed"], worker_id="first", lease_seconds=1)
    heartbeat = PostgresStore.open(DSN)
    started = threading.Event()
    def renew():
        started.set()
        return heartbeat.renew_lease(job, 60)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with store.lease_guard(job):
                result = executor.submit(renew)
                assert started.wait(1)
                store.connection.execute("SELECT pg_sleep(1.1)")
            assert result.result(timeout=5) is False
    finally:
        heartbeat.close()


def test_raw_document_round_trip_updates_and_rejects_empty_content(store):
    import hashlib

    identifier = seed_articles(store, 1)[0]
    with pytest.raises(KeyError, match="fetch again"):
        store.get_raw_document(identifier)
    content = "<html>Educação</html>".encode("iso-8859-1")
    store.save_raw_document(
        identifier, content=content, status_code=200, encoding="iso-8859-1",
        content_type="text/html; charset=iso-8859-1", final_url="https://jornal.usp.br/new",
    )
    row = store.get_raw_document(identifier)
    assert bytes(row["content"]) == content
    assert row["sha256"] == hashlib.sha256(content).hexdigest()
    assert row["encoding"] == "iso-8859-1"
    assert row["status_code"] == 200
    assert row["content_type"] == "text/html; charset=iso-8859-1"
    assert row["final_url"] == "https://jornal.usp.br/new"
    assert row["fetched_at"] is not None
    replacement = b"<html>Updated</html>"
    store.save_raw_document(identifier, content=replacement, status_code=200)
    assert bytes(store.get_raw_document(identifier)["content"]) == replacement
    with pytest.raises(ValueError, match="must not be empty"):
        store.save_raw_document(identifier, content=b"", status_code=200)
    assert bytes(store.get_raw_document(identifier)["content"]) == replacement
    assert store.connection.execute("SELECT count(*) AS n FROM raw_documents").fetchone()["n"] == 1


def test_raw_content_migration_preserves_legacy_paths_without_filesystem_import(store):
    store.connection.execute(
        "CREATE TABLE schema_migrations (version integer PRIMARY KEY, description text)"
    )
    for version, description, sql in MIGRATIONS[:4]:
        store.connection.execute(sql)
        store.connection.execute(
            "INSERT INTO schema_migrations VALUES (%s, %s)", (version, description)
        )
    store.register_source("usp", "https://jornal.usp.br/")
    ids = store.add_discovered_urls("usp", ["https://jornal.usp.br/legacy"], run_id=None, page=1)
    identifier = ids[0]
    store.connection.execute(
        "INSERT INTO raw_documents (article_id, path, sha256) VALUES (%s, %s, %s)",
        (identifier, "sites/usp/raw/legacy.html", "legacy-hash"),
    )
    assert store.initialize() == [5, 6]
    row = store.connection.execute("SELECT * FROM raw_documents").fetchone()
    assert row["path"] == "sites/usp/raw/legacy.html"
    assert row["sha256"] == "legacy-hash"
    assert row["content"] is None
    with pytest.raises(KeyError, match="fetch again"):
        store.get_raw_document(identifier)
    store.save_raw_document(identifier, content=b"<html>New</html>", status_code=200)
    row = store.connection.execute("SELECT * FROM raw_documents").fetchone()
    assert row["path"] is None
    assert bytes(row["content"]) == b"<html>New</html>"


def test_production_stages_persist_directly_to_postgres_without_article_json(
    store, tmp_path, monkeypatch
) -> None:
    from types import SimpleNamespace

    import httpx

    from university_news.operations import handlers as handlers_module
    from university_news.pipeline.fetching import ContentFetcher

    store.initialize()
    store.register_source("usp", "https://jornal.usp.br/")
    url = "https://jornal.usp.br/ciencias/noticia-nova/"
    identifiers = store.add_discovered_urls("usp", [url], run_id=None, page=1)
    identifier = identifiers[0]
    store.register_enrichment_profile(profile("direct"))
    html = """<html><head>
      <title>Pesquisa universitária apresenta resultados científicos</title>
      <meta name="author" content="Ana da Silva">
      <meta property="article:published_time" content="2026-11-04T08:00:00">
      </head><body><main id="main"><article>
      <h1>Pesquisa universitária apresenta resultados científicos</h1>
      <p>Uma equipe de pesquisadores da universidade apresentou resultados de um
      estudo científico sobre saúde pública. O projeto investiga novas tecnologias
      para melhorar a prevenção de doenças e o atendimento à população brasileira.</p>
      <p>Os cientistas realizaram experimentos com diferentes métodos de análise.
      Os resultados foram discutidos em um encontro de pesquisadores e serão
      publicados para ampliar o acesso ao conhecimento produzido na instituição.</p>
      </article></main></body></html>"""
    content = html.encode("iso-8859-1")
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, content=content,
        headers={"Content-Type": "text/html; charset=iso-8859-1"},
    ))
    class LocalFetcher(ContentFetcher):
        def __init__(self, site, **kwargs):
            super().__init__(site, transport=transport, **kwargs)

    monkeypatch.setattr(handlers_module, "ContentFetcher", LocalFetcher)
    enricher = SimpleNamespace(
        profile=SimpleNamespace(id="direct"),
        enrich=lambda article, **kwargs: annotation(article["url"], "direct"),
    )
    handlers = handlers_module.StageHandlers(store, tmp_path, enricher=enricher)
    handlers._detector = SimpleNamespace(is_portuguese=lambda text: True)
    completed = []
    while job := store.claim_job(
        ["fetch", "extract", "preprocess", "enrich"], worker_id="direct", lease_seconds=60
    ):
        if job.stage == "extract":
            # Another worker, with no shared directory or in-memory response.
            extractor_worker = handlers_module.StageHandlers(store, tmp_path / "other-worker")
            next_stages = extractor_worker.process(job)
        else:
            next_stages = handlers.process(job)
        completed.append(job.stage)
        store.complete_job(job, next_stages)

    assert completed == ["fetch", "extract", "preprocess", "enrich"]
    assert store.get_article(identifier)["author"] == "Ana da Silva"
    assert "científicos" in store.get_article(identifier)["title"]
    projected = store.connection.execute("SELECT * FROM site_documents").fetchone()
    assert projected["summary"] == "Resumo direct"
    assert projected["annotation"]["profile_id"] == "direct"
    assert store.connection.execute("SELECT count(*) AS n FROM raw_documents").fetchone()["n"] == 1
    raw = store.get_raw_document(identifier)
    assert bytes(raw["content"]) == content
    assert raw["encoding"] == "iso-8859-1"
    assert raw["final_url"] == url
    assert list(tmp_path.rglob("*.html")) == []
    assert list(tmp_path.rglob("*.json")) == []
    assert list(tmp_path.rglob("*.jsonl")) == []


def test_site_migration_backfills_existing_articles_and_annotations(store) -> None:
    # Simulate an already deployed v3 database, not only an empty fresh install.
    store.connection.execute(
        "CREATE TABLE schema_migrations (version integer PRIMARY KEY, description text)"
    )
    for version, description, sql in MIGRATIONS[:3]:
        store.connection.execute(sql)
        store.connection.execute(
            "INSERT INTO schema_migrations VALUES (%s, %s)", (version, description)
        )
    store.register_source("usp", "https://jornal.usp.br/")
    url = "https://jornal.usp.br/historica"
    store.bootstrap_articles("usp", [{"url": url, "title": "Notícia histórica", "date": "2026-02-30"}])
    store.register_enrichment_profile(profile("existing"))
    from university_news.operations.identity import stable_article_id

    identifier = stable_article_id(url)
    store.save_enrichment(identifier, annotation(url, "existing"), profile_id="existing")

    assert store.initialize() == [4, 5, 6]
    row = store.connection.execute("SELECT * FROM site_documents").fetchone()
    assert row["id"] == identifier
    assert row["title"] == "Notícia histórica"
    assert row["date"] is None
    assert row["summary"] == "Resumo existing"
    assert row["annotation"]["profile_id"] == "existing"


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
