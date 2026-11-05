# Public website

The site uses two databases: PostgreSQL for live articles, annotations,
filters, and BM25; LanceDB for semantic retrieval. Static pages are generated
from PostgreSQL. The web server needs database access and the local query
embedding model for hybrid search.

## Setup and migration

The PostgreSQL server must have ParadeDB `pg_search >= 0.26.4` and its
`vector` extension dependency installed, with `pg_search` in
`shared_preload_libraries`. The vector dependency does not move embeddings
out of LanceDB. The Compose image builds these extensions for PostgreSQL 16
on amd64:

```bash
POSTGRES_PASSWORD=... docker compose up -d --build
uv sync --dev
uv run university-news-site init-search
uv run university-news-site build
uv run university-news-site serve
```

Use the existing PostgreSQL volume; do not remove it. Take and test a database
backup before changing the server image. If PostgreSQL runs outside Docker,
install extensions against the server's actual version first. The old portable
PostgreSQL 16.2 binaries are too old for the tested pg_search binary; the
integration suite was verified on PostgreSQL 16.15 with pg_search 0.26.4.

`init-search` uses the writer `DATABASE_URL`, applies migration 4, backfills
existing accepted articles, and creates the BM25 index. It is an explicit
deployment step, never performed by API requests. A fresh collection can run
directly into PostgreSQL; importing the historical corpus is optional.
File-only LLM annotations must be imported through a deliberate data migration
before retiring those exports; they are not automatically loaded by the site.

The website uses `SITE_DATABASE_URL`, falling back to `DATABASE_URL`. Prefer
a separate role with only schema usage and SELECT on `site_documents` and
`enrichment_profiles`. The connection pool additionally enforces read-only
sessions, a statement timeout, and a configurable maximum connection count:

```sql
GRANT USAGE ON SCHEMA public, paradedb, pdb TO university_news_site;
GRANT SELECT ON site_documents, enrichment_profiles TO university_news_site;
```

Create the role and its password through your normal credential-management
process. Put the DSN in `.env`, never in versioned settings.

## What changed

The site no longer reads or generates SQLite, article Parquet projections,
or combined annotation exports. Old `site.sqlite`, `site.sqlite.tmp`,
`site_articles.parquet`, `dashboard_articles.parquet`, and the former
top-level `data/site/stats.json` are not used. Keep an archival copy until
the PostgreSQL corpus/annotation counts and the new deployment are verified.

Only static pages need rebuilding. The hourly publish service now runs
`university-news-site build`; article and annotation exports are optional
research commands. Reinstall the systemd templates after updating. Rollback
requires both the previous application version and the corresponding database
backup; test restoration separately rather than dropping the production schema.

## Search and API

`GET /api/articles` accepts `q`, region, university, date bounds, category,
CNPq field, content type, analyzed-only, mode, page, and page size.

- BM25 weights title ×4, keywords ×2, subtitle ×1.5, and summary ×1.
  ASCII folding makes accented and unaccented queries equivalent.
- LanceDB prefilters university, region, and dates. The API checks every hit
  against PostgreSQL and grows the candidate window for selective annotation
  filters, up to 1,920 candidates.
- RRF combines up to 60 candidates from each ranking. Search totals describe
  retrieved candidates (`total_is_exact=false`); unqueried listings have an
  exact count and deterministic pagination.
- Query embeddings are cached. The model runs on CPU by default under a lock.
  When semantic retrieval fails or is still loading, results fall back to BM25.
- `--lexical-only` disables embeddings, not PostgreSQL or the BM25 index.
- `GET /api/articles/{id}` returns an article and its selected annotation.
- `GET /healthz` checks PostgreSQL and index availability, returns 503 on
  database failure, and reports the periodically verified semantic status,
  timestamps, row coverage, ANN availability, and timings. Query failures mark
  semantics unhealthy even while BM25 remains available.
- `GET /readyz` returns 503 until the configured hybrid service passes a real
  embedding → LanceDB → published-article probe. Explicit lexical-only mode
  only requires PostgreSQL/BM25. See [search.md](search.md) for diagnostics.
- `/api/docs` documents the API.

Keep LanceDB and model files available to the API process. Static-only hosting
is insufficient for search. Address, port, pool size, timeouts, and query model
settings live under `[site]` in `config/settings.toml`.

## Pages and static builds

`/`, `/explorar/`, `/analises/`, `/universidades/`, `/criterios/`, and
`/sobre/` are built as HTML. Article detail pages at `/noticia/{id}` read
PostgreSQL on demand. Templates live in `site/templates/`; CSS and plain
JavaScript live in `site/static/`.

Charts use only the selected annotation per accepted article; profile counts
reflect that same selection. Keyword variants are consolidated for aggregate
charts. Raw annotation detail remains faithful to the persisted profile.

Generated pages and their `stats.json` are staged together under
`data/site/public/`. Static charts reflect the last build, while API data is
live. Deploy/rebuild without overlapping publishers and keep the last known
working release available for rollback.

University logos and provenance are in `site/static/logos/` and
`CREDITS.json`. Missing logos fall back to institution initials.
