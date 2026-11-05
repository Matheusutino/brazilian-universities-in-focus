import httpx
import pytest

from university_news.sources.catalog import USP
from university_news.pipeline.fetching import ContentFetcher


async def test_in_memory_fetch_preserves_bytes_encoding_and_redirects(tmp_path) -> None:
    content = "<html>Ciência e educação</html>".encode("iso-8859-1")
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/old":
            return httpx.Response(302, headers={"Location": "/new"})
        return httpx.Response(
            200, content=content,
            headers={"Content-Type": "text/html; charset=iso-8859-1"},
        )

    fetcher = ContentFetcher(USP, transport=httpx.MockTransport(handler))
    response = await fetcher.fetch_document("https://jornal.usp.br/old")
    assert response.content == content
    assert response.encoding == "iso-8859-1"
    assert str(response.url) == "https://jornal.usp.br/new"
    assert calls == ["/old", "/new"]
    assert list(tmp_path.iterdir()) == []


async def test_in_memory_fetch_retries_transient_errors_but_raises_permanent_errors():
    attempts = {}

    def handler(request):
        path = request.url.path
        attempts[path] = attempts.get(path, 0) + 1
        status = 503 if attempts[path] < 3 else 200
        if path == "/missing":
            status = 404
        return httpx.Response(status, content=b"<html>ok</html>")

    fetcher = ContentFetcher(
        USP, transport=httpx.MockTransport(handler), retry_multiplier=0,
    )
    assert (await fetcher.fetch_document("https://jornal.usp.br/retry")).status_code == 200
    with pytest.raises(httpx.HTTPStatusError):
        await fetcher.fetch_document("https://jornal.usp.br/missing")
    assert attempts == {"/retry": 3, "/missing": 1}
