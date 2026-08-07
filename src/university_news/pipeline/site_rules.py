from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlsplit


def author_name_from_slug(slug: str) -> str:
    particles = {"da", "das", "de", "do", "dos", "e"}
    words = [word for word in unquote(slug).split("-") if word]
    return " ".join(
        word.lower()
        if index > 0 and word.lower() in particles
        else word[:1].upper() + word[1:].lower()
        for index, word in enumerate(words)
    )


def usp_column_author(url: object) -> str | None:
    if not isinstance(url, str):
        return None
    parts = [part for part in urlsplit(url).path.split("/") if part]
    if len(parts) < 3 or parts[0] != "articulistas":
        return None
    return author_name_from_slug(parts[1])


def enrich_article_from_url(
    article: dict[str, Any],
    site: str,
) -> dict[str, Any]:
    enriched = article.copy()
    if site != "usp":
        return enriched
    url = enriched.get("url")
    if isinstance(url, str) and urlsplit(url).path.startswith("/articulista/"):
        # Singular /articulista/ URLs are profile/listing pages, not articles.
        enriched["author"] = None
    elif author := usp_column_author(url):
        # URL authorship is more reliable than generic body-text extraction
        # for Jornal da USP opinion columns.
        enriched["author"] = author
    return enriched
