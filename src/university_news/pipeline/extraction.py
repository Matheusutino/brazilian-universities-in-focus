import json
import re
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup
from lxml import etree
from trafilatura import extract

from ..sources.catalog import SiteConfig
from .site_rules import enrich_article_from_url



class StructuredExtractor:
    def __init__(
        self,
        *,
        site: SiteConfig | None = None,
    ) -> None:
        self.site = site

    def extract_html(self, html: str, url: str) -> dict[str, object]:
        """Extract an in-memory document without intermediate filesystem artifacts."""
        return self._extract_with_trafilatura(html, url)

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
        self._enrich_from_url(article, url)
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
                    r"^(?:escrito\s+por|por|texto)\s*:?\s*",
                    "",
                    value,
                    flags=re.IGNORECASE,
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

    def _enrich_from_url(
        self,
        article: dict[str, object],
        url: str,
    ) -> None:
        if self.site is None:
            return
        article.update(enrich_article_from_url(article, self.site.name))

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
