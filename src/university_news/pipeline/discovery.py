import asyncio
from dataclasses import dataclass
from datetime import date
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt
from tenacity.wait import wait_random_exponential

from ..shared.http_client import RetryableHttpStatus, raise_for_retryable_status, tls_context
from ..sources.catalog import SiteConfig


@dataclass(slots=True)
class DiscoveryResult:
    urls: set[str]
    errors: list[dict[str, object]]
    # True when a WordPress listing has no more pages.
    exhausted: bool = False


class UrlDiscovery:
    def __init__(
        self,
        site: SiteConfig,
        *,
        concurrency: int = 10,
        timeout: float = 30.0,
        attempts: int = 5,
        retry_multiplier: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("Concurrency must be greater than zero")
        self.site = site
        self.concurrency = concurrency
        self.timeout = timeout
        self.attempts = attempts
        self.retry_multiplier = retry_multiplier
        self.transport = transport

    async def discover(self, start_page: int, end_page: int) -> DiscoveryResult:
        page_values = self.site.page_values(start_page, end_page)

        semaphore = asyncio.Semaphore(self.concurrency)
        limits = httpx.Limits(
            max_connections=self.concurrency,
            max_keepalive_connections=self.concurrency,
        )
        headers = {"User-Agent": self.site.user_agent, "Accept": "text/html,*/*"}
        async with httpx.AsyncClient(
            verify=tls_context(),
            headers=headers,
            follow_redirects=True,
            timeout=self.timeout,
            limits=limits,
            transport=self.transport,
        ) as client:
            tasks = [
                self._discover_page(client, semaphore, page) for page in page_values
            ]
            page_results = await asyncio.gather(*tasks)

        urls: set[str] = set()
        errors: list[dict[str, object]] = []
        for page_urls, error in page_results:
            urls.update(page_urls)
            if error is not None:
                errors.append(error)
        return DiscoveryResult(urls=urls, errors=errors)

    async def discover_wordpress(
        self, rest_base: str, page: int, *, after: date | None = None
    ) -> DiscoveryResult:
        """One page (up to 100 articles) of the site's WordPress REST API."""
        url = self.site.wordpress_page_url(rest_base, page, after=after)
        headers = {"User-Agent": self.site.user_agent, "Accept": "application/json"}
        async with httpx.AsyncClient(
            verify=tls_context(),
            headers=headers,
            follow_redirects=True,
            timeout=self.timeout,
            transport=self.transport,
        ) as client:
            try:
                response = await self._get(client, url)
            except httpx.HTTPStatusError as exc:
                # WordPress answers 400 past the last page.
                if exc.response.status_code == 400 and "invalid_page_number" in exc.response.text:
                    return DiscoveryResult(urls=set(), errors=[], exhausted=True)
                return DiscoveryResult(urls=set(), errors=[self._error(page, url, exc)])
            except Exception as exc:  # noqa: BLE001 - reported like an HTML page failure
                return DiscoveryResult(urls=set(), errors=[self._error(page, url, exc)])
        try:
            items = response.json()
        except ValueError as exc:
            return DiscoveryResult(urls=set(), errors=[self._error(page, url, exc)])
        urls = {
            normalized
            for item in items
            if isinstance(item, dict)
            and (normalized := self._normalize(str(item.get("link") or ""), url))
        }
        total_pages = int(response.headers.get("x-wp-totalpages") or 0)
        return DiscoveryResult(urls=urls, errors=[], exhausted=not items or page >= total_pages)

    @staticmethod
    def _error(page: int, url: str, exc: Exception) -> dict[str, object]:
        return {
            "stage": "discovery",
            "page": page,
            "url": url,
            "error": type(exc).__name__,
            "message": str(exc),
        }

    async def _discover_page(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        page: int,
    ) -> tuple[set[str], dict[str, object] | None]:
        listing_url = self.site.page_url(page)
        try:
            async with semaphore:
                html = await self._download(client, listing_url)
            return self.parse_urls(html, listing_url), None
        except Exception as exc:  # One failed page does not invalidate the batch.
            return set(), {
                "stage": "discovery",
                "page": page,
                "url": listing_url,
                "error": type(exc).__name__,
                "message": str(exc),
            }

    async def _download(self, client: httpx.AsyncClient, url: str) -> str:
        return (await self._get(client, url)).text

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
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

    def parse_urls(self, html: str, listing_url: str) -> set[str]:
        soup = BeautifulSoup(html, "lxml")
        urls: set[str] = set()
        for anchor in soup.select(self.site.listing_selector):
            href = anchor.get("href")
            if isinstance(href, str) and (normalized := self._normalize(href, listing_url)):
                urls.add(normalized)
        return urls

    def _normalize(self, href: str, base_url: str) -> str | None:
        """Absolute article URL on the site's own host, without fragment."""
        if not href.strip():
            return None
        absolute_url, _fragment = urldefrag(urljoin(base_url, href.strip()))
        parts = urlsplit(absolute_url)
        if parts.scheme not in {"http", "https"} or parts.hostname != urlsplit(
            self.site.base_url
        ).hostname:
            return None
        return urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, "")
        )
