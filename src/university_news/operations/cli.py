"""Administrative commands for the durable incremental pipeline."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv

from ..enrichment.client import SUPPORTED_PROVIDERS
from ..settings import config_section
from ..paths import (
    DEFAULT_DATA_ROOT,
    combined_enrichments_directory,
    enrichment_export_directory,
    sites_directory,
)
from ..pipeline.preprocessing import (
    PREPROCESSED_ARTICLES_FILENAME,
    compile_articles,
)
from ..shared.io import atomic_write_jsonl, read_jsonl
from ..sources.catalog import SITES
from .enrichment_export import export_enrichments
from .handlers import load_enricher
from .store import JOB_STAGES, PostgresStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="university-news-db",
        description="Manage the PostgreSQL-backed incremental pipeline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create tables and register sources")

    bootstrap = subparsers.add_parser(
        "import-existing-data",
        aliases=["bootstrap"],
        help="Import existing articles without reprocessing",
    )
    bootstrap.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    bootstrap.add_argument("--sites", nargs="+", choices=sorted(SITES))

    status = subparsers.add_parser(
        "status", help="Show jobs, runs, errors, and enrichment progress"
    )
    status.add_argument(
        "--profile",
        action="append",
        help="Enrichment profile; repeatable (default: every configured provider)",
    )

    profiles = subparsers.add_parser(
        "profiles", help="List enrichment profiles and their coverage"
    )
    profiles.add_argument(
        "--full", action="store_true", help="Include the exact prompts and contract"
    )

    errors = subparsers.add_parser("errors", help="Show recent failures with details")
    errors.add_argument("--stage", choices=JOB_STAGES)
    errors.add_argument("--limit", type=int, default=20)

    retry = subparsers.add_parser("retry", help="Requeue failed jobs explicitly")
    retry.add_argument("--stage", choices=JOB_STAGES)

    backfill = subparsers.add_parser(
        "enqueue-backfill", help="Explicitly enqueue historical enrichment or indexing"
    )
    backfill.add_argument("--stage", required=True, choices=("enrich", "embed"))
    backfill.add_argument(
        "--limit", type=int, help="Maximum articles to queue (default: all missing)"
    )
    backfill.add_argument("--priority", type=int, default=10)
    backfill.add_argument(
        "--reannotate",
        action="store_true",
        help="Also requeue articles annotated by other profiles, to compare them",
    )
    backfill.add_argument(
        "--provider",
        choices=sorted(SUPPORTED_PROVIDERS),
        help="Profile that --reannotate targets (default: [enrichment].provider)",
    )

    export_enriched = subparsers.add_parser(
        "export-enrichments",
        help="Write one profile's annotations as JSONL, Parquet, and a manifest",
    )
    export_enriched.add_argument(
        "--profile",
        action="append",
        help="Profile ID; repeatable (default: every configured provider with annotations)",
    )
    export_enriched.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)

    export = subparsers.add_parser(
        "export", help="Atomically publish accepted PostgreSQL articles to data/"
    )
    export.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    return parser


def initialize(store: PostgresStore) -> list[int]:
    applied = store.initialize()
    for site in SITES.values():
        store.register_source(site.name, site.base_url)
    return applied


def configured_profile(store: PostgresStore, provider: str | None = None) -> dict[str, object]:
    """Register and return a provider's profile from settings.toml and .env."""
    profile = load_enricher(provider).profile.as_dict()
    store.register_enrichment_profile(profile)
    return profile


def configured_profiles(store: PostgresStore) -> tuple[list[str], dict[str, str]]:
    """Profiles of every provider in [enrichment].providers, and why any is missing."""
    enrichment = config_section("enrichment")
    providers = enrichment.get("providers") or [enrichment.get("provider", "openwebui")]
    profile_ids: list[str] = []
    unavailable: dict[str, str] = {}
    for provider in providers:
        try:
            profile_ids.append(str(configured_profile(store, provider)["id"]))
        except (TypeError, ValueError) as exc:
            unavailable[str(provider)] = str(exc)
    return profile_ids, unavailable


def resolve_profile_ids(store: PostgresStore, requested: list[str] | None) -> list[str]:
    if requested:
        known = {str(row["id"]) for row in store.enrichment_profiles()}
        unknown = sorted(set(requested) - known)
        if unknown:
            raise ValueError(f"Unknown enrichment profiles: {', '.join(unknown)}")
        return requested
    profile_ids, unavailable = configured_profiles(store)
    if not profile_ids:
        raise ValueError(f"No configured provider is usable: {unavailable}")
    return profile_ids


def bootstrap(
    store: PostgresStore, data_root: Path, requested_sites: Sequence[str] | None
) -> dict[str, dict[str, int]]:
    selected = requested_sites or sorted(SITES)
    counts: dict[str, dict[str, int]] = {}
    for site in selected:
        directory = sites_directory(data_root) / site
        known_urls = [
            str(record["url"])
            for record in read_jsonl(directory / "urls.jsonl")
            if isinstance(record.get("url"), str)
        ]
        records = read_jsonl(directory / PREPROCESSED_ARTICLES_FILENAME)
        counts[site] = {
            "known_urls": store.bootstrap_urls(site, known_urls),
            "accepted_articles": store.bootstrap_articles(site, records),
        }
    return counts


def export(store: PostgresStore, data_root: Path) -> dict[str, object]:
    if store.accepted_article_count() == 0:
        raise RuntimeError(
            "PostgreSQL has no accepted articles; bootstrap before publishing"
        )
    counts: dict[str, int] = {}
    for site in sorted(SITES):
        records = store.accepted_articles(site)
        if not records:
            continue
        target = sites_directory(data_root) / site / PREPROCESSED_ARTICLES_FILENAME
        atomic_write_jsonl(target, records)
        counts[site] = len(records)
    compiled = compile_articles(data_root)
    return {
        "sites": counts,
        "compiled_articles": compiled.articles,
        "compiled_output": compiled.output,
        "site_output": compiled.site_output,
    }


def run(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        print("Database command failed: DATABASE_URL is not set in .env")
        return 1
    if args.command in {"enqueue-backfill", "errors"} and args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    store = PostgresStore.open(dsn)
    try:
        applied = initialize(store)
        if args.command == "init":
            result: object = {
                "initialized": True,
                "sources": len(SITES),
                "applied_migrations": applied,
                "schema_version": store.schema_version(),
            }
        elif args.command in {"bootstrap", "import-existing-data"}:
            result = {"imported": bootstrap(store, args.data_root, args.sites)}
        elif args.command == "status":
            result = store.operational_status()
            result["schema_version"] = store.schema_version()
            try:
                profile_ids = resolve_profile_ids(store, args.profile)
            except (TypeError, ValueError) as exc:
                result["enrichment"] = {"unavailable": str(exc)}
            else:
                result["enrichment"] = store.enrichment_overview(profile_ids)
                if not args.profile:
                    result["enrichment"]["unavailable_providers"] = configured_profiles(
                        store
                    )[1]
        elif args.command == "profiles":
            result = {"profiles": store.enrichment_profiles(full=args.full)}
        elif args.command == "errors":
            result = {"errors": store.recent_errors(stage=args.stage, limit=args.limit)}
        elif args.command == "retry":
            result = {"requeued": store.retry_failed(args.stage)}
        elif args.command == "enqueue-backfill":
            if args.stage == "embed":
                result = {
                    "enqueued": store.enqueue_embedding_backfill(
                        limit=args.limit, priority=args.priority
                    )
                }
            else:
                profile_id = str(configured_profile(store, args.provider)["id"])
                queued = store.enqueue_enrichment_backfill(
                    profile_id,
                    limit=args.limit,
                    priority=args.priority,
                    reannotate=args.reannotate,
                )
                result = {
                    "reannotate_profile_id": profile_id if args.reannotate else None,
                    **queued,
                    "enrich_jobs_pending": store.stage_job_count("enrich"),
                }
        elif args.command == "export-enrichments":
            profiles = {str(row["id"]): row for row in store.enrichment_profiles(full=True)}
            profile_ids = resolve_profile_ids(store, args.profile)
            if not args.profile:
                profile_ids = [
                    profile for profile in profile_ids if profiles[profile]["annotations"]
                ]
            if not profile_ids:
                raise ValueError("No annotations to export")
            result = export_enrichments(
                store.iter_enrichments(profile_ids),
                profiles=json.loads(
                    json.dumps([profiles[profile] for profile in profile_ids], default=str)
                ),
                directory=(
                    enrichment_export_directory(args.data_root, profile_ids)
                    if args.profile
                    else combined_enrichments_directory(args.data_root)
                ),
            )
        else:
            result = export(store, args.data_root)
    except Exception as exc:  # noqa: BLE001
        print(f"Database command failed: {exc}")
        return 1
    finally:
        store.close()
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


def main() -> None:
    raise SystemExit(run())
