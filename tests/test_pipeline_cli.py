import json

from jornais_new import cli
from jornais_new.config import USP
from jornais_new.discovery import DiscoveryResult
from jornais_new.extraction import ExtractionResult
from jornais_new.fetching import FetchResult
from jornais_new.io_utils import read_jsonl
from jornais_new.pipeline import NewsPipeline, PipelineStats


async def test_pipeline_runs_stages_and_writes_artifacts(tmp_path, monkeypatch) -> None:
    urls = {
        "https://jornal.usp.br/b/",
        "https://jornal.usp.br/a/",
    }

    class FakeDiscovery:
        def __init__(self, *args, **kwargs):
            pass

        async def discover(self, start_page, end_page):
            assert (start_page, end_page) == (1, 2)
            return DiscoveryResult(urls=urls, errors=[])

    class FakeFetcher:
        def __init__(self, site, raw_directory, **kwargs):
            self.raw_directory = raw_directory

        async def fetch(self, received_urls, *, force=False):
            assert received_urls == sorted(urls)
            return FetchResult(
                downloaded=2,
                skipped=0,
                manifest_records=[
                    {"url": url, "file": "raw/x.html"} for url in received_urls
                ],
                errors=[],
            )

    class FakeExtractor:
        def __init__(self, raw_directory, articles_path, **kwargs):
            pass

        def run(self, received_urls, *, force=False):
            assert received_urls == sorted(urls)
            return ExtractionResult(extracted=2, skipped=0, errors=[])

    monkeypatch.setattr("jornais_new.pipeline.UrlDiscovery", FakeDiscovery)
    monkeypatch.setattr("jornais_new.pipeline.ContentFetcher", FakeFetcher)
    monkeypatch.setattr("jornais_new.pipeline.StructuredExtractor", FakeExtractor)

    pipeline = NewsPipeline(USP, tmp_path)
    stats = await pipeline.run(start_page=1, end_page=2)

    assert stats.discovered == 2
    assert stats.downloaded == 2
    assert stats.extracted == 2
    assert [record["url"] for record in read_jsonl(tmp_path / "urls.jsonl")] == sorted(
        urls
    )
    assert (tmp_path / "manifest.jsonl").exists()
    assert (tmp_path / "errors.jsonl").exists()
    assert json.loads((tmp_path / "discovery.json").read_text())["end_page"] == 2


async def test_pipeline_does_not_cache_partial_discovery(tmp_path, monkeypatch) -> None:
    class PartialDiscovery:
        def __init__(self, *args, **kwargs):
            pass

        async def discover(self, start_page, end_page):
            return DiscoveryResult(
                urls=set(),
                errors=[{"stage": "discovery", "page": 2, "message": "timeout"}],
            )

    monkeypatch.setattr("jornais_new.pipeline.UrlDiscovery", PartialDiscovery)
    pipeline = NewsPipeline(USP, tmp_path)
    stats = await pipeline.run(start_page=1, end_page=2)

    assert stats.discovery_errors == 1
    assert not (tmp_path / "discovery.json").exists()
    assert read_jsonl(tmp_path / "errors.jsonl")[0]["stage"] == "discovery"


async def test_pipeline_runs_each_stage_independently(tmp_path, monkeypatch) -> None:
    url = "https://jornal.usp.br/ciencias/materia/"
    calls: list[str] = []

    class FakeDiscovery:
        def __init__(self, *args, **kwargs):
            pass

        async def discover(self, start_page, end_page):
            calls.append("discovery")
            return DiscoveryResult(urls={url}, errors=[])

    class FakeFetcher:
        def __init__(self, *args, **kwargs):
            pass

        async def fetch(self, received_urls, *, force=False):
            calls.append("fetching")
            assert received_urls == [url]
            return FetchResult(1, 0, [{"url": url, "file": "raw/x.html"}], [])

    class FakeExtractor:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, received_urls, *, force=False):
            calls.append("extraction")
            assert received_urls == [url]
            return ExtractionResult(1, 0, [])

    monkeypatch.setattr("jornais_new.pipeline.UrlDiscovery", FakeDiscovery)
    monkeypatch.setattr("jornais_new.pipeline.ContentFetcher", FakeFetcher)
    monkeypatch.setattr("jornais_new.pipeline.StructuredExtractor", FakeExtractor)
    pipeline = NewsPipeline(USP, tmp_path)

    discovery_stats = await pipeline.run(stage="discovery", start_page=1, end_page=1)
    assert calls == ["discovery"]
    assert discovery_stats.discovered == 1
    assert not (tmp_path / "manifest.jsonl").exists()

    fetching_stats = await pipeline.run(stage="fetching")
    assert calls == ["discovery", "fetching"]
    assert fetching_stats.downloaded == 1

    extraction_stats = await pipeline.run(stage="extraction")
    assert calls == ["discovery", "fetching", "extraction"]
    assert extraction_stats.extracted == 1


async def test_downstream_stage_requires_urls_file(tmp_path) -> None:
    pipeline = NewsPipeline(USP, tmp_path)

    try:
        await pipeline.run(stage="fetching")
    except FileNotFoundError as exc:
        assert "discovery" in str(exc)
    else:
        raise AssertionError("fetching deveria exigir urls.jsonl")


def test_cli_returns_success_for_item_errors_and_failure_for_structural_error(
    tmp_path, monkeypatch, capsys
) -> None:
    class FakePipeline:
        def __init__(self, site, output, concurrency):
            self.output = output

        async def run(self, **kwargs):
            assert kwargs["stage"] == "discovery"
            assert kwargs["extractor"] == "trafilatura"
            if self.output.name == "failure":
                raise ValueError("configuracao invalida")
            return PipelineStats(downloaded=1, fetch_errors=1)

        @staticmethod
        def stats_as_dict(stats):
            return NewsPipeline.stats_as_dict(stats)

    monkeypatch.setattr(cli, "NewsPipeline", FakePipeline)

    success = cli.run(
        ["usp", "--stage", "discovery", "--output", str(tmp_path / "success")]
    )
    output = json.loads(capsys.readouterr().out)
    failure = cli.run(
        ["usp", "--stage", "discovery", "--output", str(tmp_path / "failure")]
    )

    assert success == 0
    assert output["fetch_errors"] == 1
    assert failure == 1
