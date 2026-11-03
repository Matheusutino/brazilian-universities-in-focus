"""Daily discovery that enqueues only URLs never seen before."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date

from dotenv import load_dotenv

from ..pipeline.discovery import DiscoveryResult, UrlDiscovery
from ..settings import config_section
from ..sources.catalog import SITES, SiteConfig
from .store import PostgresStore


@dataclass(slots=True)
class SiteDiscoveryStats:
    site: str
    pages_checked: int = 0
    urls_seen: int = 0
    urls_new: int = 0
    errors: int = 0
    stopped_after_empty_pages: int = 0


async def discover_site(
    store: PostgresStore,
    site: SiteConfig,
    *,
    run_id,
    max_pages: int,
    stop_after_empty_pages: int,
    concurrency: int,
    after: date | None = None,
) -> SiteDiscoveryStats:
    stats = SiteDiscoveryStats(site=site.name)
    discovery = UrlDiscovery(site, concurrency=concurrency)
    if site.wordpress_api:
        sources: list[tuple[str, Callable[[int], Awaitable[DiscoveryResult]]]] = [
            (
                rest_base,
                lambda page, rest_base=rest_base: discovery.discover_wordpress(
                    rest_base, page, after=after
                ),
            )
            for rest_base in site.wordpress_types
        ]
        api_worked = False
        for rest_base, fetch in sources:
            empty_pages = await _walk_pages(
                store, site, stats, fetch, range(1, max_pages + 1),
                run_id=run_id, stop_after_empty_pages=stop_after_empty_pages,
            )
            api_worked = api_worked or stats.urls_seen > 0
        if api_worked:
            store.mark_discovery_success(site.name, empty_pages)
            return stats
        # The API failed or returned nothing: use the HTML listing instead.
    pages = list(site.page_values(site.start_page, site.end_page))[:max_pages]
    empty_pages = await _walk_pages(
        store, site, stats, lambda page: discovery.discover(page, page), pages,
        run_id=run_id, stop_after_empty_pages=stop_after_empty_pages,
    )
    store.mark_discovery_success(site.name, empty_pages)
    return stats


async def _walk_pages(
    store: PostgresStore,
    site: SiteConfig,
    stats: SiteDiscoveryStats,
    fetch: Callable[[int], Awaitable[DiscoveryResult]],
    pages: Iterable[int],
    *,
    run_id,
    stop_after_empty_pages: int,
) -> int:
    """Read pages newest first until several bring nothing new."""
    empty_pages = 0
    for page in pages:
        result = await fetch(page)
        stats.pages_checked += 1
        if result.errors:
            stats.errors += len(result.errors)
            empty_pages = 0
            for record in result.errors:
                store.record_error(
                    run_id=run_id,
                    site=site.name,
                    stage="discovery",
                    error=RuntimeError(str(record.get("message") or record)),
                    transient=True,
                )
            continue

        stats.urls_seen += len(result.urls)
        inserted = store.add_discovered_urls(
            site.name,
            result.urls,
            run_id=run_id,
            page=page,
            priority=100,
        )
        stats.urls_new += len(inserted)
        empty_pages = empty_pages + 1 if not inserted else 0
        if empty_pages >= stop_after_empty_pages:
            stats.stopped_after_empty_pages = empty_pages
            break
        if result.exhausted:
            break
    return empty_pages


async def run_daily(
    store: PostgresStore,
    sites: Sequence[SiteConfig],
    *,
    max_pages: int,
    stop_after_empty_pages: int,
    concurrency: int,
    after: date | None = None,
) -> list[SiteDiscoveryStats]:
    if not store.try_daily_lock():
        raise RuntimeError("Another daily discovery run is already active")
    results: list[SiteDiscoveryStats] = []
    run_id = None
    try:
        run_id = store.start_run("daily-discovery")
        for site in sites:
            store.register_source(site.name, site.base_url)
            results.append(
                await discover_site(
                    store,
                    site,
                    run_id=run_id,
                    max_pages=max_pages,
                    stop_after_empty_pages=stop_after_empty_pages,
                    concurrency=concurrency,
                    after=after,
                )
            )
    except Exception as exc:
        if run_id is not None:
            store.finish_run(
                run_id,
                stats={"sites": [asdict(item) for item in results]},
                error=str(exc),
            )
        raise
    else:
        assert run_id is not None
        store.finish_run(run_id, stats={"sites": [asdict(item) for item in results]})
        return results
    finally:
        store.release_daily_lock()


def build_parser() -> argparse.ArgumentParser:
    settings = config_section("daily_discovery")
    parser = argparse.ArgumentParser(
        prog="university-news-daily",
        description="Discover and enqueue only university-news URLs not seen before.",
    )
    parser.add_argument("--sites", nargs="+", choices=sorted(SITES))
    parser.add_argument(
        "--max-pages",
        type=int,
        default=int(settings.get("max_pages", 20)),
    )
    parser.add_argument(
        "--stop-after-empty-pages",
        type=int,
        default=int(settings.get("stop_after_empty_pages", 3)),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(settings.get("concurrency", 5)),
        choices=range(1, 33),
    )
    parser.add_argument(
        "--after",
        type=date.fromisoformat,
        help="WordPress sites only: list articles published after YYYY-MM-DD (to recover a gap)",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    if args.stop_after_empty_pages < 1:
        parser.error("--stop-after-empty-pages must be at least 1")
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        print("Daily discovery failed: DATABASE_URL is not set in .env")
        return 1

    store = PostgresStore.open(dsn)
    try:
        store.initialize()
        selected = [SITES[name] for name in (args.sites or sorted(SITES))]
        stats = asyncio.run(
            run_daily(
                store,
                selected,
                max_pages=args.max_pages,
                stop_after_empty_pages=args.stop_after_empty_pages,
                concurrency=args.concurrency,
                after=args.after,
            )
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Daily discovery failed: {exc}")
        return 1
    finally:
        store.close()
    print(json.dumps([asdict(item) for item in stats], ensure_ascii=False))
    return 0


def main() -> None:
    raise SystemExit(run())
