import asyncio
import hashlib

import httpx

from jornais_new.config import USP
from jornais_new.fetching import ContentFetcher


def test_filename_is_sha256() -> None:
    url = "https://jornal.usp.br/ciencias/exemplo/"
    expected = hashlib.sha256(url.encode()).hexdigest() + ".html"
    assert ContentFetcher.filename_for_url(url) == expected


async def test_fetcher_limits_concurrency_and_resumes(tmp_path) -> None:
    active = 0
    maximum_active = 0
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active, calls
        calls += 1
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, request=request, content=b"<html>ok</html>")

    urls = [f"https://jornal.usp.br/noticia/{number}/" for number in range(20)]
    fetcher = ContentFetcher(
        USP,
        tmp_path / "raw",
        concurrency=10,
        transport=httpx.MockTransport(handler),
    )
    first = await fetcher.fetch(urls)
    second = await fetcher.fetch(urls)

    assert first.downloaded == 20
    assert first.skipped == 0
    assert second.downloaded == 0
    assert second.skipped == 20
    assert maximum_active == 10
    assert calls == 20
    assert all(
        fetcher.path_for_url(url).read_bytes() == b"<html>ok</html>" for url in urls
    )


async def test_fetcher_retries_only_transient_errors(tmp_path) -> None:
    attempts: dict[str, int] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path
        attempts[key] = attempts.get(key, 0) + 1
        status = 500 if key == "/retry/" and attempts[key] < 3 else 200
        if key == "/missing/":
            status = 404
        return httpx.Response(status, request=request, content=b"<html>ok</html>")

    fetcher = ContentFetcher(
        USP,
        tmp_path / "raw",
        attempts=5,
        retry_multiplier=0,
        transport=httpx.MockTransport(handler),
    )
    result = await fetcher.fetch(
        ["https://jornal.usp.br/retry/", "https://jornal.usp.br/missing/"]
    )

    assert result.downloaded == 1
    assert len(result.errors) == 1
    assert attempts["/retry/"] == 3
    assert attempts["/missing/"] == 1
