"""Statistics for static pages, computed from a PostgreSQL snapshot."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

import pandas as pd

from ..sources.catalog import REGIONS, region_for_site
from .keywords import KeywordCanon
from .labels import SDG_NAMES, as_list, label


def deduplicate_keywords(annotations: dict[str, dict[str, Any]]) -> None:
    """Merge spelling variants and drop the article's own university acronym.

    Keywords are rewritten in place, so article pages, search, and statistics
    all see the same cleaned list.
    """
    canon = KeywordCanon(as_list(item.get("topics.keywords")) for item in annotations.values())
    for item in annotations.values():
        item["topics.keywords"] = canon.clean(
            as_list(item.get("topics.keywords")), exclude=[str(item.get("university") or "")]
        )


def count_values(
    values: Iterable[object], group: str, *, order: list[Any] | None = None
) -> list[dict[str, Any]]:
    """Counts with Portuguese labels, sorted by size unless an order is given."""
    counter: Counter[Any] = Counter()
    for value in values:
        items = as_list(value) if not isinstance(value, str) else [value]
        counter.update(item for item in items if item is not None)
    keys = order if order is not None else [key for key, _count in counter.most_common()]
    total = sum(counter.values()) or 1
    rows = []
    for key in keys:
        name = f"ODS {key} · {SDG_NAMES.get(int(key), '')}" if group == "sdgs" else label(group, key)
        rows.append(
            {"value": key, "label": name, "count": counter.get(key, 0), "share": counter.get(key, 0) / total}
        )
    return rows


CHARTS: tuple[tuple[str, str, str, str, list[Any] | None], ...] = (
    ("news_category", "Categoria da notícia", "Assunto principal de cada publicação", "classification.news_category", None),
    ("scientific_fields", "Grandes áreas do conhecimento (CNPq)", "Uma notícia pode tocar mais de uma área", "topics.scientific_fields", None),
    ("content_type", "Tipo de conteúdo", "Formato jornalístico", "classification.content_type", None),
    ("institutional_axes", "Eixos institucionais", "Ensino, pesquisa, extensão e outros", "classification.institutional_axes", None),
    ("scientific_content", "Centralidade do conteúdo científico", "Do mais ao menos científico", "classification.scientific_content", ["central", "relevant", "peripheral", "absent"]),
    ("tone", "Tom", "Como a notícia se posiciona", "communication.tone", None),
    ("hype", "Hype", "Exagero ou sensacionalismo na comunicação", "communication.hype", ["none", "low", "medium", "high"]),
    ("audience", "Público principal", "A quem a notícia se dirige", "communication.audience", None),
    ("macrotemas", "Agenda Nacional: macrotemas", "Associações temáticas automáticas; não medem impacto social", "agenda_nacional.macrotemas", None),
    ("sdgs", "Objetivos de Desenvolvimento Sustentável", "ODS relacionados às notícias, na ordem oficial", "societal_impact.sdgs", list(range(1, 18))),
)


def analysis_scope(
    annotations: list[dict[str, Any]], articles_in_scope: int
) -> dict[str, Any]:
    keywords = count_values((item.get("topics.keywords") for item in annotations), "keywords")[:30]
    return {
        "annotated": len(annotations),
        "articles": articles_in_scope,
        "research_share": (
            sum(bool(item.get("research.present")) for item in annotations) / len(annotations)
            if annotations
            else 0.0
        ),
        "models": sorted({str(item.get("model")) for item in annotations if item.get("model")}),
        "charts": [
            {
                "key": key,
                "title": title,
                "note": note,
                "ordered": order is not None,
                "rows": count_values((item.get(column) for item in annotations), key, order=order),
            }
            for key, title, note, column, order in CHARTS
        ],
        "keywords": keywords,
    }


def site_statistics(
    articles: pd.DataFrame, annotations: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Everything the static pages show, computed once per publication."""
    dates = pd.to_datetime(articles["date"], errors="coerce")
    by_journal = articles.assign(_date=dates).groupby("journal")
    universities = sorted(
        (
            {
                "journal": journal,
                "region": region_for_site(journal),
                "news": int(len(group)),
                "earliest": group["_date"].min().strftime("%Y-%m-%d") if group["_date"].notna().any() else None,
                "latest": group["_date"].max().strftime("%Y-%m-%d") if group["_date"].notna().any() else None,
                "annotated": int(group["url"].isin(annotations.keys()).sum()),
            }
            for journal, group in by_journal
        ),
        key=lambda item: item["news"],
        reverse=True,
    )
    records = list(annotations.values())
    counts = articles["journal"].value_counts().to_dict()
    scopes: dict[str, Any] = {"all": analysis_scope(records, len(articles))}
    for region in REGIONS:
        journals = {item["journal"] for item in universities if item["region"] == region}
        scopes[f"region:{region}"] = analysis_scope(
            [item for item in records if str(item.get("university")) in journals],
            sum(counts.get(journal, 0) for journal in journals),
        )
    for item in universities:
        scopes[f"university:{item['journal']}"] = analysis_scope(
            [record for record in records if record.get("university") == item["journal"]],
            item["news"],
        )
    return {
        "articles": int(len(articles)),
        "universities": universities,
        "regions": list(REGIONS),
        "earliest_year": int(dates.min().year) if dates.notna().any() else None,
        "latest_year": int(dates.max().year) if dates.notna().any() else None,
        "annotated": len(records),
        "analysis": scopes,
    }
