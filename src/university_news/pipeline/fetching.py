import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt
from tenacity.wait import wait_random_exponential

from ..shared.http_client import RetryableHttpStatus, raise_for_retryable_status, tls_context
from ..sources.catalog import SiteConfig


class ContentFetcher:
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
        if concurrency < 1 or concurrency > 10:
            raise ValueError("Concurrency must be between 1 and 10")
        self.site = site
        self.concurrency = concurrency
        self.timeout = timeout
        self.attempts = attempts
        self.retry_multiplier = retry_multiplier
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            verify=tls_context(),
            headers={"User-Agent": self.site.user_agent, "Accept": "text/html,*/*"},
            follow_redirects=True,
            timeout=self.timeout,
            limits=httpx.Limits(
                max_connections=self.concurrency,
                max_keepalive_connections=self.concurrency,
            ),
            transport=self.transport,
        )

    async def fetch_document(self, url: str) -> httpx.Response:
        """Download into memory, preserving response bytes and decoding metadata."""
        async with self._client() as client:
            return await self._download(client, url)

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
