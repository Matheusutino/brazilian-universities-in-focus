from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..pipeline.preprocessing import (
    PREPROCESSED_ARTICLES_FILENAME,
    REJECTED_ARTICLES_FILENAME,
    word_count,
)
from ..paths import reports_directory, sites_directory
from ..shared.io import atomic_write_json


DEFAULT_REPORT_FILENAME = "quality_report.json"
REJECTION_REASONS = (
    "missing_author",
    "empty_title",
    "empty_text",
    "short_title",
    "short_text",
    "non_portuguese",
)
COMPLETENESS_FIELDS = (
    "author",
    "title",
    "subtitle",
    "date",
    "text",
    "tags",
    "links",
    "images",
)


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Record is not an object at {path}:{line_number}")
            yield record


def is_present(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return value is not None


def length_distribution(values: list[int]) -> dict[str, int | float | None]:
    if not values:
        return {
            "min": None,
            "median": None,
            "mean": None,
            "p95": None,
            "max": None,
        }
    ordered = sorted(values)
    p95_index = round((len(ordered) - 1) * 0.95)
    return {
        "min": ordered[0],
        "median": statistics.median(ordered),
        "mean": round(statistics.fmean(ordered), 2),
        "p95": ordered[p95_index],
        "max": ordered[-1],
    }


def valid_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return None
    return parsed if parsed.year >= 1990 else None


def rejection_counts_from_stats(directory: Path) -> Counter[str]:
    path = directory / "preprocess_stats.json"
    if not path.is_file():
        return Counter()
    try:
        stats = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Counter()
    if not isinstance(stats, dict):
        return Counter()
    return Counter(
        {
            reason: int(stats.get(f"removed_{reason}", 0))
            for reason in REJECTION_REASONS
            if int(stats.get(f"removed_{reason}", 0)) > 0
        }
    )


def analyze_site(directory: Path) -> dict[str, Any]:
    source_path = directory / "articles.jsonl"
    accepted_path = directory / PREPROCESSED_ARTICLES_FILENAME
    rejected_path = directory / REJECTED_ARTICLES_FILENAME

    source_count = 0
    source_urls: set[str] = set()
    duplicate_urls = 0
    for article in iter_jsonl(source_path):
        source_count += 1
        url = article.get("url")
        if isinstance(url, str) and url:
            if url in source_urls:
                duplicate_urls += 1
            source_urls.add(url)

    accepted = 0
    completeness = Counter({field: 0 for field in COMPLETENESS_FIELDS})
    title_words: list[int] = []
    text_words: list[int] = []
    earliest: date | None = None
    latest: date | None = None
    invalid_dates = 0
    for article in iter_jsonl(accepted_path):
        accepted += 1
        for field in COMPLETENESS_FIELDS:
            if is_present(article.get(field)):
                completeness[field] += 1
        title = article.get("title")
        text = article.get("text")
        if isinstance(title, str):
            title_words.append(word_count(title))
        if isinstance(text, str):
            text_words.append(word_count(text))
        parsed_date = valid_date(article.get("date"))
        if parsed_date is None:
            invalid_dates += 1
        else:
            earliest = parsed_date if earliest is None else min(earliest, parsed_date)
            latest = parsed_date if latest is None else max(latest, parsed_date)

    rejection_counts: Counter[str] = Counter()
    rejected_records = 0
    for rejection in iter_jsonl(rejected_path):
        rejected_records += 1
        reason = rejection.get("reason")
        rejection_counts[str(reason)] += 1

    removed = max(source_count - accepted, 0)
    stats_rejection_counts = rejection_counts_from_stats(directory)
    rejection_counts_source = "rejected_records"
    if sum(stats_rejection_counts.values()) > sum(rejection_counts.values()):
        rejection_counts = stats_rejection_counts
        rejection_counts_source = "preprocessing_stats"
    categorized_removed = min(sum(rejection_counts.values()), removed)
    completeness_report = {
        field: {
            "count": completeness[field],
            "rate": round(completeness[field] / accepted, 4) if accepted else 0.0,
        }
        for field in COMPLETENESS_FIELDS
    }
    return {
        "site": directory.name,
        "source_articles": source_count,
        "accepted_articles": accepted,
        "removed_articles": removed,
        "acceptance_rate": round(accepted / source_count, 4) if source_count else 0.0,
        "duplicate_urls": duplicate_urls,
        "rejection_details_available": rejected_path.is_file(),
        "rejected_records": rejected_records,
        "categorized_removed": categorized_removed,
        "unclassified_removed": max(removed - categorized_removed, 0),
        "rejection_counts_source": rejection_counts_source,
        "rejection_reasons": {
            reason: rejection_counts.get(reason, 0) for reason in REJECTION_REASONS
        },
        "field_completeness": completeness_report,
        "title_words": length_distribution(title_words),
        "text_words": length_distribution(text_words),
        "dates": {
            "earliest": earliest.isoformat() if earliest else None,
            "latest": latest.isoformat() if latest else None,
            "missing_or_invalid": invalid_dates,
        },
    }


def discover_site_directories(
    data_root: Path,
    sites: Sequence[str] | None = None,
) -> list[Path]:
    root = sites_directory(data_root)
    if sites:
        return [root / site for site in sorted(set(sites))]
    if not root.is_dir():
        return []
    return sorted(
        directory
        for directory in root.iterdir()
        if directory.is_dir() and (directory / "articles.jsonl").is_file()
    )


def build_quality_report(
    data_root: Path,
    sites: Sequence[str] | None = None,
) -> dict[str, Any]:
    site_reports = [
        analyze_site(directory)
        for directory in discover_site_directories(data_root, sites)
        if (directory / "articles.jsonl").is_file()
    ]
    rejection_totals = {
        reason: sum(
            int(report["rejection_reasons"][reason]) for report in site_reports
        )
        for reason in REJECTION_REASONS
    }
    source = sum(int(report["source_articles"]) for report in site_reports)
    accepted = sum(int(report["accepted_articles"]) for report in site_reports)
    removed = sum(int(report["removed_articles"]) for report in site_reports)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "totals": {
            "sites": len(site_reports),
            "source_articles": source,
            "accepted_articles": accepted,
            "removed_articles": removed,
            "acceptance_rate": round(accepted / source, 4) if source else 0.0,
            "duplicate_urls": sum(
                int(report["duplicate_urls"]) for report in site_reports
            ),
            "unclassified_removed": sum(
                int(report["unclassified_removed"]) for report in site_reports
            ),
            "rejection_reasons": rejection_totals,
        },
        "sites": site_reports,
    }


def print_summary(report: dict[str, Any]) -> None:
    print(
        "SITE\tSOURCE\tACCEPTED\tREMOVED\tKEEP_RATE\tDUP_URLS"
        "\tSUBTITLE_RATE\tMEDIAN_TEXT_WORDS"
    )
    for site in report["sites"]:
        subtitle_rate = site["field_completeness"]["subtitle"]["rate"]
        median_words = site["text_words"]["median"]
        print(
            f"{str(site['site']).upper()}\t{site['source_articles']}"
            f"\t{site['accepted_articles']}\t{site['removed_articles']}"
            f"\t{float(site['acceptance_rate']):.1%}\t{site['duplicate_urls']}"
            f"\t{float(subtitle_rate):.1%}\t{median_words}"
        )
    totals = report["totals"]
    print(
        f"\nTOTAL: {totals['accepted_articles']}/{totals['source_articles']} accepted "
        f"({float(totals['acceptance_rate']):.1%}); "
        f"{totals['removed_articles']} removed"
    )
    print("REMOVAL REASONS:")
    for reason, count in totals["rejection_reasons"].items():
        print(f"  {reason}: {count}")
    if totals["unclassified_removed"]:
        print(
            "  unclassified: "
            f"{totals['unclassified_removed']} "
            "(run preprocessing again to generate rejection details)"
        )


def rejected_files(data_root: Path, site: str | None) -> list[Path]:
    directories = discover_site_directories(data_root, [site] if site else None)
    return [
        path
        for directory in directories
        if (path := directory / REJECTED_ARTICLES_FILENAME).is_file()
    ]


def show_rejected(
    data_root: Path,
    *,
    site: str | None,
    reason: str | None,
    limit: int,
    full: bool,
) -> int:
    shown = 0
    for path in rejected_files(data_root, site):
        for rejection in iter_jsonl(path):
            if reason and rejection.get("reason") != reason:
                continue
            if full:
                print(json.dumps(rejection, ensure_ascii=False))
            else:
                article = rejection.get("article")
                article = article if isinstance(article, dict) else {}
                print(
                    f"[{rejection.get('reason')}] "
                    f"{str(rejection.get('site', path.parent.name)).upper()} | "
                    f"{article.get('title') or '<no title>'} | "
                    f"{article.get('url') or '<no URL>'}"
                )
            shown += 1
            if shown >= limit:
                return shown
    return shown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="university-news-analyze",
        description="Analyze preprocessing quality and inspect rejected articles.",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
        help="Root directory containing site data (default: data)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary = subparsers.add_parser("summary", help="Generate a quality report")
    summary.add_argument("--sites", nargs="+")
    summary.add_argument(
        "--output",
        type=Path,
        help="Report destination (default: <data-root>/reports/quality_report.json)",
    )

    rejected = subparsers.add_parser(
        "rejected",
        help="List articles rejected during preprocessing",
    )
    rejected.add_argument("--site")
    rejected.add_argument("--reason", choices=REJECTION_REASONS)
    rejected.add_argument("--limit", type=int, default=20)
    rejected.add_argument(
        "--full",
        action="store_true",
        help="Print complete JSONL rejection records",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "summary":
        report = build_quality_report(args.data_root, args.sites)
        output = args.output or reports_directory(args.data_root) / DEFAULT_REPORT_FILENAME
        atomic_write_json(output, report)
        print_summary(report)
        print(f"\nReport written to {output}")
        return 0
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    shown = show_rejected(
        args.data_root,
        site=args.site,
        reason=args.reason,
        limit=args.limit,
        full=args.full,
    )
    if shown == 0:
        print("No matching rejected articles found.")
    return 0


def main() -> None:
    raise SystemExit(run())
