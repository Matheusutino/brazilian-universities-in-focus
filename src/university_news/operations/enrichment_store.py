"""Enrichment profiles, annotations, progress, and export queries."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Sequence
from typing import Any

from psycopg import Connection

# Throughput is measured over this trailing window.
THROUGHPUT_WINDOW_MINUTES = 15


class EnrichmentStoreMixin:
    connection: Connection[Any]

    def register_enrichment_profile(self, profile: dict[str, Any]) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO enrichment_profiles
                    (id, provider, model, schema_version, prompt_sha256, settings,
                     prompts, contract)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET prompts = coalesce(enrichment_profiles.prompts, EXCLUDED.prompts),
                    contract = coalesce(enrichment_profiles.contract, EXCLUDED.contract)
                """,
                (
                    profile["id"],
                    profile["provider"],
                    profile["model"],
                    profile["schema_version"],
                    profile["prompt_sha256"],
                    json.dumps(profile["settings"], sort_keys=True),
                    json.dumps(profile.get("prompts"), ensure_ascii=False)
                    if profile.get("prompts")
                    else None,
                    json.dumps(profile.get("contract"), ensure_ascii=False)
                    if profile.get("contract")
                    else None,
                ),
            )

    def has_enrichment(self, article_id: uuid.UUID, profile_id: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM enrichments WHERE article_id = %s AND profile_id = %s",
                (article_id, profile_id),
            )
            return cursor.fetchone() is not None

    def save_enrichment(
        self,
        article_id: uuid.UUID,
        result: dict[str, Any],
        *,
        profile_id: str,
        run_id: uuid.UUID | None = None,
    ) -> bool:
        """Store one annotation; return False if the profile already had one."""
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO enrichments
                    (article_id, profile_id, content_hash, run_id, payload, metrics)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (article_id, profile_id) DO NOTHING
                """,
                (
                    article_id,
                    profile_id,
                    result.get("content_hash"),
                    run_id,
                    json.dumps(result, ensure_ascii=False),
                    json.dumps(result.get("generation_metrics"), ensure_ascii=False),
                ),
            )
            return cursor.rowcount == 1

    def enqueue_enrichment_backfill(
        self,
        profile_id: str,
        *,
        limit: int | None,
        priority: int = 10,
        reannotate: bool = False,
    ) -> dict[str, int]:
        """Queue accepted articles that still need enrichment.

        Articles without an enrich job get one; whichever provider's worker
        claims it annotates it. With ``reannotate``, completed jobs whose
        article lacks this profile's annotation (because another profile
        processed it) are queued again, to compare profiles on the same
        articles. Failed jobs are left alone; requeue them with ``retry``.
        """
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO processing_jobs (article_id, site, stage, priority)
                SELECT article.id, article.site, 'enrich', %s
                FROM articles AS article
                LEFT JOIN processing_jobs AS job
                  ON job.article_id = article.id AND job.stage = 'enrich'
                WHERE article.status = 'accepted' AND job.id IS NULL
                ORDER BY article.processed_at, article.id
                LIMIT %s
                ON CONFLICT (article_id, stage) DO NOTHING
                """,
                (priority, limit),
            )
            created = cursor.rowcount
            remaining = None if limit is None else max(limit - created, 0)
            requeued = 0
            if reannotate and remaining != 0:
                cursor.execute(
                    """
                    UPDATE processing_jobs AS job
                    SET status = 'queued', priority = %s, attempts = 0,
                        available_at = now(), finished_at = NULL, last_error = NULL
                    WHERE job.id IN (
                        SELECT candidate.id FROM processing_jobs AS candidate
                        JOIN articles AS article ON article.id = candidate.article_id
                        WHERE candidate.stage = 'enrich'
                          AND candidate.status = 'completed'
                          AND article.status = 'accepted'
                          AND NOT EXISTS (
                              SELECT 1 FROM enrichments AS enrichment
                              WHERE enrichment.article_id = candidate.article_id
                                AND enrichment.profile_id = %s
                          )
                        ORDER BY article.processed_at, article.id
                        LIMIT %s
                    )
                    """,
                    (priority, profile_id, remaining),
                )
                requeued = cursor.rowcount
        return {"created": created, "requeued": requeued}

    def average_enrichment_seconds(self, profile_id: str | None = None) -> float | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT avg((metrics->>'duration_seconds')::double precision) AS seconds
                FROM enrichments
                WHERE metrics ? 'duration_seconds'
                  AND (%(profile)s::text IS NULL OR profile_id = %(profile)s)
                """,
                {"profile": profile_id},
            )
            row = cursor.fetchone()
        if not row or row["seconds"] is None:
            return None
        return float(row["seconds"])

    def enrichment_profiles(self, *, full: bool = False) -> list[dict[str, Any]]:
        """Profiles with coverage; ``full`` adds the prompts and contract."""
        extra = "profile.prompts, profile.contract," if full else ""
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT profile.id, profile.provider, profile.model,
                       profile.schema_version, profile.prompt_sha256,
                       profile.settings, profile.created_at, {extra}
                       count(enrichment.article_id) AS annotations,
                       max(enrichment.created_at) AS last_annotation_at
                FROM enrichment_profiles AS profile
                LEFT JOIN enrichments AS enrichment ON enrichment.profile_id = profile.id
                GROUP BY profile.id ORDER BY profile.created_at
                """
            )
            return list(cursor.fetchall())

    def enrichment_progress(self, profile_id: str) -> dict[str, Any]:
        """Summarize one profile: coverage, cost, throughput, failures, and ETA."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT count(*) FROM articles WHERE status = 'accepted') AS accepted,
                    count(*) AS annotated,
                    coalesce(sum((metrics->>'api_requests')::int), 0) AS api_requests,
                    coalesce(sum((metrics->>'validation_retries')::int), 0)
                        AS validation_retries,
                    coalesce(sum((metrics->>'rate_limited_requests')::int), 0)
                        AS rate_limited_requests,
                    coalesce(sum((metrics->>'prompt_tokens')::bigint), 0) AS prompt_tokens,
                    coalesce(sum((metrics->>'completion_tokens')::bigint), 0)
                        AS completion_tokens,
                    avg((metrics->>'duration_seconds')::double precision)
                        AS average_seconds,
                    coalesce(sum((metrics->>'rate_limit_wait_seconds')::double precision), 0)
                        AS rate_limit_wait_seconds,
                    count(*) FILTER (
                        WHERE created_at > now() - make_interval(mins => %(window)s)
                    ) AS recent_articles,
                    coalesce(sum((metrics->>'api_requests')::int) FILTER (
                        WHERE created_at > now() - make_interval(mins => %(window)s)
                    ), 0) AS recent_requests,
                    min(created_at) AS first_annotation_at,
                    max(created_at) AS last_annotation_at
                FROM enrichments WHERE profile_id = %(profile)s
                """,
                {"profile": profile_id, "window": THROUGHPUT_WINDOW_MINUTES},
            )
            totals = dict(cursor.fetchone() or {})
            cursor.execute(
                """
                SELECT status, count(*) AS jobs FROM processing_jobs
                WHERE stage = 'enrich' GROUP BY status
                """
            )
            jobs = {row["status"]: int(row["jobs"]) for row in cursor.fetchall()}
            cursor.execute(
                """
                SELECT error_type, transient, count(*) AS errors,
                       max(occurred_at) AS last_occurred_at,
                       (array_agg(message ORDER BY occurred_at DESC))[1] AS last_message
                FROM pipeline_errors
                WHERE stage = 'enrich' AND details->>'profile_id' = %s
                  AND occurred_at > now() - interval '24 hours'
                GROUP BY error_type, transient ORDER BY errors DESC
                """,
                (profile_id,),
            )
            errors = list(cursor.fetchall())
        articles_per_minute = totals["recent_articles"] / THROUGHPUT_WINDOW_MINUTES
        pending = jobs.get("queued", 0) + jobs.get("running", 0)
        return {
            "profile_id": profile_id,
            "accepted_articles": int(totals["accepted"]),
            "annotated": int(totals["annotated"]),
            "missing": int(totals["accepted"]) - int(totals["annotated"]),
            "enrich_jobs": jobs,
            "cost": {
                "api_requests": int(totals["api_requests"]),
                "validation_retries": int(totals["validation_retries"]),
                "rate_limited_requests": int(totals["rate_limited_requests"]),
                "prompt_tokens": int(totals["prompt_tokens"]),
                "completion_tokens": int(totals["completion_tokens"]),
                "average_article_seconds": totals["average_seconds"],
                "rate_limit_wait_seconds": float(totals["rate_limit_wait_seconds"]),
            },
            "throughput": {
                "window_minutes": THROUGHPUT_WINDOW_MINUTES,
                "articles_per_minute": round(articles_per_minute, 2),
                "requests_per_minute": round(
                    totals["recent_requests"] / THROUGHPUT_WINDOW_MINUTES, 2
                ),
                "eta_minutes": (
                    round(pending / articles_per_minute) if articles_per_minute else None
                ),
            },
            "errors_last_24h": errors,
            "first_annotation_at": totals["first_annotation_at"],
            "last_annotation_at": totals["last_annotation_at"],
        }

    def enrichment_overview(self, profile_ids: Sequence[str]) -> dict[str, Any]:
        """Combined coverage when several profiles share the enrich queue."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    (SELECT count(*) FROM articles WHERE status = 'accepted') AS accepted,
                    count(DISTINCT article_id) AS annotated,
                    count(DISTINCT article_id) FILTER (
                        WHERE created_at > now() - make_interval(mins => %(window)s)
                    ) AS recent_articles
                FROM enrichments WHERE profile_id = ANY(%(profiles)s)
                """,
                {"profiles": list(profile_ids), "window": THROUGHPUT_WINDOW_MINUTES},
            )
            totals = dict(cursor.fetchone() or {})
            cursor.execute(
                """
                SELECT status, count(*) AS jobs FROM processing_jobs
                WHERE stage = 'enrich' GROUP BY status
                """
            )
            jobs = {row["status"]: int(row["jobs"]) for row in cursor.fetchall()}
        articles_per_minute = totals["recent_articles"] / THROUGHPUT_WINDOW_MINUTES
        pending = jobs.get("queued", 0) + jobs.get("running", 0)
        return {
            "profiles": list(profile_ids),
            "accepted_articles": int(totals["accepted"]),
            "annotated_by_any_profile": int(totals["annotated"]),
            "missing": int(totals["accepted"]) - int(totals["annotated"]),
            "enrich_jobs": jobs,
            "articles_per_minute": round(articles_per_minute, 2),
            "eta_minutes": (
                round(pending / articles_per_minute) if articles_per_minute else None
            ),
            "by_profile": [self.enrichment_progress(profile) for profile in profile_ids],
        }

    def iter_enrichments(self, profile_ids: Sequence[str]) -> Iterator[dict[str, Any]]:
        """Stream the profiles' annotations in a stable order."""
        # Server-side cursors need a transaction on an autocommit connection.
        with (
            self.connection.transaction(),
            self.connection.cursor(name=f"export_{uuid.uuid4().hex}") as cursor,
        ):
            cursor.execute(
                """
                SELECT enrichment.article_id, enrichment.profile_id, article.site,
                       article.url, enrichment.run_id, enrichment.created_at,
                       enrichment.payload
                FROM enrichments AS enrichment
                JOIN articles AS article ON article.id = enrichment.article_id
                WHERE enrichment.profile_id = ANY(%s)
                ORDER BY article.site, article.url, enrichment.profile_id
                """,
                (list(profile_ids),),
            )
            for row in cursor:
                payload = row["payload"]
                yield {
                    **(payload if isinstance(payload, dict) else json.loads(payload)),
                    "article_id": str(row["article_id"]),
                    "profile_id": row["profile_id"],
                    "run_id": str(row["run_id"]) if row["run_id"] else None,
                    "stored_at": row["created_at"].isoformat(),
                }
