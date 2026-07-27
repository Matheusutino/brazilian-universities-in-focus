import json

from university_news.analysis.quality import build_quality_report, run
from university_news.pipeline.preprocessing import (
    PREPROCESSED_ARTICLES_FILENAME,
    REJECTED_ARTICLES_FILENAME,
)


def write_jsonl(path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def test_quality_report_summarizes_site_data(tmp_path) -> None:
    site = tmp_path / "sites" / "usp"
    accepted = {
        "url": "https://example/accepted",
        "author": "Ana",
        "title": "Pesquisa universitária avança",
        "subtitle": "Resultados foram divulgados",
        "date": "2026-07-20",
        "text": "Este artigo possui conteúdo suficiente para análise estatística.",
        "tags": ["Pesquisa"],
        "links": [],
        "images": ["https://example/image.jpg"],
    }
    rejected = {
        "url": "https://example/rejected",
        "author": None,
        "title": "Artigo sem autor",
        "text": "Texto do artigo removido.",
    }
    write_jsonl(site / "articles.jsonl", [accepted, rejected, accepted])
    write_jsonl(site / PREPROCESSED_ARTICLES_FILENAME, [accepted])
    write_jsonl(
        site / REJECTED_ARTICLES_FILENAME,
        [{"site": "usp", "reason": "missing_author", "article": rejected}],
    )

    report = build_quality_report(tmp_path)
    summary = report["sites"][0]

    assert report["totals"]["source_articles"] == 3
    assert summary["accepted_articles"] == 1
    assert summary["removed_articles"] == 2
    assert summary["duplicate_urls"] == 1
    assert summary["unclassified_removed"] == 1
    assert summary["rejection_reasons"]["missing_author"] == 1
    assert summary["field_completeness"]["subtitle"]["rate"] == 1.0
    assert summary["dates"] == {
        "earliest": "2026-07-20",
        "latest": "2026-07-20",
        "missing_or_invalid": 0,
    }


def test_quality_cli_writes_report_and_filters_rejections(
    tmp_path, capsys
) -> None:
    site = tmp_path / "sites" / "ufal"
    article = {
        "url": "https://example/rejected",
        "title": "Notícia removida",
        "author": None,
        "text": "Texto.",
    }
    write_jsonl(site / "articles.jsonl", [article])
    write_jsonl(site / PREPROCESSED_ARTICLES_FILENAME, [])
    write_jsonl(
        site / REJECTED_ARTICLES_FILENAME,
        [{"site": "ufal", "reason": "missing_author", "article": article}],
    )

    assert run(["--data-root", str(tmp_path), "summary"]) == 0
    report_path = tmp_path / "reports" / "quality_report.json"
    assert report_path.is_file()

    assert (
        run(
            [
                "--data-root",
                str(tmp_path),
                "rejected",
                "--site",
                "ufal",
                "--reason",
                "missing_author",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "[missing_author] UFAL" in output
    assert "https://example/rejected" in output


def test_quality_report_uses_historical_preprocessing_stats(tmp_path) -> None:
    site = tmp_path / "sites" / "usp"
    write_jsonl(
        site / "articles.jsonl",
        [
            {"url": "https://example/accepted"},
            {"url": "https://example/rejected"},
        ],
    )
    write_jsonl(
        site / PREPROCESSED_ARTICLES_FILENAME,
        [{"url": "https://example/accepted"}],
    )
    (site / "preprocess_stats.json").write_text(
        json.dumps({"removed_missing_author": 1}),
        encoding="utf-8",
    )

    report = build_quality_report(tmp_path)
    summary = report["sites"][0]

    assert summary["categorized_removed"] == 1
    assert summary["unclassified_removed"] == 0
    assert summary["rejection_counts_source"] == "preprocessing_stats"
    assert summary["rejection_reasons"]["missing_author"] == 1
