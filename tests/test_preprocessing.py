import json

from university_news.pipeline.preprocessing import (
    PREPROCESSED_ARTICLES_FILENAME,
    REJECTED_ARTICLES_FILENAME,
    compile_articles,
    preprocess_records,
    preprocess_file,
    run,
)


class PortugueseOnlyDetector:
    def is_portuguese(self, text: str) -> bool:
        return not text.startswith("English")


def write_jsonl(path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def test_preprocess_removes_articles_missing_required_content(tmp_path) -> None:
    path = tmp_path / "sites" / "usp" / "articles.jsonl"
    valid = {
        "url": "https://example/valid",
        "author": "Ana",
        "title": "Titulo valido da materia",
        "text": "Este texto possui palavras suficientes para passar pelo filtro de qualidade.",
    }
    source_records = [
        valid,
        {"url": "https://example/no-author", "author": None, "title": "Titulo", "text": "Texto"},
        {"url": "https://example/blank-title", "author": "Ana", "title": "  ", "text": "Texto"},
        {"url": "https://example/no-text", "author": "Ana", "title": "Titulo", "text": ""},
        {"url": "https://example/short-title", "author": "Ana", "title": "Titulo curto", "text": valid["text"]},
        {"url": "https://example/short-text", "author": "Ana", "title": valid["title"], "text": "Texto curto demais"},
        {"url": "https://example/english", "author": "Ana", "title": valid["title"], "text": "English text with enough words to reach the language detection stage."},
    ]
    write_jsonl(path, source_records)

    stats = preprocess_file(path, PortugueseOnlyDetector())

    assert stats.total == 7
    assert stats.kept == 1
    assert stats.removed == 6
    assert stats.removed_missing_author == 1
    assert stats.removed_empty_title == 1
    assert stats.removed_empty_text == 1
    assert stats.removed_short_title == 1
    assert stats.removed_short_text == 1
    assert stats.removed_non_portuguese == 1
    assert [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] == source_records
    output = path.parent / PREPROCESSED_ARTICLES_FILENAME
    assert [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()] == [valid]
    rejected = [
        json.loads(line)
        for line in (path.parent / REJECTED_ARTICLES_FILENAME)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [record["reason"] for record in rejected] == [
        "missing_author",
        "empty_title",
        "empty_text",
        "short_title",
        "short_text",
        "non_portuguese",
    ]
    assert rejected[0]["article"]["url"] == "https://example/no-author"


def test_preprocess_dry_run_does_not_change_articles(tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / "sites" / "uem" / "articles.jsonl"
    record = {"url": "https://example/a", "author": "", "title": "Titulo", "text": "Texto"}
    write_jsonl(path, [record])
    monkeypatch.setattr(
        "university_news.pipeline.preprocessing.load_portuguese_detector",
        lambda: PortugueseOnlyDetector(),
    )

    result = run(["--data-root", str(tmp_path), "--dry-run"])

    assert result == 0
    output_lines = capsys.readouterr().out.splitlines()
    assert json.loads(output_lines[-1])["removed"] == 1
    assert [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] == [record]


def test_compile_adds_journal_to_preprocessed_articles(tmp_path) -> None:
    write_jsonl(
        tmp_path / "sites" / "usp" / PREPROCESSED_ARTICLES_FILENAME,
        [{"url": "https://example/usp", "title": "USP"}],
    )
    write_jsonl(
        tmp_path / "sites" / "uem" / PREPROCESSED_ARTICLES_FILENAME,
        [{"url": "https://example/uem", "title": "UEM"}],
    )

    stats = compile_articles(tmp_path)

    assert stats.journals == 2
    assert stats.articles == 2
    assert json.loads((tmp_path / "compiled" / "articles.json").read_text(encoding="utf-8")) == [
        {"url": "https://example/uem", "title": "UEM", "journal": "UEM"},
        {"url": "https://example/usp", "title": "USP", "journal": "USP"},
    ]
    manifest = json.loads((tmp_path / "compiled" / "manifest.json").read_text())
    assert manifest["dataset"]["articles"] == 2
    assert manifest["dataset"]["sha256"]
    assert manifest["totals"]["accepted_articles"] == 2
    assert [site["site"] for site in manifest["universities"]] == ["uem", "usp"]


def test_manifest_uses_preprocessing_stats_for_historical_rejections(tmp_path) -> None:
    site = tmp_path / "sites" / "usp"
    source_records = [
        {"url": "https://example/accepted", "title": "Accepted"},
        {"url": "https://example/rejected", "title": "Rejected"},
    ]
    write_jsonl(site / "articles.jsonl", source_records)
    write_jsonl(site / PREPROCESSED_ARTICLES_FILENAME, source_records[:1])
    (site / "preprocess_stats.json").write_text(
        json.dumps(
            {
                "total": 2,
                "kept": 1,
                "removed_missing_author": 1,
                "removed_empty_title": 0,
                "removed_empty_text": 0,
                "removed_short_title": 0,
                "removed_short_text": 0,
                "removed_non_portuguese": 0,
                "removed": 1,
            }
        ),
        encoding="utf-8",
    )

    compile_articles(tmp_path)

    manifest = json.loads((tmp_path / "compiled" / "manifest.json").read_text())
    site_report = manifest["universities"][0]
    assert site_report["removed_articles"] == 1
    assert site_report["rejections"]["categorized"] == 1
    assert site_report["rejections"]["detailed_records"] == 0
    assert site_report["rejections"]["by_reason"] == {"missing_author": 1}


def test_preprocess_writes_site_and_global_stats(tmp_path, monkeypatch) -> None:
    write_jsonl(
        tmp_path / "sites" / "usp" / "articles.jsonl",
        [{"url": "https://example/a", "author": "Ana", "title": "Titulo valido aqui", "text": "Este texto possui palavras suficientes para passar pelo filtro de qualidade."}],
    )
    monkeypatch.setattr(
        "university_news.pipeline.preprocessing.load_portuguese_detector",
        lambda: PortugueseOnlyDetector(),
    )

    assert run(["--data-root", str(tmp_path)]) == 0

    site_stats = json.loads((tmp_path / "sites" / "usp" / "preprocess_stats.json").read_text())
    global_stats = json.loads((tmp_path / "reports" / "preprocessing.json").read_text())
    assert site_stats["kept"] == 1
    assert global_stats["totals"]["kept"] == 1
    assert global_stats["compiled"]["articles"] == 1


def test_global_stats_include_previously_processed_sites(tmp_path, monkeypatch) -> None:
    previous_site = tmp_path / "sites" / "uem"
    previous_site.mkdir(parents=True)
    (previous_site / "preprocess_stats.json").write_text(
        json.dumps(
            {
                "site": "uem",
                "total": 3,
                "kept": 2,
                "removed_missing_author": 1,
                "removed_empty_title": 0,
                "removed_empty_text": 0,
                "removed_short_title": 0,
                "removed_short_text": 0,
                "removed_non_portuguese": 0,
                "removed": 1,
            }
        ),
        encoding="utf-8",
    )
    write_jsonl(
        tmp_path / "sites" / "usp" / "articles.jsonl",
        [
            {
                "url": "https://example/a",
                "author": "Ana",
                "title": "Titulo valido aqui",
                "text": "Este texto possui palavras suficientes para passar pelo filtro.",
            }
        ],
    )
    monkeypatch.setattr(
        "university_news.pipeline.preprocessing.load_portuguese_detector",
        lambda: PortugueseOnlyDetector(),
    )

    assert run(["--data-root", str(tmp_path), "--sites", "usp"]) == 0

    report = json.loads((tmp_path / "reports" / "preprocessing.json").read_text())
    assert report["totals"]["total"] == 4
    assert {site["site"] for site in report["sites"]} == {"uem", "usp"}


def test_preprocess_classifies_language_in_parallel_and_reports_progress() -> None:
    records = [
        {"url": "https://example/a", "author": "Ana", "title": "Titulo valido aqui", "text": "Este texto possui palavras suficientes para passar pelo filtro de qualidade."},
        {"url": "https://example/b", "author": "Bia", "title": "Titulo ingles aqui", "text": "English text with enough words to reach the language detection stage."},
        {"url": "https://example/c", "author": "", "title": "C", "text": "Texto"},
    ]
    progress: list[tuple[int, int, int, int]] = []

    _, stats = preprocess_records(
        records,
        "usp",
        PortugueseOnlyDetector(),
        lambda *values: progress.append(values),
        workers=2,
    )

    assert stats.kept == 1
    assert stats.removed == 2
    assert progress[0] == (0, 3, 0, 0)
    assert progress[-1] == (3, 3, 1, 2)
