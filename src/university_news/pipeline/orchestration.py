import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from ..shared.io import (
    append_jsonl,
    atomic_write_json,
    atomic_write_jsonl,
    read_jsonl,
)
from ..sources.catalog import SiteConfig
from .discovery import UrlDiscovery
from .extraction import ExtractionMode, StructuredExtractor
from .fetching import ContentFetcher

PipelineStage = Literal["discovery", "fetching", "extraction", "all"]


@dataclass(slots=True)
class PipelineStats:
    discovered: int = 0
    discovery_errors: int = 0
    downloaded: int = 0
    skipped_downloads: int = 0
    fetch_errors: int = 0
    extracted: int = 0
    skipped_extractions: int = 0
    extraction_errors: int = 0

    @property
    def errors(self) -> int:
        return self.discovery_errors + self.fetch_errors + self.extraction_errors


class NewsPipeline:
    def __init__(
        self,
        site: SiteConfig,
        output_directory: Path,
        *,
        concurrency: int = 10,
    ) -> None:
        self.site = site
        self.output_directory = output_directory
        self.concurrency = concurrency
        self.urls_path = output_directory / "urls.jsonl"
        self.discovery_state_path = output_directory / "discovery.json"
        self.raw_directory = output_directory / "raw"
        self.manifest_path = output_directory / "manifest.jsonl"
        self.articles_path = output_directory / "articles.jsonl"
        self.errors_path = output_directory / "errors.jsonl"
        self.current_errors_path = output_directory / ".errors.jsonl.current"

    async def run(
        self,
        *,
        stage: PipelineStage = "all",
        extractor: ExtractionMode = "trafilatura",
        start_page: int | None = None,
        end_page: int | None = None,
        force: bool = False,
    ) -> PipelineStats:
        if stage not in {"discovery", "fetching", "extraction", "all"}:
            raise ValueError(f"Invalid stage: {stage}")
        if extractor not in {"trafilatura", "llm", "hybrid"}:
            raise ValueError(f"Invalid extractor: {extractor}")
        start_page = self.site.start_page if start_page is None else start_page
        end_page = self.site.end_page if end_page is None else end_page
        if stage in {"discovery", "all"}:
            self.site.page_values(start_page, end_page)

        self.output_directory.mkdir(parents=True, exist_ok=True)
        self._prepare_run(stage, force)
        stats = PipelineStats()

        if stage in {"discovery", "all"}:
            urls = await self._run_discovery(start_page, end_page, force, stats)
        else:
            urls = self._load_urls()
        stats.discovered = len(urls)

        if stage in {"fetching", "all"}:
            await self._run_fetching(urls, force, stats)
        if stage in {"extraction", "all"}:
            self._run_extraction(urls, force, stats, extractor)
        self._finalize_errors()
        return stats

    async def _run_discovery(
        self,
        start_page: int,
        end_page: int,
        force: bool,
        stats: PipelineStats,
    ) -> list[str]:
        cached_urls = self._cached_urls(start_page, end_page) if not force else None
        if cached_urls is not None:
            return cached_urls

        discovery = UrlDiscovery(self.site, concurrency=self.concurrency)
        discovery_result = await discovery.discover(start_page, end_page)
        urls = sorted(discovery_result.urls)
        atomic_write_jsonl(self.urls_path, ({"url": url} for url in urls))
        if discovery_result.errors:
            # A later run must retry failed pages instead of treating a partial
            # URL list as a complete cache.
            self.discovery_state_path.unlink(missing_ok=True)
        else:
            atomic_write_json(
                self.discovery_state_path,
                {
                    "site": self.site.name,
                    "start_page": start_page,
                    "end_page": end_page,
                },
            )
        self._record_errors(discovery_result.errors)
        stats.discovery_errors = len(discovery_result.errors)
        return urls

    async def _run_fetching(
        self, urls: list[str], force: bool, stats: PipelineStats
    ) -> None:
        fetcher = ContentFetcher(
            self.site,
            self.raw_directory,
            concurrency=self.concurrency,
        )
        fetch_result = await fetcher.fetch(urls, force=force)
        append_jsonl(self.manifest_path, fetch_result.manifest_records)
        self._record_errors(fetch_result.errors)
        stats.downloaded = fetch_result.downloaded
        stats.skipped_downloads = fetch_result.skipped
        stats.fetch_errors = len(fetch_result.errors)

    def _run_extraction(
        self,
        urls: list[str],
        force: bool,
        stats: PipelineStats,
        extractor_mode: ExtractionMode,
    ) -> None:
        extractor = StructuredExtractor(
            self.raw_directory,
            self.articles_path,
            mode=extractor_mode,
            site=self.site,
        )
        extraction_result = extractor.run(urls, force=force)
        self._record_errors(extraction_result.errors)
        stats.extracted = extraction_result.extracted
        stats.skipped_extractions = extraction_result.skipped
        stats.extraction_errors = len(extraction_result.errors)

    def _prepare_run(self, stage: PipelineStage, force: bool) -> None:
        self.current_errors_path.unlink(missing_ok=True)
        if not force:
            return

        paths_by_stage = {
            "discovery": (self.urls_path, self.discovery_state_path),
            "fetching": (self.manifest_path,),
            "extraction": (self.articles_path,),
            "all": (
                self.urls_path,
                self.discovery_state_path,
                self.manifest_path,
                self.articles_path,
            ),
        }
        for path in paths_by_stage[stage]:
            path.unlink(missing_ok=True)

    def _load_urls(self) -> list[str]:
        if not self.urls_path.is_file():
            raise FileNotFoundError(
                f"No URLs found at {self.urls_path}; run the discovery stage"
            )
        urls = {
            str(record["url"])
            for record in read_jsonl(self.urls_path)
            if isinstance(record.get("url"), str)
        }
        return sorted(urls)

    def _cached_urls(self, start_page: int, end_page: int) -> list[str] | None:
        if not self.urls_path.is_file() or not self.discovery_state_path.is_file():
            return None
        try:
            state = json.loads(self.discovery_state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError("Invalid discovery state") from exc
        expected = {
            "site": self.site.name,
            "start_page": start_page,
            "end_page": end_page,
        }
        if state != expected:
            return None
        return self._load_urls()

    def _record_errors(self, records: list[dict[str, object]]) -> None:
        append_jsonl(self.current_errors_path, records)

    def _finalize_errors(self) -> None:
        if self.current_errors_path.exists():
            self.current_errors_path.replace(self.errors_path)
            return
        atomic_write_jsonl(self.errors_path, ())

    @staticmethod
    def stats_as_dict(stats: PipelineStats) -> dict[str, int]:
        result = asdict(stats)
        result["errors"] = stats.errors
        return result
