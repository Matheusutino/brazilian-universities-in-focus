import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt
from tenacity.wait import wait_random_exponential

from ..shared.http_client import RetryableHttpStatus, raise_for_retryable_status
from ..shared.io import atomic_write_bytes
from ..sources.catalog import SiteConfig


@dataclass(slots=True)
class FetchResult:
    downloaded: int
    skipped: int
    manifest_records: list[dict[str, object]]
    errors: list[dict[str, object]]


class ContentFetcher:
    def __init__(
        self,
        site: SiteConfig,
        raw_directory: Path,
        *,
        concurrency: int = 10,
        timeout: float = 30.0,
        attempts: int = 5,
        retry_multiplier: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if concurrency < 1 or concurrency > 10:
            raise ValueError("Concurrency must be between 1 and 10")
        self.site = site
        self.raw_directory = raw_directory
        self.concurrency = concurrency
        self.timeout = timeout
        self.attempts = attempts
        self.retry_multiplier = retry_multiplier
        self.transport = transport

    @staticmethod
    def filename_for_url(url: str) -> str:
        return f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.html"

    def path_for_url(self, url: str) -> Path:
        return self.raw_directory / self.filename_for_url(url)

    async def fetch(self, urls: list[str], *, force: bool = False) -> FetchResult:
        self.raw_directory.mkdir(parents=True, exist_ok=True)
        semaphore = asyncio.Semaphore(self.concurrency)
        limits = httpx.Limits(
            max_connections=self.concurrency,
            max_keepalive_connections=self.concurrency,
        )
        headers = {"User-Agent": self.site.user_agent, "Accept": "text/html,*/*"}
        async with httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=self.timeout,
            limits=limits,
            transport=self.transport,
        ) as client:
            tasks = [self._fetch_one(client, semaphore, url, force) for url in urls]
            results = await asyncio.gather(*tasks)

        manifest_records: list[dict[str, object]] = []
        errors: list[dict[str, object]] = []
        downloaded = 0
        skipped = 0
        for status, record in results:
            if status == "downloaded":
                downloaded += 1
                manifest_records.append(record)
            elif status == "skipped":
                skipped += 1
            else:
                errors.append(record)
        return FetchResult(downloaded, skipped, manifest_records, errors)

    async def _fetch_one(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        url: str,
        force: bool,
    ) -> tuple[str, dict[str, object]]:
        destination = self.path_for_url(url)
        if not force and destination.is_file() and destination.stat().st_size > 0:
            return "skipped", {"url": url}

        try:
            async with semaphore:
                response = await self._download(client, url)
            await asyncio.to_thread(atomic_write_bytes, destination, response.content)
            return "downloaded", {
                "url": url,
                "file": f"raw/{destination.name}",
                "status_code": response.status_code,
                "fetched_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:  # One failed URL does not invalidate the batch.
            return "error", {
                "stage": "fetching",
                "url": url,
                "error": type(exc).__name__,
                "message": str(exc),
            }

    async def _download(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self.attempts),
            wait=wait_random_exponential(multiplier=self.retry_multiplier, max=8),
            retry=retry_if_exception_type((httpx.TransportError, RetryableHttpStatus)),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                response = await client.get(url)
                raise_for_retryable_status(response)
                return response
        raise RuntimeError("Retry completed without a result")  # pragma: no cover
