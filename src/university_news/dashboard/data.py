from __future__ import annotations

from pathlib import Path
import json

import duckdb
import pandas as pd

from university_news.sources.catalog import region_for_site


ARTIFACTS = ("urls", "manifest", "articles", "errors")

# The consolidated corpus starts in 1999; earlier dates are invalid metadata.
EARLIEST_ARTICLE_DATE = pd.Timestamp("1990-01-01")


def load_consolidated_articles(path: Path) -> pd.DataFrame:
    """Load the consolidated article source consumed by the dashboard."""
    if not path.is_file() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Invalid consolidated JSON at {path}") from exc
    if not isinstance(records, list):
        raise ValueError(f"Consolidated JSON must contain a list: {path}")
    if any(not isinstance(record, dict) for record in records):
        raise ValueError(f"Consolidated JSON contains an invalid record: {path}")
    articles = pd.DataFrame(records)
    if "date" in articles:
        parsed_dates = pd.to_datetime(
            articles["date"], format="ISO8601", errors="coerce"
        )
        articles.loc[parsed_dates < EARLIEST_ARTICLE_DATE, "date"] = None
    return articles


def discover_sites(data_directory: Path) -> list[str]:
    if not data_directory.is_dir():
        return []
    return sorted(
        directory.name
        for directory in data_directory.iterdir()
        if directory.is_dir()
        and any((directory / f"{artifact}.jsonl").is_file() for artifact in ARTIFACTS)
    )


def artifact_files(
    data_directory: Path, artifact: str, sites: list[str] | None = None
) -> list[Path]:
    if artifact not in ARTIFACTS:
        raise ValueError(f"Unknown artifact: {artifact}")
    selected = set(sites or discover_sites(data_directory))
    return [
        path
        for site in sorted(selected)
        if (path := data_directory / site / f"{artifact}.jsonl").is_file()
        and path.stat().st_size > 0
    ]


def load_artifact(
    data_directory: Path, artifact: str, sites: list[str] | None = None
) -> pd.DataFrame:
    files = artifact_files(data_directory, artifact, sites)
    if not files:
        return pd.DataFrame()

    paths = [str(path.resolve()) for path in files]
    with duckdb.connect() as connection:
        frame = connection.execute(
            """
            SELECT
                regexp_extract(filename, '[/\\\\]([^/\\\\]+)[/\\\\][^/\\\\]+$', 1)
                    AS site,
                * EXCLUDE (filename)
            FROM read_json_auto(
                ?,
                format = 'newline_delimited',
                union_by_name = true,
                filename = true
            )
            """,
            [paths],
        ).df()
    return frame


def build_site_summary(
    urls: pd.DataFrame,
    manifest: pd.DataFrame,
    articles: pd.DataFrame,
    errors: pd.DataFrame,
    sites: list[str],
) -> pd.DataFrame:
    def count_by_site(frame: pd.DataFrame, column: str) -> pd.Series:
        if frame.empty or "site" not in frame or column not in frame:
            return pd.Series(dtype="int64")
        return frame.drop_duplicates(["site", column]).groupby("site")[column].count()

    discovered = count_by_site(urls, "url")
    downloaded = count_by_site(manifest, "url")
    extracted = count_by_site(articles, "url")
    if errors.empty or "site" not in errors:
        error_counts = pd.Series(dtype="int64")
    else:
        error_counts = errors.groupby("site").size()

    rows: list[dict[str, object]] = []
    for site in sites:
        url_count = int(discovered.get(site, 0))
        article_count = int(extracted.get(site, 0))
        rows.append(
            {
                "site": site.upper(),
                "região": region_for_site(site),
                "urls": url_count,
                "downloads": int(downloaded.get(site, 0)),
                "artigos": article_count,
                "erros": int(error_counts.get(site, 0)),
                "conclusão": article_count / url_count if url_count else 0.0,
            }
        )
    return pd.DataFrame(rows)
