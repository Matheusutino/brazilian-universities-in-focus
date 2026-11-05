"""HTTP server: JSON search API, article pages, and the static site."""

from __future__ import annotations

import logging
import math
import threading
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .build import PUBLIC_DIRECTORY
from .render import make_environment
from .search import ArticleIndex, ArticleQuery, CachedEncoder
from ..search.semantic import SemanticHealth, SemanticRuntime

LOGGER = logging.getLogger(__name__)
PAGE_SIZE = 12
MAX_PAGE_SIZE = 48


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid date: {value}") from exc


def monitor_semantic_search(
    settings: dict[str, Any], index: ArticleIndex, health: SemanticHealth,
    stop: threading.Event,
) -> None:
    """Periodically run embedding + retrieval + PostgreSQL ID resolution."""
    runtime = None
    interval = float(settings.get("health_interval_seconds", 60))
    while not stop.is_set():
        try:
            if runtime is None:
                runtime = SemanticRuntime.load(settings)
            report = runtime.probe(
                resolve=lambda ids: index._matching_rows(ids, ArticleQuery())
            )
            published = index.published_count()
            report["published_articles"] = published
            report["coverage_estimate"] = (
                min(report["rows"] / published, 1.0) if published else None
            )
            if stop.is_set():
                return
            if report["ready"]:
                if index.semantic is None:
                    index.encoder = CachedEncoder(
                        runtime.encoder, int(settings.get("query_cache_size", 2048))
                    )
                    index.semantic = runtime.search
            else:
                index.encoder = None
                index.semantic = None
            health.record(**report)
        except Exception as exc:
            if stop.is_set():
                return
            index.encoder = None
            index.semantic = None
            health.failure(exc)
            runtime = None
            LOGGER.exception("semantic probe failed; serving BM25 results")
        stop.wait(interval)


def create_app(
    site_directory: Path, *, index: ArticleIndex, semantic: dict[str, Any] | None = None
) -> FastAPI:
    public = site_directory / PUBLIC_DIRECTORY
    if not public.is_dir():
        raise RuntimeError(
            f"Site not built in {site_directory}; run `university-news-site build`"
        )
    environment = make_environment()
    interval = float((semantic or {}).get("health_interval_seconds", 60))
    if interval <= 0:
        raise ValueError("health_interval_seconds must be positive")
    semantic_health = SemanticHealth(enabled=bool(semantic), max_age=interval * 2 + 30)
    index.semantic_health = semantic_health

    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()
        monitor = None
        try:
            index.open()
            index.health()
            if semantic:
                monitor = threading.Thread(
                    target=monitor_semantic_search,
                    args=(semantic, index, semantic_health, stop), daemon=True,
                )
                monitor.start()
            yield
        finally:
            stop.set()
            if monitor is not None:
                monitor.join(timeout=5)
            index.close()

    app = FastAPI(title="Universidades em Foco", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.index = index

    def page_context() -> dict[str, Any]:
        import json

        stats_path = public / "stats.json"
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        return {
            "stats": stats,
            "version": stats.get("version", "0"),
        }

    @app.get("/healthz")
    def health() -> JSONResponse:
        try:
            index.health()
        except Exception:
            LOGGER.exception("PostgreSQL search health check failed")
            return JSONResponse({"ok": False, "database": False}, status_code=503)
        status = semantic_health.snapshot()
        ready = status["ready"]
        coverage = status.get("coverage_estimate")
        return JSONResponse({
            "ok": True, "database": True, "semantic": ready,
            "degraded": status["enabled"] and (
                not ready or not status.get("efficient", True)
                or (coverage is not None and coverage < 1)
            ),
            "semantic_status": status,
        })

    @app.get("/readyz")
    def readiness() -> JSONResponse:
        response = health()
        if response.status_code != 200:
            return response
        status = semantic_health.snapshot()
        if status["enabled"] and (not status["ready"] or not status.get("efficient", False)):
            return JSONResponse({"ready": False, "semantic_status": status}, status_code=503)
        return JSONResponse({"ready": True, "semantic_status": status})

    @app.get("/api/articles")
    def articles(
        q: str = "",
        region: str | None = None,
        university: str | None = None,
        start: str | None = Query(None, alias="from"),
        end: str | None = Query(None, alias="to"),
        category: str | None = None,
        field: str | None = None,
        content_type: str | None = Query(None, alias="type"),
        analyzed: bool = False,
        mode: str = Query("hybrid", pattern="^(hybrid|lexical)$"),
        page: int = Query(1, ge=1, le=10_000),
        page_size: int = Query(PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ) -> JSONResponse:
        query = ArticleQuery(
            text=q.strip()[:200],
            region=region or None,
            university=university or None,
            start=_parse_date(start),
            end=_parse_date(end),
            category=category or None,
            field=field or None,
            content_type=content_type or None,
            analyzed_only=analyzed,
        )
        offset = (page - 1) * page_size
        result = (
            index.search(query, offset=offset, limit=page_size, semantic_enabled=mode == "hybrid")
            if query.text
            else index.latest(query, offset=offset, limit=page_size)
        )
        response = JSONResponse(
            {
                "total": result.total,
                "total_is_exact": result.total_is_exact,
                "page": page,
                "page_size": page_size,
                "pages": math.ceil(result.total / page_size),
                "mode": result.mode,
                "elapsed_ms": result.elapsed_ms,
                "notes": result.notes,
                "timings": result.timings,
                "retrieval": result.retrieval,
                "results": result.results,
            }
        )
        # Bound cache staleness while endpoints read live PostgreSQL data.
        response.headers["Cache-Control"] = "public, max-age=60" if query.text else "public, max-age=300"
        return response

    @app.get("/api/articles/{identifier}")
    def article_json(identifier: str) -> dict[str, Any]:
        article = index.article(identifier)
        if article is None:
            raise HTTPException(status_code=404, detail="Article not found")
        return article

    @app.get("/noticia/{identifier}", response_class=HTMLResponse)
    def article_page(identifier: str) -> HTMLResponse:
        article = index.article(identifier)
        if article is None:
            raise HTTPException(status_code=404)
        html = environment.get_template("article.html").render(
            **page_context(), page="explore", article=article, a=article["annotation"]
        )
        return HTMLResponse(html, headers={"Cache-Control": "public, max-age=3600"})

    @app.exception_handler(StarletteHTTPException)
    async def not_found(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404 and not request.url.path.startswith("/api/"):
            return HTMLResponse((public / "404.html").read_text(encoding="utf-8"), status_code=404)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    app.mount("/", StaticFiles(directory=public, html=True), name="site")
    return app
