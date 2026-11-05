"""university-news-site: build the public website and serve it."""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from ..settings import config_section
from .build import build_site
from .search import ArticleIndex, ArticleQuery


def build_parser() -> argparse.ArgumentParser:
    site = config_section("site")
    parser = argparse.ArgumentParser(
        prog="university-news-site",
        description="Build static pages from PostgreSQL, or serve the website.",
    )
    parser.add_argument(
        "--site-dir", type=Path, default=Path(str(site.get("directory", "data/site")))
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-search", help="Apply migrations and enable the PostgreSQL BM25 index")
    commands.add_parser("build", help="Rebuild static pages from PostgreSQL")
    check = commands.add_parser("check-search", help="Verify real semantic retrieval without BM25 fallback")
    check.add_argument("--query", default="prevenção de doenças transmitidas por mosquitos")
    check.add_argument("--top-k", type=int, default=10)
    check.add_argument("--repeat", type=int, default=3)
    check.add_argument("--concurrency", type=int, default=1)
    check.add_argument("--minimum-recall", type=float, default=0.9)
    check.add_argument("--expected-id", nargs="+", default=[])
    serve = commands.add_parser("serve", help="Serve the website and its API")
    serve.add_argument("--address", default=str(site.get("address", "127.0.0.1")))
    serve.add_argument("--port", type=int, default=int(site.get("port", 8501)))
    serve.add_argument(
        "--lexical-only",
        action="store_true",
        help="Do not load the query model (lowest memory; no semantic search)",
    )
    return parser


def semantic_settings() -> dict[str, object] | None:
    site = config_section("site")
    search = config_section("search")
    embedding = config_section("embedding")
    if str(search.get("mode", "hybrid")).lower() == "lexical":
        return None
    return {
        "lancedb_path": search.get("lancedb_path", "data/indexes/lancedb"),
        "lancedb_table": search.get("lancedb_table", "university_articles"),
        "model_path": str(embedding.get("model_path") or "").strip() or None,
        "model_repo": embedding.get("model_repo"),
        "model_file": embedding.get("model_file"),
        "query_context_length": embedding.get("query_context_length", 512),
        "document_context_length": embedding.get("context_length", 32768),
        "health_interval_seconds": site.get("semantic_health_interval_seconds", 60),
        "gpu_layers": site.get("query_gpu_layers", 0),
        "threads": site.get("query_threads", 2),
        "query_cache_size": site.get("query_cache_size", 2048),
    }


def run(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init-search":
        from ..operations.store import PostgresStore
        from .search_schema import initialize_search

        dsn = os.getenv("DATABASE_URL", "").strip()
        if not dsn:
            parser.error("DATABASE_URL is required for init-search")
        store = PostgresStore.open(dsn)
        try:
            store.initialize()
            initialize_search(store.connection)
        finally:
            store.close()
        print("PostgreSQL website schema and BM25 index ready")
        return 0

    settings = config_section("site")
    index = ArticleIndex(
        os.getenv("SITE_DATABASE_URL") or os.getenv("DATABASE_URL", ""),
        max_connections=int(settings.get("database_pool_size", 4)),
        statement_timeout_ms=int(settings.get("statement_timeout_ms", 10000)),
    )
    if args.command == "check-search":
        from ..search.diagnostics import check_search
        from ..search.semantic import SemanticRuntime

        semantic = semantic_settings()
        if semantic is None:
            parser.error("Set [search].mode = 'hybrid' to check semantic retrieval")
        if int(semantic["gpu_layers"]) == 0:
            os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
        try:
            index.open()
            index.health()
            result = check_search(
                SemanticRuntime.load(semantic), args.query,
                top_k=args.top_k, repeat=args.repeat, concurrency=args.concurrency,
                minimum_recall=args.minimum_recall, expected_ids=args.expected_id,
                resolve=lambda ids: index._matching_rows(ids, ArticleQuery()),
            )
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc), "error_type": type(exc).__name__}))
            return 1
        finally:
            index.close()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    if args.command == "build":
        try:
            index.open()
            index.health()
            result = build_site(args.site_dir, index=index)
        finally:
            index.close()
        print(json.dumps(asdict(result), ensure_ascii=False))
        return 0

    import uvicorn

    from .api import create_app

    semantic = None if args.lexical_only else semantic_settings()
    if semantic and int(semantic["gpu_layers"]) == 0:
        # The CUDA build initializes the GPU even with zero offloaded layers;
        # on a full GPU that aborts the whole process. Hide it for CPU queries.
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app(args.site_dir, index=index, semantic=semantic)
    # One process: the query model and its cache are loaded once and shared.
    uvicorn.run(app, host=args.address, port=args.port, proxy_headers=True)
    return 0


def main() -> None:
    raise SystemExit(run())
