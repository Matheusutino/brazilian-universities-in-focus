"""HTTP server: JSON search API, article pages, and the static site."""

from __future__ import annotations

import logging
import math
import threading
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .build import PUBLIC_DIRECTORY
from .bundle import DATABASE_FILENAME
from .render import make_environment
from .search import ArticleIndex, ArticleQuery, CachedEncoder, lancedb_semantic

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


def load_semantic_search(settings: dict[str, Any], index: ArticleIndex) -> None:
    """Load the query model and vector index in the background.

    Until they are ready (or if they fail), search runs lexical only, so the
    site starts instantly and never depends on the model being available.
    """
    from ..search.embeddings import QwenGGUFEmbedder
    from ..search.vector_index import connect_database

    try:
        table = connect_database(Path(settings["lancedb_path"])).open_table(settings["lancedb_table"])
        model_path = settings.get("model_path")
        encoder = QwenGGUFEmbedder(
            model_path=Path(model_path) if model_path else None,
            model_repo=settings["model_repo"],
            model_file=settings["model_file"],
            context_length=int(settings.get("query_context_length", 512)),
            gpu_layers=int(settings.get("gpu_layers", 0)),
            threads=int(settings.get("threads", 2)),
        )
        encoder.encode_query("aquecimento")  # Warm up before taking traffic.
        index.encoder = CachedEncoder(encoder, int(settings.get("query_cache_size", 2048)))
        index.semantic = lancedb_semantic(table)
        LOGGER.info("semantic search ready")
    except Exception:  # noqa: BLE001 - lexical search keeps working
        LOGGER.exception("semantic search unavailable; serving lexical results")


def create_app(site_directory: Path, *, semantic: dict[str, Any] | None = None) -> FastAPI:
    database = site_directory / DATABASE_FILENAME
    public = site_directory / PUBLIC_DIRECTORY
    if not database.is_file() or not public.is_dir():
        raise RuntimeError(
            f"Site not built in {site_directory}; run `university-news-site build`"
        )
    index = ArticleIndex(database)
    environment = make_environment()
    app = FastAPI(title="Universidades em Foco", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.index = index
    if semantic:
        threading.Thread(target=load_semantic_search, args=(semantic, index), daemon=True).start()

    def page_context() -> dict[str, Any]:
        import json

        stats_path = site_directory / "stats.json"
        return {
            "stats": json.loads(stats_path.read_text(encoding="utf-8")),
            "version": index.meta("version") or "0",
        }

    @app.get("/healthz")
    def health() -> dict[str, Any]:
        return {"ok": True, "semantic": index.encoder is not None}

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
                "page": page,
                "page_size": page_size,
                "pages": math.ceil(result.total / page_size),
                "mode": result.mode,
                "elapsed_ms": result.elapsed_ms,
                "notes": result.notes,
                "timings": result.timings,
                "results": result.results,
            }
        )
        # Listings change only at publication; searches are cheap to repeat.
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
