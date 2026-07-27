import argparse
import asyncio
import json
import logging
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

from .pipeline.orchestration import NewsPipeline
from .paths import DEFAULT_DATA_ROOT, sites_directory
from .sources.catalog import SITES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="university-news",
        description="Collect and extract news from university websites.",
    )
    parser.add_argument("site", choices=sorted(SITES), help="Target site")
    parser.add_argument(
        "--stage",
        choices=("discovery", "fetching", "extraction", "all"),
        default="all",
        help="Pipeline stage to run (default: all)",
    )
    parser.add_argument(
        "--extractor",
        choices=("trafilatura", "llm", "hybrid"),
        default="trafilatura",
        help="Extraction backend (default: trafilatura)",
    )
    parser.add_argument(
        "--start-page",
        "--start-offset",
        dest="start_page",
        type=int,
        help="First listing page or offset",
    )
    parser.add_argument(
        "--end-page",
        "--end-offset",
        dest="end_page",
        type=int,
        help="Last listing page or offset",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory (default: data/sites/<site>)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        choices=range(1, 11),
        metavar="1..10",
        help="Maximum concurrent requests (default: 10)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Run the selected stage again"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable detailed logs")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.stage not in {"discovery", "all"} and (
        args.start_page is not None or args.end_page is not None
    ):
        parser.error(
            "--start-page and --end-page require the discovery or all stage"
        )
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    site = SITES[args.site]
    output = args.output or sites_directory(DEFAULT_DATA_ROOT) / site.name
    pipeline = NewsPipeline(site, output, concurrency=args.concurrency)
    try:
        stats = asyncio.run(
            pipeline.run(
                stage=args.stage,
                extractor=args.extractor,
                start_page=args.start_page,
                end_page=args.end_page,
                force=args.force,
            )
        )
    except (OSError, ValueError, RuntimeError) as exc:
        logging.error("Pipeline failed: %s", exc)
        return 1

    print(json.dumps(pipeline.stats_as_dict(stats), ensure_ascii=False))
    return 0


def main() -> None:
    raise SystemExit(run())
