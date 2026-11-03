"""Keyword deduplication for LLM annotations.

Models write the same keyword in many ways ("Pós-Graduação", "pós-graduação",
"pos_graduacao", "processos seletivos" / "processo seletivo"). Keywords are
grouped by a normalized key and shown in their most frequent spelling.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
# Portuguese plural endings, checked in order on accent-free lowercase words.
_PLURALS = (
    ("oes", "ao"),
    ("aes", "ao"),
    ("ais", "al"),
    ("eis", "el"),
    ("ois", "ol"),
    ("res", "r"),
    ("zes", "z"),
    ("ns", "m"),
)
# Singular words ending in "s" that must not lose it.
_SINGULAR_ENDINGS = ("ss", "us", "is", "os", "es")


def _singular(word: str) -> str:
    if len(word) <= 3:
        return word
    for plural, singular in _PLURALS:
        if word.endswith(plural) and len(word) > len(plural) + 1:
            return word[: -len(plural)] + singular
    if word.endswith("s") and not word.endswith(_SINGULAR_ENDINGS):
        return word[:-1]
    if word.endswith(("os", "es")) and len(word) > 4:
        return word[:-1]
    return word


def keyword_key(text: str) -> str:
    """Accent-, case-, punctuation-, and plural-insensitive key."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    words = _NON_ALPHANUMERIC.sub(" ", ascii_text.lower()).split()
    return " ".join(_singular(word) for word in words)


class KeywordCanon:
    """Maps every spelling of a keyword to its most frequent form in the corpus."""

    def __init__(self, keyword_lists: Iterable[Iterable[str]]) -> None:
        spellings: dict[str, Counter[str]] = {}
        for keywords in keyword_lists:
            for keyword in keywords:
                text = " ".join(str(keyword).split())
                if text:
                    spellings.setdefault(keyword_key(text), Counter())[text] += 1
        self._display = {key: counter.most_common(1)[0][0] for key, counter in spellings.items()}

    def clean(self, keywords: Iterable[str], *, exclude: Iterable[str] = ()) -> list[str]:
        """One entry per keyword, in canonical spelling, without excluded terms."""
        excluded = {keyword_key(term) for term in exclude if term}
        cleaned: list[str] = []
        seen: set[str] = set()
        for keyword in keywords:
            key = keyword_key(str(keyword))
            if not key or key in seen or key in excluded:
                continue
            seen.add(key)
            cleaned.append(self._display.get(key, " ".join(str(keyword).split())))
        return cleaned
