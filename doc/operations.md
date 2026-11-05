# Durable daily pipeline

The production path uses PostgreSQL as its source of truth, so new articles
are processed incrementally and interrupted jobs can be retried. Database setup, the
schema, job lifecycle, recovery, and the full command reference are in
[database.md](database.md).

## Setup

For a fresh pipeline, initialize PostgreSQL/BM25 and collect directly into the
database. Do not run the file pipeline followed by a JSON import:

```bash
uv run university-news-site init-search
uv run university-news-daily --max-pages 4000
uv run university-news-worker --stages fetch extract preprocess --exit-when-empty
uv run university-news-worker --stages enrich --exit-when-empty
uv run university-news-worker --stages embed --exit-when-empty
```

The high page cap walks the configured archives on an empty database; subsequent
daily runs stop after known pages. Workers can run alongside discovery through
the systemd services. URLs, extracted/preprocessed records, rejection reasons,
LLM annotations, jobs, and errors are persisted in PostgreSQL, including JSONB
payloads. Raw HTML bytes and response metadata also live in PostgreSQL;
extraction reads them directly, so workers need no shared HTML directory.
Embeddings are written directly to LanceDB. There is no intermediate HTML file
or article JSON/JSONL stage in production.

Migration 5 adds HTML content to `raw_documents`. It preserves legacy path-only
rows but does not read or import local files. Such documents must be fetched
again before extraction; existing extracted articles remain available. For a
fresh collection, simply initialize the schema and run discovery/workers above.
The optional offline file commands remain available for research.

### Optional historical import

Start PostgreSQL and write `DATABASE_URL` to `.env` (see
[database.md](database.md#setting-up-postgresql)), then import the existing
corpus once:

```bash
./scripts/setup_local_postgres.sh
uv run university-news-db import-existing-data --data-root data
```

The import registers every previously discovered URL and accepted article
without creating jobs, so the historical collection is not reprocessed.

## Daily cycle

```bash
uv run university-news-daily                                   # enqueue unseen URLs
uv run university-news-worker --stages fetch extract preprocess --exit-when-empty
uv run university-news-worker --stages enrich --exit-when-empty
uv run university-news-worker --stages embed --exit-when-empty
uv run university-news-site build                            # refresh static pages/charts
```

Daily discovery checks at most `[daily_discovery].max_pages` listing pages per
site and stops after `stop_after_empty_pages` pages with no new URLs. `export`
is an optional research export. Website endpoints read PostgreSQL directly;
only static pages and aggregate charts wait for the next build.

## Queue semantics

- Jobs are unique per article and stage (`fetch`, `extract`, `preprocess`,
  `enrich`, `embed`).
- Workers claim jobs with PostgreSQL leases and `FOR UPDATE SKIP LOCKED`.
  Each claim has a unique token; a stale attempt cannot renew, fail, complete,
  or begin a fenced result write after it expires or is reclaimed.
- Slow calls renew their leases with a heartbeat using a separate connection.
  A row lock fences writes to PostgreSQL and LanceDB against another claim
  while the PostgreSQL connection remains alive. PostgreSQL and LanceDB cannot
  commit atomically. Remote requests can still be repeated after a crash or
  lease loss.
- New articles use priority 100. Explicit backfill uses priority 10.
- A pipeline, model, prompt, or schema version change never reschedules old
  articles automatically.

Historical work and retries are always explicit:

```bash
uv run university-news-db enqueue-backfill --stage enrich --limit 100
uv run university-news-db enqueue-backfill --stage embed --limit 100
uv run university-news-db retry --stage enrich
uv run university-news-db status
```

`status` reports queue totals, accepted and rejected article totals, the ten
most recent runs, and the twenty most recent errors. `retry` resets the
attempt counter for the selected failed jobs. Status and backfill output also
estimate LLM calls and ETA from measured runtimes.

The enrichment worker uses the selected provider's `workers` setting, falling
back to `[enrichment].workers`, as bounded concurrency. For example, sixteen
workers mean at most sixteen active HTTP requests, even when each
article is split into two calls. All threads of one worker process share a
single client, so `requests_per_minute` is respected across them. Each worker
process is recorded in `pipeline_runs`, and stopping it with SIGTERM or Ctrl-C
lets in-flight jobs finish.

## systemd user services

```bash
./scripts/install_user_services.sh
# On machines where user services stop after logout:
sudo loginctl enable-linger "$USER"
```

This installs daily discovery, an hourly static-page build, a core worker, one
enrichment worker per provider in `[enrichment].providers`
(`university-news-enrichment-worker@<provider>`), a GPU embedding worker, and
the website (`university-news-site`), and a daily database backup to
`data/backups/` (`university-news-backup.timer`). Providers removed from
`[enrichment].providers` are disabled on the next install. The hourly publish run
builds pages directly from PostgreSQL; it does not export a second website database.
Run `university-news-site init-search` before installing the website service.
The templates are in `deploy/systemd/`.
The workers start after `university-news-postgres` when the rootless database
is used.

```bash
systemctl --user status 'university-news-enrichment-worker@*'
journalctl --user -u university-news-enrichment-worker@nvidia_nim -f
systemctl --user stop university-news-enrichment-worker@openwebui   # graceful: run becomes "interrupted"
```
