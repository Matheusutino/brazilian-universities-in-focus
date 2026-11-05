# Implementation documentation

| Document | Covers |
| --- | --- |
| [architecture.md](architecture.md) | data flow, package layout, generated artifacts, article identity |
| [configuration.md](configuration.md) | `settings.toml`, secrets in `.env`, reading settings from the shell |
| [collection.md](collection.md) | university catalog, discovery, fetching, extraction backends |
| [preprocessing.md](preprocessing.md) | validation rules, rejection log, compilation, quality reports |
| [enrichment.md](enrichment.md) | LLM clipping metadata, providers, validation, resumability |
| [search.md](search.md) | Qwen3 embeddings, LanceDB index, hybrid retrieval with RRF |
| [database.md](database.md) | PostgreSQL setup, schema and migrations, job lifecycle, recovery, transparency, commands, runbook |
| [operations.md](operations.md) | daily cycle, workers, systemd services |
| [site.md](site.md) | public website: static pages, live PostgreSQL API, BM25, LanceDB, deployment |

Contributor conventions (style, tests, commits) are in
[`AGENTS.md`](../AGENTS.md).
