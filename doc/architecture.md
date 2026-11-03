# Architecture

The project turns scattered university news portals into a structured,
searchable Portuguese corpus. Every stage reads and writes plain files under
`data/`, so each one can be rerun independently. The daily production path
records the same work in PostgreSQL.

## Data flow

```text
university portals
      │  discovery (listing pages → URLs)
      ▼
data/sites/<site>/urls.jsonl
      │  fetching (HTML download, retries, rate limits)
      ▼
data/sites/<site>/manifest.jsonl + raw HTML
      │  extraction (trafilatura, site rules, optional LLM fallback)
      ▼
data/sites/<site>/articles.jsonl
      │  preprocessing (validation, language detection, rejection log)
      ▼
data/sites/<site>/articles.preprocessed.jsonl
      │  compilation (URL deduplication, manifest, Parquet projection)
      ▼
data/compiled/articles.json + site_articles.parquet + manifest.json
      ├──► enrichment (LLM clipping metadata, JSON Schema validation)
      │        data/enriched/clipping/<version>/annotations.jsonl
      ├──► indexing (Qwen3 Embedding GGUF → LanceDB)
      │        data/indexes/lancedb/
      └──► website build (static pages + SQLite FTS5 bundle) ──► search API
```

## Package layout

```text
src/university_news/
├── cli.py          # single-university collection command
├── settings.py     # config/settings.toml loader and university-news-config
├── paths.py        # canonical locations of generated artifacts
├── sources/        # university catalog, listing selectors, and regions
├── pipeline/       # discovery, fetching, extraction, preprocessing, runner
├── analysis/       # data-quality reports and rejected-article inspection
├── enrichment/     # schema-validated LLM clipping metadata
├── search/         # embeddings, LanceDB indexing, and rank fusion
├── operations/     # PostgreSQL store, durable queue, daily discovery, workers
├── site/           # public website: build, search API, templates, static assets
└── shared/         # HTTP client, JSONL helpers, and shared article fields
```

Command-line parsing stays in the CLI modules; reusable behavior lives in the
domain modules beside them.

## Generated data layout

```text
data/
├── sites/<site>/       # raw and processed artifacts per university
├── compiled/           # full JSON, compact Parquet projection, and manifest
├── site/               # built website: pages and search bundle
├── enriched/           # versioned LLM annotations, errors, and run manifests
├── reports/            # preprocessing and quality reports
├── indexes/lancedb/    # local vector index
├── models/             # embedding model (GGUF)
├── postgres/           # live PostgreSQL cluster and its binaries
├── backups/            # daily database dumps
└── logs/               # operational logs and PID files
```

`data/`, `.env`, and generated service files are runtime artifacts and are not
committed.

## Article identity

An article is identified by `uuid5(NAMESPACE_URL, url)`. This identifier links
the compiled corpus, enrichment annotations, and the LanceDB index, so these
stages can join without extra keys. The PostgreSQL pipeline first canonicalizes
the URL (lowercase scheme and host, no fragment) in
`operations/identity.py`, so the same page always maps to one queue entry.
