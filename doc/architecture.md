# Architecture

PostgreSQL is the source of truth for raw HTML, articles, LLM annotations, jobs, errors,
and runs. LanceDB holds the rebuildable vector index. The website queries
PostgreSQL directly; there is no separate website database.

## Production data flow

```text
university portals
    → daily URL discovery → PostgreSQL durable jobs
    → fetch → raw HTML bytes + response metadata in PostgreSQL
    → extract → preprocess → accepted/rejected articles in PostgreSQL
                              ├→ enrich → versioned annotations in PostgreSQL
                              └→ embed → LanceDB vectors
```

Migration 4 adds `site_documents`, a query projection in the same PostgreSQL
database. Triggers update it in the source transaction when articles or
annotations change. Only accepted articles are present. The most recently
stored annotation is selected per article, with profile ID as a deterministic
tie-breaker; older profiles remain available for research.

The projection stores searchable metadata, classification fields, and the
selected annotation. It has a `pg_search` BM25 index; it is not a second
source of truth. Removing or rejecting an article removes it from the
projection. API queries validate vector hits against that projection.

## Website

- List, filter, and article endpoints query PostgreSQL using a bounded read-only
  connection pool. No export or build is needed to expose newly accepted news.
- BM25 searches title, subtitle, summary, and keywords in PostgreSQL.
- Semantic retrieval embeds only the query and searches LanceDB.
- The API merges the rankings with RRF and loads authoritative article metadata
  from PostgreSQL.
- Institutional pages and aggregate charts are built from a consistent
  PostgreSQL snapshot. Their refresh is independent of the live endpoints.
- JSONL, JSON, and annotation Parquet exports remain research/interchange
  formats, not website inputs.

## Package layout

```text
src/university_news/
├── pipeline/       # discovery, fetching, extraction, preprocessing
├── sources/        # university catalog, pagination, metadata selectors, regions
├── operations/     # PostgreSQL queue, migrations, projections, worker persistence
├── enrichment/     # validated LLM client and optional file-based batch CLI
├── search/         # Qwen embeddings, LanceDB index, rank fusion
├── site/           # PostgreSQL queries, BM25 setup, API, static pages
├── analysis/       # corpus-quality reports
└── shared/         # HTTP, I/O, and annotation presentation
```

## Identity and recovery

All article IDs use UUID5 over the same canonical URL: lowercase scheme/host,
no fragment, preserved path/query. Worker, batch indexer, and enrichment use
`operations/identity.py`. Existing indexes built with different normalization
must be rebuilt or reconciled before switching traffic.

LanceDB writes and PostgreSQL job completion are separate operations. Index
writes are idempotent by article ID; failed jobs can be retried. Preserve the
embedding model/version metadata and rebuild the index if its contract changes.

## Files

Production workers store HTML in PostgreSQL, not on disk. Historical exports
may still exist under `data/sites/`. `data/models/` contains GGUF
models; `data/indexes/lancedb-v2/` contains vectors; `data/site/public/` contains
generated pages and their snapshot statistics. Research exports remain under
`data/compiled/` and `data/enriched/`. Runtime data is not committed.
