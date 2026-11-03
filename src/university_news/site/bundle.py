"""Build the read-only data bundle the website serves from.

The bundle is one SQLite file with every article, its LLM annotation, and an
FTS5 full-text index, plus precomputed statistics for the static pages. It is
rebuilt at publication time, so the web server never loads the corpus into
memory and never needs PostgreSQL.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..shared.articles import SITE_ARTICLE_COLUMNS
from ..sources.catalog import REGIONS, region_for_site
from .keywords import KeywordCanon
from .labels import SDG_NAMES, as_list, label

# The consolidated corpus starts in 1999; earlier dates are invalid metadata.
EARLIEST_ARTICLE_DATE = "1990-01-01"
DATABASE_FILENAME = "site.sqlite"

SCHEMA_SQL = """
CREATE TABLE articles (
    rowid INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT,
    author TEXT,
    date TEXT,
    journal TEXT NOT NULL,
    region TEXT NOT NULL,
    annotated INTEGER NOT NULL DEFAULT 0,
    category TEXT,
    content_type TEXT,
    fields TEXT,
    annotation TEXT
);
CREATE INDEX articles_date_idx ON articles (date DESC);
CREATE INDEX articles_journal_date_idx ON articles (journal, date DESC);
CREATE INDEX articles_region_date_idx ON articles (region, date DESC);
CREATE INDEX articles_annotated_idx ON articles (annotated, date DESC);
-- Contentless index: the text lives in articles; rowids match.
CREATE VIRTUAL TABLE articles_fts USING fts5(
    title, subtitle, summary, keywords,
    content='',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def article_id(url: str) -> str:
    """Same identifier as the LanceDB index, so semantic hits join directly."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, url))


def _text(value: object) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = " ".join(str(value).split())
    return text or None


def _plain(value: Any) -> Any:
    """Convert numpy containers and scalars into JSON-ready Python values."""
    if isinstance(value, np.ndarray):
        return [_plain(item) for item in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and np.isnan(value):
        return None
    return value


def load_articles(path: Path) -> pd.DataFrame:
    articles = pd.read_parquet(path, columns=list(SITE_ARTICLE_COLUMNS)).astype(object)
    articles = articles[articles["url"].map(lambda url: isinstance(url, str) and bool(url))]
    articles = articles.drop_duplicates("url")
    dates = pd.to_datetime(articles["date"], format="ISO8601", errors="coerce")
    valid = dates >= pd.Timestamp(EARLIEST_ARTICLE_DATE)
    articles["date"] = dates.dt.strftime("%Y-%m-%d").where(valid, None)
    articles["journal"] = articles["journal"].astype(str).str.upper()
    return articles


def load_annotations(path: Path | None) -> dict[str, dict[str, Any]]:
    """Flat annotation records keyed by article URL (first profile wins)."""
    if path is None or not path.is_file():
        return {}
    frame = pd.read_parquet(path).sort_values(["url", "enriched_at"])
    annotations: dict[str, dict[str, Any]] = {}
    for record in frame.to_dict("records"):
        url = record.get("url")
        if not isinstance(url, str) or url in annotations:
            continue
        clean = {key: _plain(value) for key, value in record.items()}
        if isinstance(clean.get("entities"), str):
            clean["entities"] = json.loads(clean["entities"])
        annotations[url] = clean
    return annotations


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


@dataclass(frozen=True, slots=True)
class BundleStats:
    articles: int
    annotated: int
    path: str


def build_database(
    articles: pd.DataFrame,
    annotations: dict[str, dict[str, Any]],
    path: Path,
    *,
    meta: dict[str, Any],
) -> BundleStats:
    """Write the SQLite bundle atomically (readers never see a partial file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".sqlite.tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    annotated = 0
    try:
        connection.executescript(SCHEMA_SQL)
        rows = []
        texts = []
        for rowid, record in enumerate(
            articles.sort_values("date", ascending=False, na_position="last").to_dict("records"),
            start=1,
        ):
            url = str(record["url"])
            annotation = annotations.get(url)
            annotated += annotation is not None
            fields = as_list((annotation or {}).get("topics.scientific_fields"))
            rows.append(
                (
                    rowid,
                    article_id(url),
                    url,
                    _text(record.get("title")) or "Notícia sem título",
                    _text(record.get("subtitle")),
                    _text(record.get("author")),
                    record.get("date"),
                    record["journal"],
                    region_for_site(record["journal"]),
                    int(annotation is not None),
                    (annotation or {}).get("classification.news_category"),
                    (annotation or {}).get("classification.content_type"),
                    f"|{'|'.join(fields)}|" if fields else None,
                    json.dumps(annotation, ensure_ascii=False) if annotation else None,
                )
            )
            texts.append(
                (
                    rowid,
                    _text(record.get("title")) or "",
                    _text(record.get("subtitle")) or "",
                    (annotation or {}).get("summary.one_sentence") or "",
                    " ".join(map(str, as_list((annotation or {}).get("topics.keywords")))),
                )
            )
        connection.executemany(
            "INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
        connection.executemany(
            "INSERT INTO articles_fts (rowid, title, subtitle, summary, keywords) "
            "VALUES (?, ?, ?, ?, ?)",
            texts,
        )
        connection.execute("INSERT INTO articles_fts (articles_fts) VALUES ('optimize')")
        connection.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in meta.items()],
        )
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()
    os.replace(temporary, path)
    return BundleStats(articles=len(rows), annotated=annotated, path=str(path))


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
