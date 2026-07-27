import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from lingua import Language, LanguageDetectorBuilder
from tqdm import tqdm

from ..paths import (
    compiled_articles_path,
    compiled_manifest_path,
    reports_directory,
    sites_directory,
)
from ..shared.io import (
    atomic_write_json,
    atomic_write_json_array,
    atomic_write_jsonl,
    read_jsonl,
)


REQUIRED_FIELDS = ("author", "title", "text")
MIN_TITLE_WORDS = 3
MIN_TEXT_WORDS = 10
PREPROCESSED_ARTICLES_FILENAME = "articles.preprocessed.jsonl"
REJECTED_ARTICLES_FILENAME = "articles.rejected.jsonl"
COMPILED_ARTICLES_FILENAME = "articles.json"
SITE_STATS_FILENAME = "preprocess_stats.json"
GLOBAL_STATS_FILENAME = "preprocess_stats.json"
ProgressReporter = Callable[[int, int, int, int], None]


class PortugueseDetector(Protocol):
    def is_portuguese(self, text: str) -> bool: ...


class LinguaPortugueseDetector:
    def __init__(self) -> None:
        self.detector = LanguageDetectorBuilder.from_all_languages().build()

    def is_portuguese(self, text: str) -> bool:
        return self.detector.detect_language_of(text) == Language.PORTUGUESE


def load_portuguese_detector() -> LinguaPortugueseDetector:
    return LinguaPortugueseDetector()


@dataclass(frozen=True, slots=True)
class PreprocessStats:
    site: str
    total: int
    kept: int
    removed_missing_author: int
    removed_empty_title: int
    removed_empty_text: int
    removed_short_title: int
    removed_short_text: int
    removed_non_portuguese: int

    @property
    def removed(self) -> int:
        return self.total - self.kept


@dataclass(frozen=True, slots=True)
class CompileStats:
    journals: int
    articles: int
    output: str
    manifest: str


def is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def word_count(value: str) -> int:
    return len(re.findall(r"\b\w+\b", value, flags=re.UNICODE))


def preprocess_records(
    records: list[dict[str, Any]],
    site: str,
    detector: PortugueseDetector,
    progress: ProgressReporter | None = None,
    workers: int = 8,
) -> tuple[list[dict[str, Any]], PreprocessStats]:
    valid_records, _, stats = preprocess_records_with_rejections(
        records,
        site,
        detector,
        progress,
        workers,
    )
    return valid_records, stats


def preprocess_records_with_rejections(
    records: list[dict[str, Any]],
    site: str,
    detector: PortugueseDetector,
    progress: ProgressReporter | None = None,
    workers: int = 8,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], PreprocessStats]:
    if workers < 1:
        raise ValueError("workers must be at least 1")

    valid_records: list[dict[str, Any]] = []
    rejected_records: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    missing_author = 0
    empty_title = 0
    empty_text = 0
    short_title = 0
    short_text = 0
    non_portuguese = 0

    total = len(records)
    if progress is not None:
        progress(0, total, 0, 0)

    def reject(record: dict[str, Any], reason: str) -> None:
        rejected_records.append(
            {
                "site": site,
                "reason": reason,
                "article": record,
            }
        )

    for record in records:
        if not is_nonempty_string(record.get("author")):
            missing_author += 1
            reject(record, "missing_author")
        elif not is_nonempty_string(record.get("title")):
            empty_title += 1
            reject(record, "empty_title")
        elif not is_nonempty_string(record.get("text")):
            empty_text += 1
            reject(record, "empty_text")
        elif word_count(str(record["title"])) < MIN_TITLE_WORDS:
            short_title += 1
            reject(record, "short_title")
        elif word_count(str(record["text"])) < MIN_TEXT_WORDS:
            short_text += 1
            reject(record, "short_text")
        else:
            candidates.append(record)

    removed = missing_author + empty_title + empty_text + short_title + short_text
    if progress is not None and removed:
        progress(removed, total, 0, removed)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        languages = executor.map(
            lambda record: detector.is_portuguese(str(record["text"])), candidates
        )
        for index, (record, is_portuguese) in enumerate(
            zip(candidates, languages), start=1
        ):
            if is_portuguese:
                valid_records.append(record)
            else:
                non_portuguese += 1
                reject(record, "non_portuguese")
            current = removed + index
            if progress is not None and (current % 100 == 0 or current == total):
                progress(current, total, len(valid_records), current - len(valid_records))

    return valid_records, rejected_records, PreprocessStats(
        site=site,
        total=len(records),
        kept=len(valid_records),
        removed_missing_author=missing_author,
        removed_empty_title=empty_title,
        removed_empty_text=empty_text,
        removed_short_title=short_title,
        removed_short_text=short_text,
        removed_non_portuguese=non_portuguese,
    )


def preprocess_file(
    path: Path,
    detector: PortugueseDetector,
    *,
    dry_run: bool = False,
    progress: ProgressReporter | None = None,
    workers: int = 8,
) -> PreprocessStats:
    records = read_jsonl(path)
    valid_records, rejected_records, stats = preprocess_records_with_rejections(
        records, path.parent.name, detector, progress, workers
    )
    if not dry_run:
        atomic_write_jsonl(path.parent / PREPROCESSED_ARTICLES_FILENAME, valid_records)
        atomic_write_jsonl(path.parent / REJECTED_ARTICLES_FILENAME, rejected_records)
    return stats


def write_site_stats(path: Path, stats: PreprocessStats) -> None:
    atomic_write_json(path.parent / SITE_STATS_FILENAME, asdict(stats) | {"removed": stats.removed})


def load_site_stats(data_root: Path) -> list[PreprocessStats]:
    root = sites_directory(data_root)
    if not root.is_dir():
        return []
    results: list[PreprocessStats] = []
    field_names = tuple(PreprocessStats.__dataclass_fields__)
    for path in sorted(root.glob(f"*/{SITE_STATS_FILENAME}")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            results.append(
                PreprocessStats(**{name: record[name] for name in field_names})
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return results


def write_global_stats(
    data_root: Path,
    site_stats: list[PreprocessStats],
    compiled: CompileStats,
) -> None:
    persisted_stats = load_site_stats(data_root)
    if persisted_stats:
        site_stats = persisted_stats
    fields = (
        "total",
        "kept",
        "removed_missing_author",
        "removed_empty_title",
        "removed_empty_text",
        "removed_short_title",
        "removed_short_text",
        "removed_non_portuguese",
        "removed",
    )
    totals = {
        field: sum(getattr(stats, field) for stats in site_stats)
        for field in fields
    }
    atomic_write_json(
        reports_directory(data_root) / "preprocessing.json",
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "totals": totals,
            "sites": [asdict(stats) | {"removed": stats.removed} for stats in site_stats],
            "compiled": asdict(compiled),
        },
    )


def article_files(data_root: Path, sites: Sequence[str] | None) -> list[Path]:
    root = sites_directory(data_root)
    if sites:
        return [root / site / "articles.jsonl" for site in sites]
    if not root.is_dir():
        return []
    return sorted(
        directory / "articles.jsonl"
        for directory in root.iterdir()
        if directory.is_dir() and (directory / "articles.jsonl").is_file()
    )


def preprocessed_article_files(data_root: Path) -> list[Path]:
    root = sites_directory(data_root)
    if not root.is_dir():
        return []
    return sorted(
        directory / PREPROCESSED_ARTICLES_FILENAME
        for directory in root.iterdir()
        if directory.is_dir()
        and (directory / PREPROCESSED_ARTICLES_FILENAME).is_file()
    )


def _iter_jsonl(path: Path):
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


def _count_jsonl(path: Path) -> int:
    return sum(1 for _ in _iter_jsonl(path))


def _relative_path(path: Path, data_root: Path) -> str:
    try:
        return str(path.relative_to(data_root))
    except ValueError:
        return str(path)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _preprocessing_rejection_counts(directory: Path) -> Counter[str]:
    path = directory / SITE_STATS_FILENAME
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
            for reason in (
                "missing_author",
                "empty_title",
                "empty_text",
                "short_title",
                "short_text",
                "non_portuguese",
            )
            if int(stats.get(f"removed_{reason}", 0)) > 0
        }
    )


def _site_manifest(directory: Path, data_root: Path) -> dict[str, Any]:
    discovered_count = _count_jsonl(directory / "urls.jsonl")
    downloaded_count = _count_jsonl(directory / "manifest.jsonl")
    extracted_count = _count_jsonl(directory / "articles.jsonl")
    raw_directory = directory / "raw"
    raw_html_count = (
        sum(1 for path in raw_directory.iterdir() if path.is_file())
        if raw_directory.is_dir()
        else 0
    )
    error_stages: Counter[str] = Counter()
    error_types: Counter[str] = Counter()
    error_count = 0
    for error in _iter_jsonl(directory / "errors.jsonl"):
        error_count += 1
        stage = str(error.get("stage") or "unknown")
        error_stages[stage] += 1
        error_types[str(error.get("error") or error.get("type") or "unknown")] += 1
    rejection_reasons: Counter[str] = Counter()
    rejected_count = 0
    for rejection in _iter_jsonl(directory / REJECTED_ARTICLES_FILENAME):
        rejected_count += 1
        reason = str(rejection.get("reason") or "unknown")
        rejection_reasons[reason] += 1
    stats_rejection_reasons = _preprocessing_rejection_counts(directory)
    if sum(stats_rejection_reasons.values()) > sum(rejection_reasons.values()):
        rejection_reasons = stats_rejection_reasons

    parsed_dates: list[date] = []
    missing_or_invalid_dates = 0
    accepted_count = 0
    completeness_counts = {
        field: 0
        for field in (
            "author",
            "title",
            "subtitle",
            "date",
            "text",
            "tags",
            "links",
            "images",
        )
    }
    for article in _iter_jsonl(directory / PREPROCESSED_ARTICLES_FILENAME):
        accepted_count += 1
        for field in completeness_counts:
            value = article.get(field)
            if (
                bool(value)
                if not isinstance(value, str)
                else bool(value.strip())
            ):
                completeness_counts[field] += 1
        value = article.get("date")
        try:
            parsed = date.fromisoformat(str(value)[:10])
        except ValueError:
            missing_or_invalid_dates += 1
            continue
        if parsed.year < 1990:
            missing_or_invalid_dates += 1
        else:
            parsed_dates.append(parsed)

    removed_count = max(extracted_count - accepted_count, 0)
    categorized_rejections = min(sum(rejection_reasons.values()), removed_count)
    return {
        "site": directory.name,
        "discovered_urls": discovered_count,
        "downloaded_pages": downloaded_count,
        "raw_html_files": raw_html_count,
        "extracted_articles": extracted_count,
        "accepted_articles": accepted_count,
        "removed_articles": removed_count,
        "acceptance_rate": (
            round(accepted_count / extracted_count, 4) if extracted_count else 0.0
        ),
        "errors": {
            "total": error_count,
            "by_stage": dict(sorted(error_stages.items())),
            "by_type": dict(sorted(error_types.items())),
        },
        "rejections": {
            "total": removed_count,
            "categorized": categorized_rejections,
            "detailed_records": rejected_count,
            "unclassified": max(removed_count - categorized_rejections, 0),
            "by_reason": dict(sorted(rejection_reasons.items())),
        },
        "field_completeness": {
            field: {
                "count": count,
                "rate": (
                    round(count / accepted_count, 4)
                    if accepted_count
                    else 0.0
                ),
            }
            for field, count in completeness_counts.items()
        },
        "dates": {
            "earliest": min(parsed_dates).isoformat() if parsed_dates else None,
            "latest": max(parsed_dates).isoformat() if parsed_dates else None,
            "missing_or_invalid": missing_or_invalid_dates,
        },
        "artifacts": {
            name: _relative_path(directory / filename, data_root)
            for name, filename in {
                "discovered_urls": "urls.jsonl",
                "download_manifest": "manifest.jsonl",
                "raw_html": "raw",
                "extracted_articles": "articles.jsonl",
                "accepted_articles": PREPROCESSED_ARTICLES_FILENAME,
                "rejected_articles": REJECTED_ARTICLES_FILENAME,
                "errors": "errors.jsonl",
                "preprocessing_stats": SITE_STATS_FILENAME,
            }.items()
            if (directory / filename).exists()
        },
    }


def write_compiled_manifest(
    data_root: Path,
    *,
    output_path: Path,
    articles: int,
) -> Path:
    site_reports = [
        _site_manifest(path.parent, data_root)
        for path in preprocessed_article_files(data_root)
    ]
    error_stages: Counter[str] = Counter()
    error_types: Counter[str] = Counter()
    rejection_reasons: Counter[str] = Counter()
    for site in site_reports:
        for stage, count in site["errors"]["by_stage"].items():
            error_stages[stage] += int(count)
        for error_type, count in site["errors"]["by_type"].items():
            error_types[error_type] += int(count)
        for reason, count in site["rejections"]["by_reason"].items():
            rejection_reasons[reason] += int(count)
    valid_earliest_dates = [
        site["dates"]["earliest"] for site in site_reports if site["dates"]["earliest"]
    ]
    valid_latest_dates = [
        site["dates"]["latest"] for site in site_reports if site["dates"]["latest"]
    ]
    path = compiled_manifest_path(data_root)
    atomic_write_json(
        path,
        {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset": {
                "path": _relative_path(output_path, data_root),
                "format": "json",
                "articles": articles,
                "universities": len(site_reports),
                "bytes": output_path.stat().st_size,
                "sha256": _file_sha256(output_path),
                "source_pattern": _relative_path(
                    sites_directory(data_root) / "*" / PREPROCESSED_ARTICLES_FILENAME,
                    data_root,
                ),
            },
            "preprocessing": {
                "language": "Portuguese",
                "required_fields": list(REQUIRED_FIELDS),
                "minimum_title_words": MIN_TITLE_WORDS,
                "minimum_text_words": MIN_TEXT_WORDS,
                "rejection_priority": [
                    "missing_author",
                    "empty_title",
                    "empty_text",
                    "short_title",
                    "short_text",
                    "non_portuguese",
                ],
            },
            "totals": {
                "discovered_urls": sum(
                    int(site["discovered_urls"]) for site in site_reports
                ),
                "downloaded_pages": sum(
                    int(site["downloaded_pages"]) for site in site_reports
                ),
                "raw_html_files": sum(
                    int(site["raw_html_files"]) for site in site_reports
                ),
                "extracted_articles": sum(
                    int(site["extracted_articles"]) for site in site_reports
                ),
                "accepted_articles": sum(
                    int(site["accepted_articles"]) for site in site_reports
                ),
                "removed_articles": sum(
                    int(site["removed_articles"]) for site in site_reports
                ),
                "errors": {
                    "total": sum(
                        int(site["errors"]["total"]) for site in site_reports
                    ),
                    "by_stage": dict(sorted(error_stages.items())),
                    "by_type": dict(sorted(error_types.items())),
                },
                "rejections": {
                    "total": sum(
                        int(site["rejections"]["total"]) for site in site_reports
                    ),
                    "categorized": sum(
                        int(site["rejections"]["categorized"])
                        for site in site_reports
                    ),
                    "detailed_records": sum(
                        int(site["rejections"]["detailed_records"])
                        for site in site_reports
                    ),
                    "unclassified": sum(
                        int(site["rejections"]["unclassified"])
                        for site in site_reports
                    ),
                    "by_reason": dict(sorted(rejection_reasons.items())),
                },
                "dates": {
                    "earliest": min(valid_earliest_dates)
                    if valid_earliest_dates
                    else None,
                    "latest": max(valid_latest_dates)
                    if valid_latest_dates
                    else None,
                    "missing_or_invalid": sum(
                        int(site["dates"]["missing_or_invalid"])
                        for site in site_reports
                    ),
                },
            },
            "universities": site_reports,
        },
    )
    return path


def compile_articles(
    data_root: Path,
    *,
    output_path: Path | None = None,
) -> CompileStats:
    output_path = output_path or compiled_articles_path(data_root)
    sources = preprocessed_article_files(data_root)

    def compiled_records():
        for path in sources:
            journal = path.parent.name.upper()
            for record in _iter_jsonl(path):
                yield record | {"journal": journal}

    articles = atomic_write_json_array(output_path, compiled_records())
    manifest_path = write_compiled_manifest(
        data_root,
        output_path=output_path,
        articles=articles,
    )
    return CompileStats(
        journals=len(sources),
        articles=articles,
        output=str(output_path),
        manifest=str(manifest_path),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="university-news-preprocess",
        description="Remove incomplete or short records from articles.jsonl.",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
        help="Data root containing sites/, compiled/, and reports/ (default: data)",
    )
    parser.add_argument(
        "--sites",
        nargs="+",
        help="Sites to process; defaults to every site with articles.jsonl",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report removals without writing files",
    )
    parser.add_argument(
        "--no-compile",
        action="store_true",
        help="Do not update the consolidated articles.json file",
    )
    parser.add_argument(
        "--compiled-output",
        type=Path,
        help="Consolidated JSON destination (default: <data-root>/compiled/articles.json)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Concurrent language classifications per university (default: 8)",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    files = article_files(args.data_root, args.sites)
    if not files:
        print("No articles.jsonl files found.")
        return 0

    try:
        detector = load_portuguese_detector()
    except (OSError, RuntimeError) as exc:
        print(f"Could not load the language detector: {exc}")
        return 1

    had_errors = False
    completed_stats: list[PreprocessStats] = []
    for path in files:
        if not path.is_file():
            print(f"{path.parent.name}: articles.jsonl not found")
            had_errors = True
            continue
        try:
            print(f"{path.parent.name}: preprocessing", flush=True)
            with tqdm(
                desc=path.parent.name.upper(),
                unit="articles",
                dynamic_ncols=True,
            ) as progress_bar:
                def report_progress(
                    current: int,
                    total: int,
                    kept: int,
                    removed: int,
                ) -> None:
                    progress_bar.total = total
                    progress_bar.update(current - progress_bar.n)
                    progress_bar.set_postfix(kept=kept, removed=removed)
                    progress_bar.refresh()

                stats = preprocess_file(
                    path,
                    detector,
                    dry_run=args.dry_run,
                    progress=report_progress,
                    workers=args.workers,
                )
        except (OSError, ValueError) as exc:
            print(f"{path.parent.name}: error: {exc}")
            had_errors = True
            continue
        print(json.dumps(asdict(stats) | {"removed": stats.removed}, ensure_ascii=False))
        completed_stats.append(stats)
        if not args.dry_run:
            write_site_stats(path, stats)
    if had_errors or args.dry_run or args.no_compile:
        return 1 if had_errors else 0
    try:
        compiled = compile_articles(args.data_root, output_path=args.compiled_output)
    except (OSError, ValueError) as exc:
        print(f"Could not compile articles: {exc}")
        return 1
    print(json.dumps(asdict(compiled), ensure_ascii=False))
    write_global_stats(args.data_root, completed_stats, compiled)
    return 1 if had_errors else 0


def run_compile(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="university-news-compile",
        description="Compile preprocessed articles into a single JSON file.",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data"),
        help="Data root containing sites/, compiled/, and reports/ (default: data)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Consolidated JSON destination (default: <data-root>/compiled/articles.json)",
    )
    args = parser.parse_args(argv)
    try:
        stats = compile_articles(args.data_root, output_path=args.output)
    except (OSError, ValueError) as exc:
        print(f"Could not compile articles: {exc}")
        return 1
    write_global_stats(args.data_root, load_site_stats(args.data_root), stats)
    print(json.dumps(asdict(stats), ensure_ascii=False))
    return 0


def main() -> None:
    raise SystemExit(run())


def compile_main() -> None:
    raise SystemExit(run_compile())
