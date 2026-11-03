# Preprocessing, compilation, and quality

## Preprocessing

```bash
uv run university-news-preprocess --data-root data --workers 8
```

Each extracted article is validated before it reaches the corpus. Articles are
rejected when they have:

| Reason | Rule |
| --- | --- |
| `missing_author` | no author |
| `empty_title` / `empty_text` | blank title or body |
| `short_title` | fewer than 3 words in the title |
| `short_text` | fewer than 10 words in the body |
| `non_portuguese` | the body is not detected as Portuguese (Lingua) |

Accepted articles go to `articles.preprocessed.jsonl`. Rejected ones go to
`articles.rejected.jsonl` with the reason and the original record, so no data
is silently lost. Per-site counts are written to `preprocess_stats.json`.

## Compilation

Preprocessing ends by compiling every site into `data/compiled/`. To rebuild
only the consolidated artifacts:

```bash
uv run university-news-compile --data-root data
```

| Artifact | Contents |
| --- | --- |
| `articles.json` | full accepted corpus |
| `site_articles.parquet` | compact projection (URL, title, subtitle, author, date, university) the website is built from |
| `manifest.json` | counts per university, pipeline errors by stage and type, rejection categories, field completeness, date coverage, artifact paths, preprocessing parameters, file size, and SHA-256 checksum |

After updating from the former `dashboard_articles.parquet` export, run
`uv run university-news-compile --data-root data` before building the website.
The compiled manifest uses schema version 2 and names this projection
`site_dataset`; compilation results report its path as `site_output`.

Compilation keeps the first occurrence of each URL. Later duplicates are
dropped before they can reach the vector index.

## Quality analysis

```bash
uv run university-news-analyze summary
```

This writes `data/reports/quality_report.json`, which covers removal reasons,
acceptance rates, duplicate URLs, field completeness, text-length
distributions, and date coverage, both per site and overall.

Inspect rejected articles:

```bash
uv run university-news-analyze rejected --site usp --reason missing_author
uv run university-news-analyze rejected --reason non_portuguese --limit 50
uv run university-news-analyze rejected --site ufal --full
```

When per-site statistics record more rejections than the rejection log holds,
the report uses the statistics. In that case it reports separately how many
full rejection records are available.
