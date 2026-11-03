"""Incremental CLI for enriching preprocessed news with an LLM."""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from tqdm import tqdm

from ..paths import (
    DEFAULT_DATA_ROOT,
    clipping_enrichment_directory,
    sites_directory,
)
from ..pipeline.preprocessing import PREPROCESSED_ARTICLES_FILENAME
from ..settings import PROJECT_ROOT, config_section
from ..shared.io import append_jsonl, atomic_write_json
from .client import (
    ClippingEnricher,
    EnrichmentSettings,
    article_content_hash,
    article_id,
)
from .schema import DEFAULT_SCHEMA_PATH, ClippingSchema
from .workload import enrichment_workload


@dataclass(slots=True)
class EnrichmentStats:
    scanned: int = 0
    selected: int = 0
    enriched: int = 0
    skipped: int = 0
    errors: int = 0
    duration_seconds: float = 0.0
    api_requests: int = 0
    validation_retries: int = 0
    correction_duration_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    articles_with_token_usage: int = 0
    articles_with_complete_token_usage: int = 0
    workload: dict[str, int | float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EnrichmentTask:
    article: dict[str, Any]
    university: str
    signature: tuple[str, str, str, str, str, str]
    number: int


@dataclass(frozen=True, slots=True)
class EnrichmentOutcome:
    task: EnrichmentTask
    result: dict[str, Any] | None
    error: Exception | None
    duration_seconds: float
    metrics: dict[str, Any] | None


def build_parser() -> argparse.ArgumentParser:
    enrichment = config_section("enrichment")
    parser = argparse.ArgumentParser(
        prog="university-news-enrich",
        description="Enrich preprocessed university news through an LLM provider.",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=DEFAULT_DATA_ROOT,
        help="Data root containing sites/ (default: data)",
    )
    parser.add_argument(
        "--sites",
        nargs="+",
        help="Universities to enrich; defaults to every preprocessed site",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help="Clipping contract JSON",
    )
    parser.add_argument("--output", type=Path, help="Annotations JSONL destination")
    parser.add_argument("--errors-output", type=Path, help="Errors JSONL destination")
    parser.add_argument("--limit", type=int, help="Maximum new articles to request")
    parser.add_argument(
        "--workers",
        type=int,
        default=int(enrichment.get("workers", 1)),
        help="Maximum simultaneous LLM requests",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Request enrichment even when the same version already exists",
    )
    parser.add_argument(
        "--provider",
        choices=("openwebui", "nvidia_nim"),
        help="Override ENRICHMENT_PROVIDER",
    )
    parser.add_argument("--model", help="Override the selected provider model")
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--max-input-chars", type=int)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument(
        "--calls",
        type=int,
        choices=range(1, 3),
        metavar="1..2",
        help="Model calls used to fill one complete clipping JSON",
    )
    parser.add_argument(
        "--no-json-mode",
        action="store_true",
        help="Do not send response_format=json_object to the compatible API",
    )
    return parser


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise TypeError(f"Record is not an object at {path}:{line_number}")
            yield value


def source_files(data_root: Path, sites: Sequence[str] | None) -> list[Path]:
    root = sites_directory(data_root)
    if sites:
        return [
            root / site.lower() / PREPROCESSED_ARTICLES_FILENAME for site in sites
        ]
    if not root.is_dir():
        return []
    return sorted(root.glob(f"*/{PREPROCESSED_ARTICLES_FILENAME}"))


def completed_signatures(path: Path) -> set[tuple[str, str, str, str, str, str]]:
    if not path.is_file():
        return set()
    completed: set[tuple[str, str, str, str, str, str]] = set()
    for record in _iter_jsonl(path):
        values = (
            record.get("article_id"),
            record.get("content_hash"),
            record.get("schema_version"),
            record.get("model"),
            record.get("strategy"),
            record.get("provider") or "openwebui",
        )
        if all(isinstance(value, str) and value for value in values):
            completed.add(values)  # type: ignore[arg-type]
    return completed


def enrich_articles(
    files: Sequence[Path],
    *,
    enricher: ClippingEnricher,
    output_path: Path,
    errors_path: Path,
    manifest_path: Path,
    limit: int | None = None,
    force: bool = False,
    workers: int = 1,
) -> EnrichmentStats:
    if limit is not None and limit < 1:
        raise ValueError("--limit must be at least 1")
    if workers < 1:
        raise ValueError("--workers must be at least 1")
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Preprocessed article files not found: {', '.join(missing)}")

    known = set() if force else completed_signatures(output_path)
    stats = EnrichmentStats()
    started_at = datetime.now(UTC).isoformat()
    run_started = time.perf_counter()
    progress = tqdm(desc="Enriching", unit="articles", total=limit)
    progress_lock = threading.Lock()
    scheduled = set(known)
    pending: set[Future[EnrichmentOutcome]] = set()

    def consume(completed: set[Future[EnrichmentOutcome]]) -> None:
        for future in completed:
            outcome = future.result()
            task = outcome.task
            if outcome.error is not None:
                _record_error(
                    errors_path,
                    task.article,
                    task.university,
                    outcome.error,
                    provider=enricher.settings.provider,
                    model=enricher.settings.model,
                    duration_seconds=outcome.duration_seconds,
                    generation_metrics=outcome.metrics,
                )
                stats.errors += 1
                _accumulate_metrics(stats, outcome.metrics)
            else:
                assert outcome.result is not None
                append_jsonl(output_path, [outcome.result])
                known.add(task.signature)
                stats.enriched += 1
                _accumulate_metrics(
                    stats, outcome.result.get("generation_metrics")
                )
            with progress_lock:
                progress.update(1)
                progress.set_postfix(
                    enriched=stats.enriched,
                    errors=stats.errors,
                    skipped=stats.skipped,
                )

    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            reached_limit = False
            for path in files:
                university = path.parent.name.upper()
                for article in _iter_jsonl(path):
                    stats.scanned += 1
                    try:
                        signature = (
                            article_id(article),
                            article_content_hash(article),
                            enricher.schema.version,
                            enricher.settings.model,
                            enricher.strategy,
                            enricher.settings.provider,
                        )
                    except ValueError as exc:
                        _record_error(
                            errors_path,
                            article,
                            university,
                            exc,
                            provider=enricher.settings.provider,
                            model=enricher.settings.model,
                        )
                        stats.errors += 1
                        continue
                    if signature in scheduled:
                        stats.skipped += 1
                        continue
                    if limit is not None and stats.selected >= limit:
                        reached_limit = True
                        break

                    stats.selected += 1
                    scheduled.add(signature)
                    task = EnrichmentTask(
                        article=article,
                        university=university,
                        signature=signature,
                        number=stats.selected,
                    )
                    pending.add(
                        executor.submit(
                            _execute_enrichment,
                            enricher,
                            task,
                            progress,
                            progress_lock,
                        )
                    )
                    if len(pending) >= workers:
                        completed, pending = wait(
                            pending, return_when=FIRST_COMPLETED
                        )
                        consume(completed)
                if reached_limit:
                    break
            while pending:
                completed, pending = wait(pending, return_when=FIRST_COMPLETED)
                consume(completed)
    finally:
        progress.close()

    stats.duration_seconds = round(time.perf_counter() - run_started, 3)
    stats.workload = enrichment_workload(
        stats.selected,
        workers=workers,
        calls_per_article=enricher.settings.enrichment_calls,
        validation_attempts=enricher.settings.validation_attempts,
        transport_retries=enricher.settings.max_retries,
    ).as_dict()

    atomic_write_json(
        manifest_path,
        {
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "schema_version": enricher.schema.version,
            "schema_path": str(enricher.schema.path),
            "model": enricher.settings.model,
            "provider": enricher.settings.provider,
            "strategy": enricher.strategy,
            "llm_calls_per_article": enricher.settings.enrichment_calls,
            "output": str(output_path),
            "errors_output": str(errors_path),
            "sources": [str(path) for path in files],
            "force": force,
            "limit": limit,
            "workers": workers,
            "stats": asdict(stats),
        },
    )
    return stats


def _execute_enrichment(
    enricher: ClippingEnricher,
    task: EnrichmentTask,
    progress: tqdm,
    progress_lock: threading.Lock,
) -> EnrichmentOutcome:
    started = time.perf_counter()

    def show_request(call: int, total: int, attempt: int) -> None:
        with progress_lock:
            progress.set_description(
                f"{task.university} {task.number}: "
                f"call {call}/{total}, attempt {attempt}"
            )
            progress.refresh()

    try:
        result = enricher.enrich(
            task.article,
            university=task.university,
            on_request_start=show_request,
        )
    except Exception as exc:  # noqa: BLE001
        metrics = getattr(exc, "generation_metrics", None)
        return EnrichmentOutcome(
            task=task,
            result=None,
            error=exc,
            duration_seconds=time.perf_counter() - started,
            metrics=metrics if isinstance(metrics, dict) else None,
        )
    return EnrichmentOutcome(
        task=task,
        result=result,
        error=None,
        duration_seconds=time.perf_counter() - started,
        metrics=result.get("generation_metrics"),
    )


def _record_error(
    path: Path,
    article: dict[str, Any],
    university: str,
    error: Exception,
    *,
    provider: str | None = None,
    model: str | None = None,
    duration_seconds: float | None = None,
    generation_metrics: Any = None,
) -> None:
    append_jsonl(
        path,
        [
            {
                "occurred_at": datetime.now(UTC).isoformat(),
                "university": university,
                "url": article.get("url"),
                "provider": provider,
                "model": model,
                "error": type(error).__name__,
                "message": str(error),
                "duration_seconds": (
                    round(duration_seconds, 3)
                    if duration_seconds is not None
                    else None
                ),
                "generation_metrics": (
                    generation_metrics
                    if isinstance(generation_metrics, dict)
                    else None
                ),
            }
        ],
    )


def _accumulate_metrics(stats: EnrichmentStats, metrics: Any) -> None:
    if not isinstance(metrics, dict):
        return
    stats.api_requests += int(metrics.get("api_requests") or 0)
    stats.validation_retries += int(metrics.get("validation_retries") or 0)
    stats.correction_duration_seconds = round(
        stats.correction_duration_seconds
        + float(metrics.get("correction_duration_seconds") or 0.0),
        3,
    )
    if metrics.get("token_usage_available"):
        stats.articles_with_token_usage += 1
        stats.prompt_tokens += int(metrics.get("prompt_tokens") or 0)
        stats.completion_tokens += int(metrics.get("completion_tokens") or 0)
        stats.total_tokens += int(metrics.get("total_tokens") or 0)
    if metrics.get("token_usage_complete"):
        stats.articles_with_complete_token_usage += 1


def run(argv: Sequence[str] | None = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    try:
        schema = ClippingSchema.load(args.schema)
        overrides = {
            key: value
            for key, value in {
                "model": args.model,
                "provider": args.provider,
                "temperature": args.temperature,
                "max_input_chars": args.max_input_chars,
                "max_output_tokens": args.max_output_tokens,
                "enrichment_calls": args.calls,
                "json_mode": False if args.no_json_mode else None,
            }.items()
            if value is not None
        }
        settings = EnrichmentSettings.from_config(**overrides)
        enricher = ClippingEnricher(settings, schema)
        files = source_files(args.data_root, args.sites)
        if not files:
            raise FileNotFoundError("No preprocessed article files found")

        directory = clipping_enrichment_directory(args.data_root, schema.version)
        output_path = args.output or directory / "annotations.jsonl"
        errors_path = args.errors_output or directory / "errors.jsonl"
        stats = enrich_articles(
            files,
            enricher=enricher,
            output_path=output_path,
            errors_path=errors_path,
            manifest_path=directory / "manifest.json",
            limit=args.limit,
            force=args.force,
            workers=args.workers,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Enrichment failed: {exc}")
        return 1

    print(json.dumps(asdict(stats), ensure_ascii=False))
    return 0 if stats.errors == 0 else 1


def main() -> None:
    raise SystemExit(run())
