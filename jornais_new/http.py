import httpx


class RetryableHttpStatus(httpx.HTTPStatusError):
    """Status HTTP que pode ser tentado novamente."""


def raise_for_retryable_status(response: httpx.Response) -> None:
    if response.status_code == 429 or response.status_code >= 500:
        raise RetryableHttpStatus(
            f"Status HTTP transitorio: {response.status_code}",
            request=response.request,
            response=response,
        )
    response.raise_for_status()
