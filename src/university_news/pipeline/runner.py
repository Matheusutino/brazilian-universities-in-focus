import argparse
import asyncio
import contextlib
import json
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from ..paths import DEFAULT_DATA_ROOT, logs_directory, sites_directory
from ..sources.catalog import SITES


PipelineStage = str
ExtractorMode = str
SubprocessFactory = Callable[..., Awaitable[asyncio.subprocess.Process]]


@dataclass(frozen=True, slots=True)
class SiteRun:
    site: str
    command: list[str]
    log_path: Path
    returncode: int
    elapsed_seconds: float


@dataclass(slots=True)
class ProgressTracker:
    total: int
    running: dict[str, float]
    completed: int = 0
    failed: int = 0

    def mark_started(self, site: str) -> None:
        self.running[site] = time.monotonic()

    def mark_finished(self, site: str, returncode: int) -> None:
        self.running.pop(site, None)
        self.completed += 1
        if returncode != 0:
            self.failed += 1

    def summary(self) -> str:
        queued = self.total - self.completed - len(self.running)
        running = ", ".join(
            f"{site} {time.monotonic() - started_at:.0f}s"
            for site, started_at in sorted(self.running.items())
        )
        details = f"; running: {running}" if running else ""
        return (
            f"progress: {self.completed}/{self.total} completed, "
            f"{len(self.running)} running, {queued} queued, {self.failed} failed"
            f"{details}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="university-news-all",
        description="Run multiple university news pipelines concurrently.",
    )
    parser.add_argument(
        "--sites",
        nargs="+",
        choices=sorted(SITES),
        help="Sites to run (default: all)",
    )
    parser.add_argument(
        "--stage",
        choices=("discovery", "fetching", "extraction", "all"),
        default="all",
        help="Pipeline stage to run for each site (default: all)",
    )
    parser.add_argument(
        "--extractor",
        choices=("trafilatura", "llm", "hybrid"),
        default="trafilatura",
        help="Extraction backend (default: trafilatura)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=sites_directory(DEFAULT_DATA_ROOT),
        help="Site output root (default: data/sites)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=3,
        help="Maximum concurrent pipelines (default: 3)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        choices=range(1, 11),
        metavar="1..10",
        help="Concurrent requests within each pipeline (default: 10)",
    )
    parser.add_argument("--force", action="store_true", help="Run the selected stage again")
    parser.add_argument("--verbose", action="store_true", help="Enable detailed logs")
    parser.add_argument(
        "--no-live-output",
        action="store_true",
        help="Do not mirror pipeline logs to the terminal",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=30.0,
        help="Seconds between progress summaries; 0 disables them (default: 30)",
    )
    parser.add_argument(
        "--failed-only",
        action="store_true",
        help="Only run sites with a non-empty errors.jsonl file",
    )
    return parser


def select_sites(
    requested_sites: Sequence[str] | None,
    *,
    output_root: Path,
    failed_only: bool,
) -> list[str]:
    sites = sorted(requested_sites or SITES)
    if not failed_only:
        return sites
    return [
        site
        for site in sites
        if failed_stages(site, output_root=output_root)
    ]


def failed_stages(site: str, *, output_root: Path) -> set[str]:
    """Return failed stages from the site's most recent known run."""
    site_directory = output_root / site
    stages: set[str] = set()
    errors_path = site_directory / "errors.jsonl"
    if errors_path.is_file():
        try:
            for line in errors_path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stage = record.get("stage") if isinstance(record, dict) else None
                if stage in {"discovery", "fetching", "extraction"}:
                    stages.add(stage)
        except OSError:
            pass

    # If execution stopped before errors.jsonl was written, the JSON summary in
    # the log can still identify the failed stage.
    if stages:
        return stages
    log_path = logs_directory(output_root.parent) / f"{site}.log"
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return stages
    for line in reversed(lines):
        candidate = line[line.find("{") :] if "{" in line else ""
        if not candidate:
            continue
        try:
            summary = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(summary, dict):
            continue
        if summary.get("discovery_errors", 0):
            stages.add("discovery")
        if summary.get("fetch_errors", 0):
            stages.add("fetching")
        if summary.get("extraction_errors", 0):
            stages.add("extraction")
        break
    return stages


def select_failed_runs(
    requested_sites: Sequence[str] | None,
    *,
    output_root: Path,
    requested_stage: PipelineStage,
) -> list[tuple[str, PipelineStage]]:
    """Select sites and the earliest stage required for each retry."""
    sites = sorted(requested_sites or SITES)
    selected: list[tuple[str, PipelineStage]] = []
    for site in sites:
        stages = failed_stages(site, output_root=output_root)
        if requested_stage != "all":
            if requested_stage in stages:
                selected.append((site, requested_stage))
            continue
        if not stages:
            continue
        if stages == {"fetching"}:
            stage = "fetching"
        elif stages == {"extraction"}:
            stage = "extraction"
        else:
            stage = "all"
        selected.append((site, stage))
    return selected


def build_command(
    site: str,
    *,
    python_executable: str,
    stage: PipelineStage,
    extractor: ExtractorMode,
    output_root: Path,
    concurrency: int,
    force: bool,
    verbose: bool,
) -> list[str]:
    command = [
        python_executable,
        "-m",
        "university_news",
        site,
        "--stage",
        stage,
        "--extractor",
        extractor,
        "--output",
        str(output_root / site),
        "--concurrency",
        str(concurrency),
    ]
    if force:
        command.append("--force")
    if verbose:
        command.append("--verbose")
    return command


async def run_site(
    site: str,
    command: list[str],
    log_path: Path,
    semaphore: asyncio.Semaphore,
    subprocess_factory: SubprocessFactory,
    *,
    live_output: bool,
    tracker: ProgressTracker | None = None,
) -> SiteRun:
    async with semaphore:
        start = time.monotonic()
        if tracker is not None:
            tracker.mark_started(site)
        print(f"{site}: iniciado (log: {log_path})", flush=True)
        with log_path.open("w", encoding="utf-8") as log_file:
            log_file.write(f"$ {' '.join(command)}\n\n")
            log_file.flush()
            if live_output:
                process = await subprocess_factory(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                await stream_output(site, process, log_file)
                returncode = await process.wait()
            else:
                process = await subprocess_factory(
                    *command,
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                )
                returncode = await process.wait()
        if tracker is not None:
            tracker.mark_finished(site, returncode)
    return SiteRun(
        site=site,
        command=command,
        log_path=log_path,
        returncode=returncode,
        elapsed_seconds=time.monotonic() - start,
    )


async def report_progress(tracker: ProgressTracker, interval: float) -> None:
    if interval <= 0:
        return
    while tracker.completed < tracker.total:
        await asyncio.sleep(interval)
        if tracker.completed < tracker.total:
            print(tracker.summary(), flush=True)


async def stream_output(
    site: str,
    process: asyncio.subprocess.Process,
    log_file,
) -> None:
    if process.stdout is None:
        return

    while line := await process.stdout.readline():
        text = line.decode("utf-8", errors="replace")
        log_file.write(text)
        log_file.flush()
        print(f"[{site}] {text}", end="", flush=True)


async def run_all(
    args: argparse.Namespace,
    *,
    subprocess_factory: SubprocessFactory = asyncio.create_subprocess_exec,
    python_executable: str = sys.executable,
) -> list[SiteRun]:
    if args.jobs < 1:
        raise ValueError("--jobs must be at least 1")

    args.output_root.mkdir(parents=True, exist_ok=True)
    if args.failed_only:
        selected_runs = select_failed_runs(
            args.sites,
            output_root=args.output_root,
            requested_stage=args.stage,
        )
    else:
        selected_runs = [(site, args.stage) for site in sorted(args.sites or SITES)]
    if not selected_runs:
        print("Nenhum site para executar.", flush=True)
        return []

    if args.failed_only:
        details = ", ".join(f"{site} ({stage})" for site, stage in selected_runs)
        print(f"Retomadas necessarias: {details}", flush=True)

    log_dir = logs_directory(args.output_root.parent)
    log_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(args.jobs)
    tracker = ProgressTracker(total=len(selected_runs), running={})
    progress_task = asyncio.create_task(
        report_progress(tracker, args.progress_interval)
    )

    tasks = []
    for site, stage in selected_runs:
        command = build_command(
            site,
            python_executable=python_executable,
            stage=stage,
            extractor=args.extractor,
            output_root=args.output_root,
            concurrency=args.concurrency,
            force=args.force,
            verbose=args.verbose,
        )
        tasks.append(
            asyncio.create_task(
                run_site(
                    site,
                    command,
                    log_dir / f"{site}.log",
                    semaphore,
                    subprocess_factory,
                    live_output=not args.no_live_output,
                    tracker=tracker,
                )
            )
        )

    results: list[SiteRun] = []
    try:
        for task in asyncio.as_completed(tasks):
            result = await task
            results.append(result)
            status = "ok" if result.returncode == 0 else f"error {result.returncode}"
            print(
                f"{result.site}: {status} "
                f"({result.elapsed_seconds:.1f}s, log: {result.log_path})",
                flush=True,
            )
    finally:
        progress_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await progress_task
    return sorted(results, key=lambda result: result.site)


def run(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        results = asyncio.run(run_all(args))
    except ValueError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        with contextlib.suppress(Exception):
            print("Execution interrupted by the user.", file=sys.stderr)
        return 130

    failed = [result for result in results if result.returncode != 0]
    if failed:
        failed_sites = ", ".join(result.site for result in failed)
        print(f"Failed pipelines: {failed_sites}", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    raise SystemExit(run())
