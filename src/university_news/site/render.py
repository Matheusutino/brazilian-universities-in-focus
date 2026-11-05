"""Jinja2 environment shared by the static build and the article pages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .labels import LABELS, SDG_NAMES, as_list, label

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATES = PACKAGE_ROOT / "templates"
STATIC = PACKAGE_ROOT / "static"
LOGOS = STATIC / "logos"
MONTHS = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
REGION_SLUGS = {
    "Norte": "norte",
    "Nordeste": "nordeste",
    "Centro-Oeste": "centro-oeste",
    "Sudeste": "sudeste",
    "Sul": "sul",
}


def format_count(value: Any) -> str:
    return f"{int(value):,}".replace(",", ".")


def date_br(value: Any) -> str:
    if not value:
        return "Sem data"
    year, month, day = str(value)[:10].split("-")
    return f"{day}.{month}.{year}"


def month_year(value: Any) -> str:
    if not value:
        return "—"
    year, month = str(value)[:7].split("-")
    return f"{MONTHS[int(month) - 1]}. {year}"


def percent(value: Any, digits: int = 0) -> str:
    return f"{float(value) * 100:.{digits}f}%".replace(".", ",")


def coverage(part: Any, whole: Any) -> str:
    """A share with enough decimals that small coverage never reads as zero."""
    if not whole:
        return "—"
    share = int(part) / int(whole)
    digits = 1 if share >= 0.01 or share == 0 else 2 if share >= 0.0001 else 3
    return percent(share, digits)


def has_logo(journal: str) -> bool:
    return (LOGOS / f"{journal.lower()}.png").is_file()


def labels_json() -> str:
    """Enum labels for the browser; '<' is escaped so JSON is safe in <script>."""
    return json.dumps(LABELS, ensure_ascii=False).replace("<", "\\u003c")


def make_environment() -> Environment:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters.update(
        count=format_count,
        date_br=date_br,
        month_year=month_year,
        percent=percent,
        coverage=coverage,
        has_logo=has_logo,
        region_slug=lambda region: REGION_SLUGS.get(str(region), "none"),
        label=label,
        labels=lambda group, values: [label(group, value) for value in as_list(values)],
        sdg=lambda number: f"ODS {number} · {SDG_NAMES.get(int(number), '')}",
    )
    return environment
