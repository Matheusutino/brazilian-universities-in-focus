import asyncio
import threading
from types import SimpleNamespace

from university_news.enrichment.workload import enrichment_workload
from university_news.operations import daily
from university_news.operations import store as store_module
from university_news.operations.database_schema import BASE_SCHEMA_SQL, MIGRATIONS
from university_news.operations.store import (
    Job,
    canonicalize_url,
    stable_article_id,
)
from university_news.operations.worker import LeaseHeartbeat, work
from university_news.sources.catalog import SiteConfig


def test_migrations_are_numbered_consecutively() -> None:
    assert [version for version, _description, _sql in MIGRATIONS] == list(
        range(1, len(MIGRATIONS) + 1)
    )


def test_operational_schema_never_uses_pipeline_version_for_jobs() -> None:
    normalized = " ".join(BASE_SCHEMA_SQL.lower().split())

    assert "unique (article_id, stage)" in normalized
    assert "pipeline_version" not in normalized


def test_article_identity_is_stable_by_canonical_url() -> None:
    with_fragment = "HTTPS://Example.edu/news/item#section"
    canonical = "https://example.edu/news/item"

    assert canonicalize_url(with_fragment) == canonical
    assert stable_article_id(with_fragment) == stable_article_id(canonical)


def test_postgres_connection_uses_autocommit_for_session_locks(monkeypatch) -> None:
    sentinel = object()
    captured = {}

    def fake_connect(dsn, **kwargs):
        captured.update({"dsn": dsn, **kwargs})
        return sentinel

    monkeypatch.setattr(store_module, "connect", fake_connect)

    store = store_module.PostgresStore.open("postgresql://test")

    assert store.connection is sentinel
    assert captured["dsn"] == "postgresql://test"
    assert captured["autocommit"] is True


def test_daily_discovery_stops_after_consecutive_pages_without_new_urls(
    monkeypatch,
) -> None:
    site = SiteConfig(
        name="test",
        base_url="https://example.edu/",
        listing_url="https://example.edu/news/",
        paginated_url="https://example.edu/news/page/{page}/",
        listing_selector="a",
        start_page=1,
        end_page=10,
    )
    pages = {
        1: {"https://example.edu/new"},
        2: {"https://example.edu/known-a"},
        3: {"https://example.edu/known-b"},
        4: {"https://example.edu/should-not-be-read"},
    }

    class Discovery:
        def __init__(self, *_args, **_kwargs):
            pass

        async def discover(self, start, _end):
            return SimpleNamespace(urls=pages[start], errors=[], exhausted=False)

    class Store:
        def __init__(self):
            self.known = {
                "https://example.edu/known-a",
                "https://example.edu/known-b",
            }
            self.checked = []

        def add_discovered_urls(self, _site, urls, **kwargs):
            self.checked.append(kwargs["page"])
            inserted = set(urls).difference(self.known)
            self.known.update(inserted)
            return list(inserted)

        def mark_discovery_success(self, _site, empty_pages):
            self.empty_pages = empty_pages

        def record_error(self, **_kwargs):
            raise AssertionError("No discovery error expected")

    store = Store()
    monkeypatch.setattr(daily, "UrlDiscovery", Discovery)

    stats = asyncio.run(
        daily.discover_site(
            store,
            site,
            run_id="run-id",
            max_pages=10,
            stop_after_empty_pages=2,
            concurrency=1,
        )
    )

    assert store.checked == [1, 2, 3]
    assert stats.urls_new == 1
    assert stats.stopped_after_empty_pages == 2


def test_worker_completes_jobs_and_enqueues_only_explicit_next_stages() -> None:
    jobs = [
        Job(
            id=1,
            article_id=stable_article_id("https://example.edu/new"),
            site="usp",
            url="https://example.edu/new",
            stage="preprocess",
            attempts=1,
            priority=100,
        )
    ]

    class Store:
        def __init__(self):
            self.completed = []

        def claim_job(self, *_args, **_kwargs):
            return jobs.pop(0) if jobs else None

        def complete_job(self, job, next_stages):
            self.completed.append((job.id, next_stages))

        def fail_job(self, *_args, **_kwargs):
            raise AssertionError("No worker failure expected")

        @staticmethod
        def renew_lease(*_args, **_kwargs):
            return True

    class Handlers:
        @staticmethod
        def process(_job):
            return ("enrich", "embed")

    store = Store()
    stats = work(
        store,
        Handlers(),
        stages=["preprocess"],
        worker_id="test",
        lease_seconds=60,
        max_attempts=3,
        poll_seconds=0.1,
        heartbeat_seconds=5,
        max_jobs=None,
        exit_when_empty=True,
    )

    assert stats == {"completed": 1, "failed": 0, "retried": 0}
    assert store.completed == [(1, ("enrich", "embed"))]


def test_enrichment_workload_accounts_for_calls_and_retries() -> None:
    workload = enrichment_workload(
        1_000,
        workers=16,
        measured_article_seconds=155,
    )

    assert workload.minimum_llm_calls == 1_000
    assert workload.maximum_validation_calls == 2_000
    assert workload.theoretical_maximum_http_attempts == 8_000
    assert workload.maximum_concurrent_requests == 16
    assert workload.estimated_duration_seconds == 9_765


def test_slow_jobs_renew_their_lease() -> None:
    renewed = threading.Event()

    class Store:
        @staticmethod
        def renew_lease(job_id, worker_id, lease_seconds):
            assert (job_id, worker_id, lease_seconds) == (7, "worker", 60)
            renewed.set()
            return True

    with LeaseHeartbeat(
        Store(),
        job_id=7,
        worker_id="worker",
        lease_seconds=60,
        heartbeat_seconds=0.01,
    ):
        assert renewed.wait(0.2)



def test_worker_pauses_after_transient_errors_and_resets_on_success() -> None:
    jobs = [
        Job(
            id=number,
            article_id=stable_article_id(f"https://example.edu/{number}"),
            site="usp",
            url=f"https://example.edu/{number}",
            stage="enrich",
            attempts=1,
            priority=100,
        )
        for number in range(4)
    ]
    outcomes = ["transient", "transient", "ok", "transient"]

    class RateLimitError(Exception):
        pass

    class Store:
        def __init__(self):
            self.failed = []

        def claim_job(self, *_args, **_kwargs):
            return jobs.pop(0) if jobs else None

        def complete_job(self, *_args):
            pass

        def fail_job(self, job, _error, **kwargs):
            self.failed.append((job.id, kwargs["transient"]))

        @staticmethod
        def renew_lease(*_args, **_kwargs):
            return True

    class Handlers:
        @staticmethod
        def process(_job):
            if outcomes.pop(0) == "transient":
                raise RateLimitError("429")
            return ()

    class RecordingStop(threading.Event):
        def __init__(self):
            super().__init__()
            self.waits = []

        def wait(self, timeout=None):
            self.waits.append(timeout)
            return False

    stop = RecordingStop()
    stats = work(
        Store(),
        Handlers(),
        stages=["enrich"],
        worker_id="test",
        lease_seconds=60,
        max_attempts=3,
        poll_seconds=0.1,
        heartbeat_seconds=5,
        max_jobs=None,
        exit_when_empty=True,
        stop=stop,
        transient_backoff_seconds=10,
    )

    assert stats == {"completed": 1, "failed": 0, "retried": 3}
    assert stop.waits == [10, 20, 10]


async def test_wordpress_site_falls_back_to_html_when_the_api_fails(monkeypatch) -> None:
    site = SiteConfig(
        name="wp",
        base_url="https://example.edu/",
        listing_url="https://example.edu/news/",
        paginated_url="https://example.edu/news/page/{page}/",
        listing_selector="a",
        start_page=1,
        end_page=5,
        wordpress_api="https://example.edu/wp-json/wp/v2/",
    )
    calls = []

    class Discovery:
        def __init__(self, *_args, **_kwargs):
            pass

        async def discover_wordpress(self, rest_base, page, *, after=None):
            calls.append(("api", page))
            return SimpleNamespace(urls=set(), errors=[{"message": "403"}], exhausted=False)

        async def discover(self, start, _end):
            calls.append(("html", start))
            return SimpleNamespace(urls={f"https://example.edu/{start}"}, errors=[], exhausted=False)

    class Store:
        def add_discovered_urls(self, _site, urls, **_kwargs):
            return list(urls)

        def mark_discovery_success(self, *_args):
            pass

        def record_error(self, **_kwargs):
            pass

    monkeypatch.setattr(daily, "UrlDiscovery", Discovery)
    stats = await daily.discover_site(
        Store(), site, run_id="run", max_pages=2, stop_after_empty_pages=3, concurrency=1
    )

    assert calls == [("api", 1), ("api", 2), ("html", 1), ("html", 2)]
    assert stats.urls_new == 2
