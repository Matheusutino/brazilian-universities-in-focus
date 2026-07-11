import json
import os
from typing import Any, Protocol
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, ValidationError


DEFAULT_OPENROUTER_MODEL = "openrouter/owl-alpha"


class ArticleLLMResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    subtitle: str | None = None
    author: str | None = None
    date: str | None = None
    text: str = Field(min_length=1)
    tags: list[str] | None = None
    links: list[str] | None = None
    images: list[str] | None = None


class ExtractionStrategy(Protocol):
    def run(self, url: str, sections: list[str]) -> list[dict[str, Any]]: ...


class LLMArticleExtractor:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        content_selector: str | None = None,
        strategy: ExtractionStrategy | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.content_selector = content_selector
        self._strategy_instance = strategy

    def extract(self, html: str, url: str) -> dict[str, object]:
        cleaned_html = self._clean_article_html(html)
        blocks = self._strategy().run(url, [cleaned_html])
        failures: list[str] = []

        for block in blocks:
            if block.get("error"):
                failures.append(str(block.get("content") or "erro desconhecido"))
                continue
            candidate = self._unwrap_block(block)
            try:
                article = ArticleLLMResponse.model_validate(candidate)
            except ValidationError as exc:
                failures.append(str(exc))
                continue
            return self._normalize_article(article, url)

        detail = "; ".join(failures) or "resposta vazia"
        raise ValueError(f"OpenRouter nao retornou um artigo valido: {detail}")

    def _strategy(self) -> ExtractionStrategy:
        if self._strategy_instance is not None:
            return self._strategy_instance

        api_key = self.api_key or os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY nao foi definida no .env")
        model = self.model or os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
        provider = model if model.startswith("openrouter/") else f"openrouter/{model}"

        from crawl4ai import LLMConfig, LLMExtractionStrategy

        self._strategy_instance = LLMExtractionStrategy(
            llm_config=LLMConfig(
                provider=provider,
                api_token=api_key,
                backoff_base_delay=2,
                backoff_max_attempts=5,
                backoff_exponential_factor=2,
            ),
            schema=ArticleLLMResponse.model_json_schema(),
            extraction_type="schema",
            instruction=(
                "Extraia somente o conteudo editorial principal desta noticia. "
                "Ignore menus, cabecalhos, rodapes, publicidade, noticias relacionadas "
                "e barras de compartilhamento. Preserve o texto integral, sem resumir, "
                "traduzir ou inventar informacoes. Retorne autor e data somente quando "
                "estiverem presentes e mantenha o subtitulo separado do titulo. Em "
                "links e images, use apenas URLs existentes no HTML do corpo editorial."
            ),
            input_format="html",
            apply_chunking=False,
            force_json_response=True,
            extra_args={"temperature": 0.0},
        )
        return self._strategy_instance

    def _clean_article_html(self, html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        selected = soup.select(self.content_selector) if self.content_selector else []
        if selected:
            main = soup.new_tag("section")
            for node in selected:
                main.append(node.extract())
            cleanup_selector = "script, style, noscript, form, iframe, .social-share"
        else:
            # Seletores separados preservam a prioridade. Em paginas da USP ha cards
            # <article> no menu antes do <main id="main"> no documento.
            main = (
                soup.select_one("main#main")
                or soup.select_one("article#content")
                or soup.select_one("main")
                or soup.select_one("article")
            )
            cleanup_selector = (
                "script, style, noscript, nav, footer, aside, form, iframe"
            )

        for element in main.select(cleanup_selector) if main else []:
            element.decompose()
        return str(main or soup.body or soup)

    @staticmethod
    def _unwrap_block(block: dict[str, Any]) -> Any:
        if "text" in block:
            return block
        content = block.get("content")
        if isinstance(content, dict):
            return content
        if isinstance(content, str):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return block
        return block

    @classmethod
    def _normalize_article(
        cls, article: ArticleLLMResponse, page_url: str
    ) -> dict[str, object]:
        return {
            "url": page_url,
            "title": cls._optional_string(article.title),
            "subtitle": cls._optional_string(article.subtitle),
            "author": cls._optional_string(article.author),
            "date": cls._optional_string(article.date),
            "text": article.text.strip(),
            "tags": cls._normalize_strings(article.tags),
            "links": cls._normalize_urls(article.links, page_url),
            "images": cls._normalize_urls(article.images, page_url),
        }

    @staticmethod
    def _optional_string(value: str | None) -> str | None:
        normalized = value.strip() if value else ""
        return normalized or None

    @classmethod
    def _normalize_strings(cls, values: list[str] | None) -> list[str]:
        return sorted(
            {
                normalized
                for value in values or []
                if (normalized := cls._optional_string(value))
            }
        )

    @staticmethod
    def _normalize_urls(values: list[str] | None, page_url: str) -> list[str]:
        urls: set[str] = set()
        for value in values or []:
            absolute, _fragment = urldefrag(urljoin(page_url, value.strip()))
            if urlsplit(absolute).scheme in {"http", "https"}:
                urls.add(absolute)
        return sorted(urls)
