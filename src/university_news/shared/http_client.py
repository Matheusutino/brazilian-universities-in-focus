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
