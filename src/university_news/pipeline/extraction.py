import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup
from lxml import etree
from trafilatura import extract

from ..shared.io import append_jsonl, read_jsonl
from ..sources.catalog import SiteConfig
from .fetching import ContentFetcher
from .llm_extraction import LLMArticleExtractor

ExtractionMode = Literal["trafilatura", "llm", "hybrid"]
LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ExtractionResult:
    extracted: int
    skipped: int
    errors: list[dict[str, object]]


class StructuredExtractor:
    def __init__(
        self,
        raw_directory: Path,
        articles_path: Path,
        *,
        mode: ExtractionMode = "trafilatura",
        site: SiteConfig | None = None,
        llm_extractor: LLMArticleExtractor | None = None,
    ) -> None:
        if mode not in {"trafilatura", "llm", "hybrid"}:
            raise ValueError(f"Invalid extractor: {mode}")
        self.raw_directory = raw_directory
        self.articles_path = articles_path
        self.mode = mode
        self.site = site
        self._llm_extractor = llm_extractor

    def run(self, urls: list[str], *, force: bool = False) -> ExtractionResult:
        processed = set() if force else self._processed_urls()
        extracted_count = 0
        skipped_count = 0
        errors: list[dict[str, object]] = []
        total = len(urls)

        LOGGER.info(
            "Iniciando extraction: total=%s, ja_processadas=%s, force=%s, extractor=%s",
            total,
            len(processed),
            force,
            self.mode,
        )

        for index, url in enumerate(urls, start=1):
            if url in processed:
                skipped_count += 1
                self._log_progress(
                    index, total, extracted_count, skipped_count, len(errors)
                )
                continue
            raw_path = self.raw_directory / ContentFetcher.filename_for_url(url)
            if not raw_path.is_file() or raw_path.stat().st_size == 0:
                errors.append(
                    self._error(url, "HTMLNotFound", f"HTML not found: {raw_path}")
                )
                self._log_progress(
                    index, total, extracted_count, skipped_count, len(errors)
                )
                continue
            try:
                article = self.extract_file(raw_path, url)
                append_jsonl(self.articles_path, [article])
                extracted_count += 1
            except Exception as exc:
                errors.append(self._error(url, type(exc).__name__, str(exc)))
            self._log_progress(index, total, extracted_count, skipped_count, len(errors))

        LOGGER.info(
            "Extraction complete: total=%s, extracted=%s, skipped=%s, errors=%s",
            total,
            extracted_count,
            skipped_count,
            len(errors),
        )
        return ExtractionResult(extracted_count, skipped_count, errors)

    @staticmethod
    def _log_progress(
        index: int, total: int, extracted_count: int, skipped_count: int, errors: int
    ) -> None:
        if total == 0:
            return
        if index == total or index % 100 == 0:
            LOGGER.info(
            "Extraction progress: %s/%s, extracted=%s, skipped=%s, errors=%s",
                index,
                total,
                extracted_count,
                skipped_count,
                errors,
            )

    def extract_file(self, raw_path: Path, url: str) -> dict[str, object]:
        html = raw_path.read_text(encoding="utf-8", errors="replace")
        if self.mode == "llm":
            return self._llm().extract(html, url)

        trafilatura_article: dict[str, object] | None = None
        trafilatura_error: Exception | None = None
        try:
            trafilatura_article = self._extract_with_trafilatura(html, url)
        except Exception as exc:
            trafilatura_error = exc

        if self.mode == "trafilatura":
            if trafilatura_error is not None:
                raise trafilatura_error
            assert trafilatura_article is not None
            return trafilatura_article

        if trafilatura_article is not None and not self._needs_llm(trafilatura_article):
            return trafilatura_article
        try:
            return self._llm().extract(html, url)
        except Exception as exc:
            if trafilatura_article is not None:
                LOGGER.warning(
                    "LLM falhou para %s; mantendo resultado do trafilatura: %s",
                    url,
                    exc,
                )
                return trafilatura_article
            if trafilatura_error is not None:
                raise trafilatura_error
            raise

    def _extract_with_trafilatura(self, html: str, url: str) -> dict[str, object]:
        payload = extract(html, url=url, output_format="json", with_metadata=True)
        if not payload:
            raise ValueError("Trafilatura did not find article content")
        metadata = json.loads(payload)
        if not isinstance(metadata, dict):
            raise ValueError("Unexpected Trafilatura output")

        text = self._optional_string(metadata.get("text") or metadata.get("raw_text"))
        if not text:
            raise ValueError("Extraction returned no text")
        links, images = self._extract_resources(html, url)
        site_links, site_images = self._extract_site_resources(html, url)
        links = sorted(set(links) | site_links)
        images = sorted(set(images) | site_images)
        article = {
            "url": url,
            "title": self._optional_string(metadata.get("title")),
            # A description from Trafilatura may be social metadata or an
            # automatic excerpt. Subtitles must come from an explicit site
            # selector instead.
            "subtitle": None,
            "author": self._optional_string(metadata.get("author")),
            "date": self._optional_string(metadata.get("date")),
            "text": text,
            "tags": self._normalize_tags(
                metadata.get("tags") or metadata.get("categories")
            ),
            "links": links,
            "images": images,
        }
        self._enrich_from_site_html(article, html)
        article["text"] = self._strip_leading_headings(
            text,
            article.get("title"),
            article.get("subtitle"),
        )
        return article

    def _extract_site_resources(
        self, html: str, page_url: str
    ) -> tuple[set[str], set[str]]:
        if self.site is None or not self.site.content_selector:
            return set(), set()

        soup = BeautifulSoup(html, "lxml")
        links: set[str] = set()
        images: set[str] = set()
        for section in soup.select(self.site.content_selector):
            anchors = [section] if section.name == "a" else section.select("a[href]")
            for anchor in anchors:
                normalized = self._normalize_resource_url(anchor.get("href"), page_url)
                if normalized:
                    links.add(normalized)

            image_nodes = [section] if section.name == "img" else section.select("img")
            for image in image_nodes:
                source = (
                    image.get("src")
                    or image.get("data-src")
                    or image.get("data-lazy-src")
                )
                normalized = self._normalize_resource_url(source, page_url)
                if normalized:
                    images.add(normalized)
        return links, images

    def _llm(self) -> LLMArticleExtractor:
        if self._llm_extractor is None:
            self._llm_extractor = LLMArticleExtractor(
                content_selector=self.site.content_selector if self.site else None
            )
        return self._llm_extractor

    def _enrich_from_site_html(self, article: dict[str, object], html: str) -> None:
        if self.site is None:
            return
        soup = BeautifulSoup(html, "lxml")

        if self.site.title_selector:
            title = soup.select_one(self.site.title_selector)
            if title is not None:
                article["title"] = self._optional_string(
                    title.get_text(" ", strip=True)
                )

        if self.site.subtitle_selector:
            subtitle = soup.select_one(self.site.subtitle_selector)
            if subtitle is not None:
                article["subtitle"] = self._optional_string(
                    subtitle.get_text(" ", strip=True)
                )

        if self.site.author_selector:
            author = soup.select_one(self.site.author_selector)
            if author is not None:
                value = author.get_text(" ", strip=True)
                value = re.sub(
                    r"^(?:por|texto)\s*:?\s*", "", value, flags=re.IGNORECASE
                )
                article["author"] = self._optional_string(value)

        # Some Joomla templates leave the byline as plain text instead of
        # placing it in a dedicated element (for example, "Escrito por Nome").
        if not article.get("author"):
            for node in soup.find_all(string=re.compile(r"\bEscrito\s+por\b", re.I)):
                match = re.search(
                    r"\bEscrito\s+por\s+([^|\n]+)", str(node), flags=re.I
                )
                if match:
                    article["author"] = self._optional_string(match.group(1))
                    if article["author"]:
                        break

        if self.site.date_selector:
            date = soup.select_one(self.site.date_selector)
            if date is not None:
                value = date.get("datetime") or date.get_text(" ", strip=True)
                article["date"] = self._normalize_date(value)

        if self.site.tags_selector:
            tags = {
                value
                for node in soup.select(self.site.tags_selector)
                if (value := self._optional_string(node.get_text(" ", strip=True)))
            }
            if tags:
                article["tags"] = sorted(tags)

    @staticmethod
    def _strip_leading_headings(
        text: str,
        title: object,
        subtitle: object,
    ) -> str:
        headings = {
            " ".join(value.split()).casefold()
            for value in (title, subtitle)
            if isinstance(value, str) and value.strip()
        }
        if not headings:
            return text
        lines = text.splitlines()
        while lines and " ".join(lines[0].split()).casefold() in headings:
            lines.pop(0)
        cleaned = "\n".join(lines).strip()
        return cleaned or text

    @staticmethod
    def _needs_llm(article: dict[str, object]) -> bool:
        text = article.get("text")
        return (
            not article.get("title")
            or not article.get("author")
            or not article.get("date")
            or not isinstance(text, str)
            or len(text) < 200
        )

    def _processed_urls(self) -> set[str]:
        records = read_jsonl(self.articles_path)
        required_fields = {"subtitle", "links", "images"}
        if any(not required_fields.issubset(record) for record in records):
            raise ValueError(
                "articles.jsonl usa um schema antigo; execute extraction com --force"
            )
        return {
            str(record["url"])
            for record in records
            if isinstance(record.get("url"), str)
        }

    @classmethod
    def _extract_resources(
        cls, html: str, page_url: str
    ) -> tuple[list[str], list[str]]:
        payload = extract(
            html,
            url=page_url,
            output_format="xml",
            with_metadata=True,
            include_links=True,
            include_images=True,
        )
        if not payload:
            return [], []

        root = etree.fromstring(payload.encode("utf-8"))
        links = {
            normalized
            for node in root.xpath('.//*[local-name()="ref"][@target]')
            if (normalized := cls._normalize_resource_url(node.get("target"), page_url))
        }
        images = {
            normalized
            for node in root.xpath('.//*[local-name()="graphic"][@src]')
            if (normalized := cls._normalize_resource_url(node.get("src"), page_url))
        }
        return sorted(links), sorted(images)

    @staticmethod
    def _normalize_resource_url(value: str | None, page_url: str) -> str | None:
        if not value:
            return None
        absolute_url, _fragment = urldefrag(urljoin(page_url, value.strip()))
        if urlsplit(absolute_url).scheme not in {"http", "https"}:
            return None
        return absolute_url

    @classmethod
    def _normalize_date(cls, value: Any) -> str | None:
        normalized = cls._optional_string(value)
        if not normalized:
            return None
        iso_match = re.search(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", normalized)
        if iso_match:
            return "-".join(iso_match.groups())
        br_match = re.search(r"\b(\d{2})/(\d{2})/(\d{2,4})\b", normalized)
        if br_match:
            day, month, year = br_match.groups()
            if len(year) == 2:
                year = f"20{year}"
            return f"{year}-{month}-{day}"
        pt_match = re.search(
            r"\b(\d{1,2})\s+(?:de\s+)?"
            r"(janeiro|jan|fevereiro|fev|março|mar|marco|abril|abr|maio|mai|"
            r"junho|jun|julho|jul|agosto|ago|setembro|set|outubro|out|"
            r"novembro|nov|dezembro|dez)\s*,?\s+(?:de\s+)?(\d{4})\b",
            normalized,
            flags=re.IGNORECASE,
        )
        if pt_match:
            day, month_name, year = pt_match.groups()
            months = {
                "janeiro": 1,
                "jan": 1,
                "fevereiro": 2,
                "fev": 2,
                "março": 3,
                "marco": 3,
                "mar": 3,
                "abril": 4,
                "abr": 4,
                "maio": 5,
                "mai": 5,
                "junho": 6,
                "jun": 6,
                "julho": 7,
                "jul": 7,
                "agosto": 8,
                "ago": 8,
                "setembro": 9,
                "set": 9,
                "outubro": 10,
                "out": 10,
                "novembro": 11,
                "nov": 11,
                "dezembro": 12,
                "dez": 12,
            }
            return f"{year}-{months[month_name.lower()]:02d}-{int(day):02d}"
        return normalized

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @classmethod
    def _normalize_tags(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            separator = ";" if ";" in value else ","
            values = value.split(separator)
        elif isinstance(value, (list, tuple, set)):
            values = value
        else:
            values = [value]
        tags = {tag for item in values if (tag := cls._optional_string(item))}
        return sorted(tags)

    @staticmethod
    def _error(url: str, error: str, message: str) -> dict[str, object]:
        return {
            "stage": "extraction",
            "url": url,
            "error": error,
            "message": message,
        }
