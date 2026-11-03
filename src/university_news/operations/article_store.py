"""Article, enrichment, and embedding persistence operations."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from typing import Any

from psycopg import Connection

from .identity import canonicalize_url, payload_hash, stable_article_id


class ArticleStoreMixin:
    connection: Connection[Any]

    def bootstrap_urls(self, site: str, urls: Iterable[str]) -> int:
        inserted = 0
        with self.connection.transaction(), self.connection.cursor() as cursor:
            for url in sorted(set(urls)):
                canonical = canonicalize_url(url)
                cursor.execute(
                    """
                    INSERT INTO discovered_urls (id, site, url, canonical_url)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (canonical_url) DO NOTHING
                    """,
                    (stable_article_id(canonical), site, url, canonical),
                )
                inserted += int(cursor.rowcount > 0)
        return inserted

    def save_raw_document(
        self,
        article_id: uuid.UUID,
        *,
        path: str,
        sha256: str,
        status_code: int | None,
    ) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO raw_documents (article_id, path, sha256, status_code)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (article_id) DO UPDATE
                SET path = EXCLUDED.path, sha256 = EXCLUDED.sha256,
                    status_code = EXCLUDED.status_code, fetched_at = now()
                """,
                (article_id, path, sha256, status_code),
            )

    def save_article(
        self,
        article_id: uuid.UUID,
        *,
        site: str,
        url: str,
        status: str,
        payload: dict[str, Any],
        content_hash: str | None = None,
        rejection_reason: str | None = None,
    ) -> None:
        if status not in {"extracted", "accepted", "rejected"}:
            raise ValueError(f"Invalid article status: {status}")
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO articles
                    (id, site, url, status, payload, content_hash, rejection_reason,
                     extracted_at, processed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s,
                        CASE WHEN %s = 'extracted' THEN now() ELSE NULL END,
                        CASE WHEN %s IN ('accepted','rejected') THEN now() ELSE NULL END)
                ON CONFLICT (id) DO UPDATE
                SET status = EXCLUDED.status, payload = EXCLUDED.payload,
                    content_hash = EXCLUDED.content_hash,
                    rejection_reason = EXCLUDED.rejection_reason,
                    extracted_at = COALESCE(articles.extracted_at, EXCLUDED.extracted_at),
                    processed_at = COALESCE(EXCLUDED.processed_at, articles.processed_at),
                    updated_at = now()
                """,
                (
                    article_id,
                    site,
                    url,
                    status,
                    json.dumps(payload, ensure_ascii=False),
                    content_hash,
                    rejection_reason,
                    status,
                    status,
                ),
            )

    def get_article(self, article_id: uuid.UUID) -> dict[str, Any]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM articles WHERE id = %s", (article_id,))
            row = cursor.fetchone()
        if not row:
            raise KeyError(f"Article not found: {article_id}")
        payload = row["payload"]
        return payload if isinstance(payload, dict) else json.loads(payload)

    def save_embedding_state(
        self, article_id: uuid.UUID, *, content_hash: str, model: str
    ) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO embedding_state (article_id, content_hash, model)
                VALUES (%s, %s, %s)
                ON CONFLICT (article_id) DO NOTHING
                """,
                (article_id, content_hash, model),
            )

    def bootstrap_articles(
        self, site: str, articles: Iterable[dict[str, Any]]
    ) -> int:
        inserted_count = 0
        with self.connection.transaction(), self.connection.cursor() as cursor:
            for article in articles:
                inserted_count += int(self._bootstrap_article(cursor, site, article))
        return inserted_count

    @staticmethod
    def _bootstrap_article(cursor: Any, site: str, article: dict[str, Any]) -> bool:
        url = article.get("url")
        if not isinstance(url, str) or not url.strip():
            return False
        identifier = stable_article_id(url)
        canonical = canonicalize_url(url)
        digest = payload_hash(article)
        cursor.execute(
            """
            INSERT INTO discovered_urls (id, site, url, canonical_url)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (canonical_url) DO NOTHING
            """,
            (identifier, site, url, canonical),
        )
        cursor.execute(
            """
            INSERT INTO articles (id, site, url, status, payload, content_hash,
                                  extracted_at, processed_at)
            VALUES (%s, %s, %s, 'accepted', %s, %s, now(), now())
            ON CONFLICT (id) DO NOTHING
            """,
            (identifier, site, url, json.dumps(article, ensure_ascii=False), digest),
        )
        article_inserted = cursor.rowcount > 0
        return article_inserted

    def enqueue_embedding_backfill(self, *, limit: int | None, priority: int = 10) -> int:
        stage = "embed"
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO processing_jobs (article_id, site, stage, priority)
                SELECT article.id, article.site, %s, %s
                FROM articles AS article
                LEFT JOIN processing_jobs AS job
                  ON job.article_id = article.id AND job.stage = %s
                WHERE article.status = 'accepted' AND job.id IS NULL
                ORDER BY article.processed_at, article.id
                LIMIT %s
                ON CONFLICT (article_id, stage) DO NOTHING
                """,
                (stage, priority, stage, limit),  # LIMIT NULL means no limit.
            )
            return cursor.rowcount

    def accepted_articles(self, site: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT payload FROM articles WHERE status = 'accepted'"
        params: tuple[Any, ...] = ()
        if site:
            query += " AND site = %s"
            params = (site,)
        query += " ORDER BY site, url"
        with self.connection.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        return [
            row["payload"]
            if isinstance(row["payload"], dict)
            else json.loads(row["payload"])
            for row in rows
        ]

    def accepted_article_count(self) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) AS articles FROM articles WHERE status = 'accepted'"
            )
            row = cursor.fetchone()
        return int(row["articles"]) if row else 0
