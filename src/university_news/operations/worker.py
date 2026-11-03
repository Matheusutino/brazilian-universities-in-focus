"""Durable stage workers for newly discovered articles."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import threading
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Self

import httpx
from dotenv import load_dotenv

from ..enrichment.workload import enrichment_workload
from ..paths import DEFAULT_DATA_ROOT
from ..settings import config_section
from ..enrichment.client import SUPPORTED_PROVIDERS, ClippingEnricher
from .handlers import StageHandlers, WorkerStageError, load_enricher
from .store import JOB_STAGES, PostgresStore


class LeaseHeartbeat:
    """Renew a running job lease while a slow remote call is in progress."""

    def __init__(
        self,
        store: PostgresStore,
        *,
        job_id: int,
        worker_id: str,
        lease_seconds: int,
        heartbeat_seconds: float,
    ) -> None:
        self.store = store
        self.job_id = job_id
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Self:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            try:
                renewed = self.store.renew_lease(
                    self.job_id,
                    self.worker_id,
                    self.lease_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"job={self.job_id} heartbeat failed: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                continue
            if not renewed:
                print(f"job={self.job_id} lease ownership was lost", flush=True)
                return


def is_transient(error: Exception) -> bool:
    if isinstance(error, WorkerStageError):
        return error.transient
    if isinstance(error, (httpx.TimeoutException, httpx.TransportError)):
        return True
    return type(error).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "RateLimitError",
        "RetryableHttpStatus",
    }


def work(
    store: PostgresStore,
    handlers: StageHandlers,
    *,
    stages: Sequence[str],
    worker_id: str,
    lease_seconds: int,
    max_attempts: int,
    poll_seconds: float,
    heartbeat_seconds: int,
    max_jobs: int | None,
    exit_when_empty: bool,
    run_id: uuid.UUID | None = None,
    stop: threading.Event | None = None,
    transient_backoff_seconds: float = 30.0,
) -> dict[str, int]:
    """Claim and process jobs until the queue empties, a limit, or a stop request.

    A stop request lets the current job finish, so no work is abandoned
    mid-request; leases cover hard crashes. After a transient error (an
    endpoint down or rate limited) the thread pauses before claiming again,
    doubling up to 10 minutes, so an unavailable provider does not burn the
    attempts of jobs that another provider's worker could complete.
    """
    stats = {"completed": 0, "failed": 0, "retried": 0}
    stop = stop or threading.Event()
    consecutive_transient = 0
    while not stop.is_set() and (max_jobs is None or sum(stats.values()) < max_jobs):
        job = store.claim_job(
            stages,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        if job is None:
            if exit_when_empty:
                break
            stop.wait(poll_seconds)
            continue
        print(
            f"job={job.id} site={job.site} stage={job.stage} "
            f"attempt={job.attempts} url={job.url}",
            flush=True,
        )
        try:
            with LeaseHeartbeat(
                store,
                job_id=job.id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                heartbeat_seconds=heartbeat_seconds,
            ):
                next_stages = handlers.process(job)
        except Exception as exc:  # noqa: BLE001
            transient = is_transient(exc)
            error_details = getattr(handlers, "error_details", None)
            store.fail_job(
                job,
                exc,
                transient=transient,
                max_attempts=max_attempts,
                run_id=run_id,
                details=error_details(job, exc) if error_details else None,
            )
            stats["retried" if transient and job.attempts < max_attempts else "failed"] += 1
            print(f"job={job.id} failed: {type(exc).__name__}: {exc}", flush=True)
            if transient:
                consecutive_transient += 1
                pause = min(
                    transient_backoff_seconds * 2 ** (consecutive_transient - 1), 600.0
                )
                print(f"pausing {pause:.0f}s after a transient error", flush=True)
                stop.wait(pause)
            else:
                consecutive_transient = 0
        else:
            store.complete_job(job, next_stages)
            stats["completed"] += 1
            consecutive_transient = 0
    return stats


def build_parser() -> argparse.ArgumentParser:
    settings = config_section("workers")
    parser = argparse.ArgumentParser(
        prog="university-news-worker",
        description="Process durable incremental university-news jobs.",
    )
    parser.add_argument("--stages", nargs="+", choices=JOB_STAGES, default=list(JOB_STAGES))
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--worker-id")
    parser.add_argument("--workers", type=int)
    parser.add_argument(
        "--provider",
        choices=sorted(SUPPORTED_PROVIDERS),
        help="Enrichment provider for this process (default: [enrichment].provider)",
    )
    parser.add_argument(
        "--lease-seconds", type=int, default=int(settings.get("lease_seconds", 600))
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=int,
        default=int(settings.get("heartbeat_seconds", 60)),
    )
    parser.add_argument(
        "--max-attempts", type=int, default=int(settings.get("max_attempts", 5))
    )
    parser.add_argument(
        "--poll-seconds", type=float, default=float(settings.get("poll_seconds", 5.0))
    )
    parser.add_argument("--max-jobs", type=int)
    parser.add_argument("--exit-when-empty", action="store_true")
    return parser


def configured_workers(stages: Sequence[str], provider: str | None = None) -> int:
    if tuple(stages) == ("enrich",):
        enrichment = config_section("enrichment")
        provider_settings = enrichment.get(provider or enrichment.get("provider"), {})
        return int(provider_settings.get("workers", enrichment.get("workers", 1)))
    workers = config_section("workers")
    if tuple(stages) == ("embed",):
        return int(workers.get("embedding", 1))
    if set(stages).issubset({"fetch", "extract", "preprocess"}):
        return int(workers.get("core", 1))
    return 1


def run_one_worker(
    dsn: str,
    args: argparse.Namespace,
    *,
    worker_id: str,
    run_id: uuid.UUID,
    enricher: ClippingEnricher | None,
    stop: threading.Event,
) -> dict[str, int]:
    store = PostgresStore.open(dsn)
    try:
        return work(
            store,
            StageHandlers(store, args.data_root, enricher=enricher, run_id=run_id),
            stages=args.stages,
            worker_id=worker_id,
            lease_seconds=args.lease_seconds,
            max_attempts=args.max_attempts,
            poll_seconds=args.poll_seconds,
            heartbeat_seconds=args.heartbeat_seconds,
            max_jobs=args.max_jobs,
            exit_when_empty=args.exit_when_empty,
            run_id=run_id,
            stop=stop,
        )
    finally:
        store.close()


def run(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.provider and "enrich" not in args.stages:
        parser.error("--provider applies only to the enrich stage")
    workers = args.workers or configured_workers(args.stages, args.provider)
    if (
        args.lease_seconds < 60
        or args.heartbeat_seconds < 5
        or args.heartbeat_seconds >= args.lease_seconds
        or args.max_attempts < 1
        or args.poll_seconds < 0.1
        or workers < 1
    ):
        parser.error("lease, attempts, or polling interval is outside its valid range")
    if args.max_jobs is not None and args.max_jobs < 1:
        parser.error("--max-jobs must be at least 1")
    if workers > 1 and args.max_jobs is not None:
        parser.error("--max-jobs currently requires --workers 1")
    dsn = os.getenv("DATABASE_URL", "").strip()
    if not dsn:
        print("Worker failed: DATABASE_URL is not set in .env")
        return 1
    worker_id = args.worker_id or f"{socket.gethostname()}:{os.getpid()}"
    enricher: ClippingEnricher | None = None
    config: dict[str, object] = {"stages": list(args.stages), "workers": workers}
    store = PostgresStore.open(dsn)
    try:
        store.initialize()
        if "enrich" in args.stages:
            enricher = load_enricher(args.provider)
            profile = enricher.profile.as_dict()
            store.register_enrichment_profile(profile)
            config["enrichment_profile"] = profile
            config["enrichment_endpoint"] = {
                "url": enricher.settings.base_url,
                "api_keys": len(enricher.settings.api_keys),
                "requests_per_minute_per_key": enricher.settings.requests_per_minute,
            }
            config["model_info"] = enricher.model_info()
            workload = enrichment_workload(
                store.stage_job_count("enrich"),
                workers=workers,
                measured_article_seconds=store.average_enrichment_seconds(
                    enricher.profile.id
                ),
            )
            print(
                json.dumps(
                    {"enrichment_profile": profile["id"], "enrichment_workload": workload.as_dict()}
                ),
                flush=True,
            )
        kind = f"worker:{'+'.join(args.stages)}"
        if enricher is not None:
            kind += f":{enricher.settings.provider}"
        run_id = store.start_run(kind, config=config, worker_id=worker_id)
    except Exception as exc:  # noqa: BLE001
        print(f"Worker failed: {exc}")
        store.close()
        return 1

    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        print(f"Received {signal.Signals(signum).name}; finishing current jobs", flush=True)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    print(f"run={run_id} worker={worker_id} workers={workers}", flush=True)
    stats = {"completed": 0, "failed": 0, "retried": 0}
    error: str | None = None
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    run_one_worker,
                    dsn,
                    args,
                    worker_id=worker_id if workers == 1 else f"{worker_id}:{number}",
                    run_id=run_id,
                    enricher=enricher,
                    stop=stop,
                )
                for number in range(1, workers + 1)
            ]
            for future in futures:
                result = future.result()
                stats = {key: stats[key] + result[key] for key in stats}
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        stop.set()
        print(f"Worker failed: {error}")
    finally:
        status = "failed" if error else "interrupted" if stop.is_set() else "completed"
        store.finish_run(run_id, stats=stats, error=error, status=status)
        store.close()
    print(json.dumps({"run_id": str(run_id), "status": status, **stats}))
    # A requested stop is a clean exit even if some jobs failed along the way;
    # failed jobs stay visible in `university-news-db errors`.
    if status == "interrupted":
        return 0
    return 0 if status != "failed" and stats["failed"] == 0 else 1


def main() -> None:
    raise SystemExit(run())
