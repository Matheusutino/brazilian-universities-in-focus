from __future__ import annotations

import json

from dashboard.data import build_site_summary, discover_sites, load_artifact


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
