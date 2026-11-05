"""PostgreSQL run tracking and durable work queue."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from psycopg import Connection, connect
from psycopg.rows import dict_row

from .article_store import ArticleStoreMixin
from .database_schema import MIGRATIONS
from .enrichment_store import EnrichmentStoreMixin
from .identity import canonicalize_url, stable_article_id

JOB_STAGES = ("fetch", "extract", "preprocess", "enrich", "embed")
DAILY_LOCK_NAME = "university-news-daily"
MIGRATION_LOCK_NAME = "university-news-migrations"
RUN_STATUSES = ("completed", "failed", "interrupted")


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    article_id: uuid.UUID
    site: str
    url: str
    stage: str
    attempts: int
    priority: int
    worker_id: str = ""
    lease_token: uuid.UUID | None = None


class LeaseLostError(RuntimeError):
    """A worker no longer owns the claimed attempt."""


class PostgresStore(ArticleStoreMixin, EnrichmentStoreMixin):
    """Repository shared by coordinators and worker processes."""

    def __init__(self, connection: Connection[Any]) -> None:
        self.connection = connection

    @classmethod
    def open(cls, dsn: str) -> PostgresStore:
        # Session advisory locks stay independent from short data transactions.
        return cls(connect(dsn, autocommit=True, row_factory=dict_row))

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> list[int]:
        """Apply pending schema migrations and return their versions."""
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(hashtext(%s))", (MIGRATION_LOCK_NAME,))
        try:
            with self.connection.transaction(), self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version integer PRIMARY KEY,
                        description text NOT NULL,
                        applied_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                cursor.execute("SELECT version FROM schema_migrations")
                applied = {int(row["version"]) for row in cursor.fetchall()}
            pending = [migration for migration in MIGRATIONS if migration[0] not in applied]
            for version, description, sql in pending:
                with self.connection.transaction(), self.connection.cursor() as cursor:
                    cursor.execute(sql)
                    cursor.execute(
                        "INSERT INTO schema_migrations (version, description) VALUES (%s, %s)",
                        (version, description),
                    )
            return [version for version, _description, _sql in pending]
        finally:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_unlock(hashtext(%s))", (MIGRATION_LOCK_NAME,)
                )

    def schema_version(self) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT coalesce(max(version), 0) AS version FROM schema_migrations")
            row = cursor.fetchone()
        return int(row["version"]) if row else 0

    def register_source(self, site: str, base_url: str) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO sources (site, base_url) VALUES (%s, %s)
                ON CONFLICT (site) DO UPDATE
                SET base_url = EXCLUDED.base_url, updated_at = now()
                """,
                (site, base_url),
            )

    def start_run(
        self,
        kind: str,
        *,
        config: dict[str, Any] | None = None,
        worker_id: str | None = None,
    ) -> uuid.UUID:
        """Record a run with a snapshot of the configuration it used."""
        run_id = uuid.uuid4()
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pipeline_runs (id, kind, status, config, worker_id)
                VALUES (%s, %s, 'running', %s, %s)
                """,
                (run_id, kind, json.dumps(config or {}, ensure_ascii=False), worker_id),
            )
        return run_id

    def finish_run(
        self,
        run_id: uuid.UUID,
        *,
        stats: dict[str, Any],
        error: str | None = None,
        status: str | None = None,
    ) -> None:
        status = status or ("failed" if error else "completed")
        if status not in RUN_STATUSES:
            raise ValueError(f"Invalid run status: {status}")
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE pipeline_runs
                SET status = %s, finished_at = now(), stats = %s, error = %s
                WHERE id = %s
                """,
                (status, json.dumps(stats), error, run_id),
            )

    def try_daily_lock(self) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (DAILY_LOCK_NAME,))
            row = cursor.fetchone()
        return bool(row and next(iter(row.values())))

    def release_daily_lock(self) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (DAILY_LOCK_NAME,))

    def add_discovered_urls(
        self,
        site: str,
        urls: Iterable[str],
        *,
        run_id: uuid.UUID | None,
        page: int | None,
        priority: int = 100,
    ) -> list[uuid.UUID]:
        inserted: list[uuid.UUID] = []
        with self.connection.transaction(), self.connection.cursor() as cursor:
            for url in sorted(set(urls)):
                canonical = canonicalize_url(url)
                identifier = stable_article_id(canonical)
                cursor.execute(
                    """
                    INSERT INTO discovered_urls
                        (id, site, url, canonical_url, discovery_run_id, discovery_page)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (canonical_url) DO NOTHING
                    RETURNING id
                    """,
                    (identifier, site, url, canonical, run_id, page),
                )
                if cursor.fetchone() is None:
                    continue
                inserted.append(identifier)
                cursor.execute(
                    """
                    INSERT INTO processing_jobs (article_id, site, stage, priority)
                    VALUES (%s, %s, 'fetch', %s)
                    ON CONFLICT (article_id, stage) DO NOTHING
                    """,
                    (identifier, site, priority),
                )
        return inserted

    def mark_discovery_success(self, site: str, empty_pages: int) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE sources
                SET last_successful_discovery_at = now(),
                    consecutive_empty_pages = %s, updated_at = now()
                WHERE site = %s
                """,
                (empty_pages, site),
            )

    def claim_job(
        self,
        stages: Sequence[str],
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> Job | None:
        self._validate_stages(stages)
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT id FROM processing_jobs
                    WHERE stage = ANY(%s) AND available_at <= now()
                      AND (status = 'queued'
                           OR (status = 'running' AND lease_expires_at < now()))
                    ORDER BY priority DESC, available_at, id
                    FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE processing_jobs AS job
                SET status = 'running', attempts = attempts + 1,
                    worker_id = %s, lease_token = %s, started_at = COALESCE(started_at, now()),
                    lease_expires_at = now() + make_interval(secs => %s)
                FROM candidate WHERE job.id = candidate.id
                RETURNING job.id, job.article_id, job.site, job.stage,
                          job.attempts, job.priority, job.worker_id, job.lease_token,
                          (SELECT url FROM discovered_urls WHERE id = job.article_id) AS url
                """,
                (list(stages), worker_id, uuid.uuid4(), lease_seconds),
            )
            row = cursor.fetchone()
        return Job(**row) if row else None

    @contextmanager
    def lease_guard(self, job: Job):
        """Fence side effects while holding the job row against a new claimant."""
        with self.connection.transaction(), self.connection.cursor() as cursor:
            # Lock first, then check time in a separate statement: the lease can
            # expire while this transaction waits for someone else's row lock.
            cursor.execute("SELECT id FROM processing_jobs WHERE id = %s FOR UPDATE", (job.id,))
            cursor.execute(
                """
                SELECT id FROM processing_jobs
                WHERE id = %s AND status = 'running' AND worker_id = %s
                  AND lease_token = %s AND lease_expires_at > clock_timestamp()
                """,
                (job.id, job.worker_id, job.lease_token),
            )
            if cursor.fetchone() is None:
                raise LeaseLostError(f"Job {job.id} lease ownership was lost")
            yield

    def complete_job(self, job: Job, next_stages: Sequence[str] = ()) -> bool:
        self._validate_stages(next_stages)
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute("SELECT id FROM processing_jobs WHERE id = %s FOR UPDATE", (job.id,))
            cursor.execute(
                """
                UPDATE processing_jobs SET status = 'completed', finished_at = now(),
                    lease_expires_at = NULL, lease_token = NULL, last_error = NULL
                WHERE id = %s AND status = 'running' AND worker_id = %s
                  AND lease_token = %s AND lease_expires_at > clock_timestamp()
                """,
                (job.id, job.worker_id, job.lease_token),
            )
            if cursor.rowcount != 1:
                return False
            for stage in next_stages:
                cursor.execute(
                    """
                    INSERT INTO processing_jobs (article_id, site, stage, priority)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (article_id, stage) DO NOTHING
                    """,
                    (job.article_id, job.site, stage, job.priority),
                )
        return True

    def renew_lease(self, job: Job, lease_seconds: int) -> bool:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute("SELECT id FROM processing_jobs WHERE id = %s FOR UPDATE", (job.id,))
            cursor.execute(
                """
                UPDATE processing_jobs
                SET lease_expires_at = clock_timestamp() + make_interval(secs => %s)
                WHERE id = %s AND worker_id = %s AND lease_token = %s
                  AND status = 'running' AND lease_expires_at > clock_timestamp()
                """,
                (lease_seconds, job.id, job.worker_id, job.lease_token),
            )
            return cursor.rowcount == 1

    def fail_job(
        self,
        job: Job,
        error: Exception,
        *,
        transient: bool,
        max_attempts: int,
        run_id: uuid.UUID | None = None,
        details: dict[str, Any] | None = None,
    ) -> bool:
        retry = transient and job.attempts < max_attempts
        delay_seconds = min(60 * (5 ** max(job.attempts - 1, 0)), 43_200)
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute("SELECT id FROM processing_jobs WHERE id = %s FOR UPDATE", (job.id,))
            cursor.execute(
                """
                UPDATE processing_jobs SET status = %s,
                    available_at = CASE WHEN %s THEN now() + make_interval(secs => %s)
                                        ELSE available_at END,
                    lease_expires_at = NULL, lease_token = NULL, worker_id = NULL,
                    finished_at = CASE WHEN %s THEN NULL ELSE now() END,
                    last_error = %s WHERE id = %s AND status = 'running'
                      AND worker_id = %s AND lease_token = %s
                      AND lease_expires_at > clock_timestamp()
                """,
                ("queued" if retry else "failed", retry, delay_seconds, retry, str(error),
                 job.id, job.worker_id, job.lease_token),
            )
            if cursor.rowcount != 1:
                return False
            cursor.execute(
                """
                INSERT INTO pipeline_errors
                    (run_id, job_id, article_id, site, stage, transient,
                     error_type, message, details)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (run_id, job.id, job.article_id, job.site, job.stage,
                 transient, type(error).__name__, str(error),
                 json.dumps(details or {}, ensure_ascii=False, default=str)),
            )
        return True

    def record_error(
        self,
        *,
        run_id: uuid.UUID,
        site: str,
        stage: str,
        error: Exception,
        transient: bool,
    ) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO pipeline_errors
                    (run_id, site, stage, transient, error_type, message)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (run_id, site, stage, transient, type(error).__name__, str(error)),
            )

    def jobs_summary(self) -> list[dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT stage, status, count(*) AS jobs,
                       min(created_at) AS oldest_created_at
                FROM processing_jobs GROUP BY stage, status ORDER BY stage, status
                """
            )
            return list(cursor.fetchall())

    def operational_status(self) -> dict[str, list[dict[str, Any]]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT site, status, count(*) AS articles
                FROM articles GROUP BY site, status ORDER BY site, status
                """
            )
            articles = list(cursor.fetchall())
            cursor.execute(
                """
                SELECT id, kind, status, worker_id, started_at, finished_at, stats, error
                FROM pipeline_runs ORDER BY started_at DESC LIMIT 10
                """
            )
            runs = list(cursor.fetchall())
            cursor.execute(
                """
                SELECT site, stage, transient, error_type, message, occurred_at
                FROM pipeline_errors ORDER BY occurred_at DESC LIMIT 20
                """
            )
            errors = list(cursor.fetchall())
        return {
            "jobs": self.jobs_summary(),
            "articles": articles,
            "recent_runs": runs,
            "recent_errors": errors,
        }

    def recent_errors(
        self, *, stage: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Recent failures with their context (profile, spent request metrics)."""
        if stage:
            self._validate_stages([stage])
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT error.occurred_at, error.site, error.stage, error.transient,
                       error.error_type, error.message, error.run_id, error.job_id,
                       job.status AS job_status, job.attempts,
                       discovered.url, error.details
                FROM pipeline_errors AS error
                LEFT JOIN processing_jobs AS job ON job.id = error.job_id
                LEFT JOIN discovered_urls AS discovered ON discovered.id = error.article_id
                WHERE %(stage)s::text IS NULL OR error.stage = %(stage)s
                ORDER BY error.occurred_at DESC LIMIT %(limit)s
                """,
                {"stage": stage, "limit": limit},
            )
            return list(cursor.fetchall())

    def stage_job_count(self, stage: str) -> int:
        self._validate_stages([stage])
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*) AS jobs FROM processing_jobs
                WHERE stage = %s AND status IN ('queued', 'running')
                """,
                (stage,),
            )
            row = cursor.fetchone()
        return int(row["jobs"]) if row else 0

    def retry_failed(
        self, stage: str | None = None, *, site: str | None = None, limit: int | None = None
    ) -> int:
        if stage:
            self._validate_stages([stage])
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                WITH candidates AS (
                    SELECT id FROM processing_jobs
                    WHERE status = 'failed'
                      AND (%s::text IS NULL OR stage = %s)
                      AND (%s::text IS NULL OR site = %s)
                    ORDER BY id FOR UPDATE SKIP LOCKED LIMIT %s
                )
                UPDATE processing_jobs AS job
                SET status = 'queued', available_at = now(), attempts = 0,
                    finished_at = NULL, last_error = NULL, worker_id = NULL,
                    lease_expires_at = NULL, lease_token = NULL
                FROM candidates WHERE job.id = candidates.id
                """,
                (stage, stage, site, site, limit),
            )
            return cursor.rowcount

    @staticmethod
    def _validate_stages(stages: Sequence[str]) -> None:
        unknown = sorted(set(stages).difference(JOB_STAGES))
        if unknown:
            raise ValueError(f"Unknown job stages: {', '.join(unknown)}")
