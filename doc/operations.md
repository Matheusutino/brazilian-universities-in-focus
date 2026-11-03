# Durable daily pipeline

The production path uses PostgreSQL as its source of truth, so new articles
are processed incrementally and crashes never lose work. Database setup, the
schema, job lifecycle, recovery, and the full command reference are in
[database.md](database.md).

## Setup

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
uv run university-news-db export --data-root data             # publish to data/compiled
```

Daily discovery checks at most `[daily_discovery].max_pages` listing pages per
site and stops after `stop_after_empty_pages` pages with no new URLs. `export`
atomically rewrites the compiled artifacts the website is built from.

## Queue semantics

- Jobs are unique per article and stage (`fetch`, `extract`, `preprocess`,
  `enrich`, `embed`).
- Workers claim jobs with PostgreSQL leases and `FOR UPDATE SKIP LOCKED`, so
  several workers never claim the same job, and a crashed worker's jobs become
  claimable again when the lease expires.
- Slow calls renew their leases with a heartbeat.
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

The enrichment worker uses `[enrichment].workers` as bounded concurrency.
Sixteen workers mean at most sixteen active HTTP requests, even when each
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

This installs daily discovery, an hourly export, a core worker, one
enrichment worker per provider in `[enrichment].providers`
(`university-news-enrichment-worker@<provider>`), a GPU embedding worker, and
the website (`university-news-site`), and a daily database backup to
`data/backups/` (`university-news-backup.timer`). Providers removed from
`[enrichment].providers` are disabled on the next install. The hourly publish run exports articles and
annotations and rebuilds the site. The templates are in `deploy/systemd/`.
The workers start after `university-news-postgres` when the rootless database
is used.

```bash
systemctl --user status 'university-news-enrichment-worker@*'
journalctl --user -u university-news-enrichment-worker@nvidia_nim -f
systemctl --user stop university-news-enrichment-worker@openwebui   # graceful: run becomes "interrupted"
```
