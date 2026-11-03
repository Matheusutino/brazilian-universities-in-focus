"""university-news-site: build the public website and serve it."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from ..paths import DEFAULT_DATA_ROOT
from ..settings import config_section
from .build import build_site, default_sources


def build_parser() -> argparse.ArgumentParser:
    site = config_section("site")
    parser = argparse.ArgumentParser(
        prog="university-news-site",
        description="Build the static pages and data bundle, or serve the website.",
    )
    parser.add_argument(
        "--site-dir", type=Path, default=Path(str(site.get("directory", "data/site")))
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Rebuild pages and the data bundle")
    build.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
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
        "gpu_layers": site.get("query_gpu_layers", 0),
        "threads": site.get("query_threads", 2),
        "query_cache_size": site.get("query_cache_size", 2048),
    }


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "build":
        try:
            result = build_site(args.site_dir, **default_sources(args.data_root))
        except (OSError, ValueError) as exc:
            print(f"Site build failed: {exc}")
            return 1
        print(json.dumps(asdict(result), ensure_ascii=False))
        return 0

    import os

    import uvicorn

    from .api import create_app

    semantic = None if args.lexical_only else semantic_settings()
    if semantic and int(semantic["gpu_layers"]) == 0:
        # The CUDA build initializes the GPU even with zero offloaded layers;
        # on a full GPU that aborts the whole process. Hide it for CPU queries.
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app(args.site_dir, semantic=semantic)
    # One process: the query model and its cache are loaded once and shared.
    uvicorn.run(app, host=args.address, port=args.port, proxy_headers=True)
    return 0


def main() -> None:
    raise SystemExit(run())
