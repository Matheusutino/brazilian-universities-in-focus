import ssl
from functools import lru_cache

import certifi
import httpx

from ..settings import PROJECT_ROOT

# Intermediate CA certificates that some university servers fail to send
# (they serve only their own certificate). Trust still comes from certifi's
# roots; these only complete the chain.
EXTRA_CERTIFICATES = PROJECT_ROOT / "config" / "certs"


@lru_cache(maxsize=1)
def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=certifi.where())
    for path in sorted(EXTRA_CERTIFICATES.glob("*.pem")):
        context.load_verify_locations(cafile=str(path))
    return context


import httpx


class RetryableHttpStatus(httpx.HTTPStatusError):
    """HTTP status that can be retried."""


def raise_for_retryable_status(response: httpx.Response) -> None:
    if response.status_code == 429 or response.status_code >= 500:
        raise RetryableHttpStatus(
            f"Transient HTTP status: {response.status_code}",
            request=response.request,
            response=response,
        )
    response.raise_for_status()
