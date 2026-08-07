from __future__ import annotations

import json

import pandas as pd

from university_news.dashboard.data import (
    build_site_summary,
    discover_sites,
    load_artifact,
    load_consolidated_articles,
    region_for_site,
)


def write_jsonl(path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )


def test_dashboard_loads_sites_and_builds_summary(tmp_path) -> None:
    write_jsonl(
        tmp_path / "usp" / "urls.jsonl",
        [{"url": "https://example/a"}, {"url": "https://example/b"}],
    )
    write_jsonl(
        tmp_path / "usp" / "manifest.jsonl",
        [{"url": "https://example/a", "status_code": 200}],
    )
    write_jsonl(
        tmp_path / "usp" / "articles.jsonl",
        [{"url": "https://example/a", "title": "A", "tags": ["Pesquisa"]}],
    )
    write_jsonl(
        tmp_path / "usp" / "errors.jsonl",
        [{"url": "https://example/b", "stage": "fetching", "error": "Timeout"}],
    )
    (tmp_path / "vazio").mkdir()

    assert discover_sites(tmp_path) == ["usp"]
    urls = load_artifact(tmp_path, "urls")
    manifest = load_artifact(tmp_path, "manifest")
    articles = load_artifact(tmp_path, "articles")
    errors = load_artifact(tmp_path, "errors")
    summary = build_site_summary(urls, manifest, articles, errors, ["usp"])

    assert urls.iloc[0]["site"] == "usp"
    assert summary.to_dict("records") == [
            {
                "site": "USP",
                "região": "Sudeste",
                "urls": 2,
            "downloads": 1,
            "artigos": 1,
            "erros": 1,
            "conclusão": 0.5,
        }
    ]


def test_dashboard_ignores_empty_jsonl(tmp_path) -> None:
    site = tmp_path / "ufrgs"
    site.mkdir()
    (site / "errors.jsonl").write_text("", encoding="utf-8")

    assert discover_sites(tmp_path) == ["ufrgs"]
    assert load_artifact(tmp_path, "errors").empty


def test_dashboard_loads_only_consolidated_articles(tmp_path) -> None:
    path = tmp_path / "articles.json"
    path.write_text(
        json.dumps([{"journal": "USP", "title": "Notícia"}]), encoding="utf-8"
    )

    articles = load_consolidated_articles(path)

    assert articles.to_dict("records") == [{"journal": "USP", "title": "Notícia"}]
    assert all(dtype == object for dtype in articles.dtypes)


def test_dashboard_discards_invalid_article_dates(tmp_path) -> None:
    path = tmp_path / "articles.json"
    path.write_text(
        json.dumps(
            [
                {"journal": "FURG", "date": "0137-11-26"},
                {"journal": "USP", "date": "2026-07-17"},
            ]
        ),
        encoding="utf-8",
    )

    articles = load_consolidated_articles(path)

    assert articles["date"].isna().tolist() == [True, False]
    assert articles.loc[1, "date"] == "2026-07-17"


def test_dashboard_loads_compact_parquet_artifact(tmp_path) -> None:
    path = tmp_path / "dashboard_articles.parquet"
    pd.DataFrame(
        [
            {
                "url": "https://example/usp",
                "title": "Notícia",
                "subtitle": "Resumo",
                "author": "Ana",
                "date": "2026-07-17",
                "journal": "USP",
            }
        ]
    ).to_parquet(path)

    articles = load_consolidated_articles(path)

    assert articles.to_dict("records") == [
        {
            "url": "https://example/usp",
            "title": "Notícia",
            "subtitle": "Resumo",
            "author": "Ana",
            "date": "2026-07-17",
            "journal": "USP",
        }
    ]
    assert all(dtype == object for dtype in articles.dtypes)


def test_dashboard_maps_every_supported_university_region() -> None:
    assert region_for_site("UFT") == "Norte"
