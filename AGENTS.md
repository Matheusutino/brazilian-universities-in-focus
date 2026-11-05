# Repository Guidelines

## Project Structure & Module Organization

Application code lives in `src/university_news/`. Key packages are `pipeline/` for collection and preprocessing, `enrichment/` for LLM metadata, `search/` for LanceDB retrieval, `site/` for static pages and the PostgreSQL search API, and `operations/` for the PostgreSQL queue. Shared helpers belong in `shared/`; university definitions belong in `sources/`. Tests are in `tests/test_*.py`, settings and schemas in `config/`, implementation docs in `doc/`, and deployment utilities in `scripts/` and `deploy/`. Treat `data/`, `.env`, and generated service files as uncommitted runtime artifacts.

## Build, Test, and Development Commands

- `uv sync --dev`: create/update the Python 3.12 environment and install test dependencies.
- `uv run pytest`: run the complete test suite.
- `uv run pytest tests/test_fetching.py -q`: run one focused test module.
- `TEST_DATABASE_URL=... uv run pytest tests/test_postgres_store.py tests/test_site.py`: run integration tests against dedicated PostgreSQL with pg_search 0.26.4+ (the database is wiped).
- `uv run university-news-daily --sites usp`: discover and queue one university.
- `uv run university-news-worker --stages fetch extract preprocess --exit-when-empty`: process queued articles into PostgreSQL.
- `uv run university-news-preprocess --data-root data --workers 8`: clean and compile collected articles.
- `uv run university-news-site build && uv run university-news-site serve`: build and serve the website locally.
- `uv run university-news-site init-search`: apply migrations and create BM25 indexes using `DATABASE_URL`; the server must have pg_search preloaded.
- `uv run university-news-config enrichment provider`: inspect a configured setting.

GPU indexing builds `llama-cpp-python` with CUDA settings from `pyproject.toml`; contributors without CUDA should avoid rebuilding that dependency unless necessary.

## Coding Style & Naming Conventions

Use four-space indentation, type hints, and PEP 8 naming: `snake_case` for modules and functions, `PascalCase` for classes, and `UPPER_CASE` for constants. Keep argument parsing in CLI modules and reusable behavior in domain modules. Prefer `pathlib.Path`, explicit UTF-8 encoding, structured records, and narrow exception handling. No formatter or linter is configured, so preserve surrounding style and group imports as standard library, third-party, then local.

## Testing Guidelines

Tests use `pytest` and `pytest-asyncio` with automatic async mode. Schema changes need a new entry in `MIGRATIONS` (never edit an applied one) and an integration test. Name files `test_<area>.py` and functions `test_<behavior>`. Use `tmp_path` for filesystem work and `monkeypatch` or local fakes for external boundaries. Add regression tests for behavioral fixes. No coverage threshold is configured; prioritize outputs, persisted artifacts, retries, and failure paths.

## Commit & Pull Request Guidelines

Existing history uses short lowercase subjects such as `update`, but new commits should use concise imperative summaries (for example, `Handle expired worker leases`). Keep commits focused and do not include generated data or secrets. Pull requests should explain the change, list verification commands, identify configuration or schema migrations, and link relevant issues. Include screenshots for website changes and sample CLI output for user-visible command changes.

## Security & Configuration

Copy `.env.example` to `.env`; never commit API keys or `DATABASE_URL`. Put non-secret defaults in `config/settings.toml` and update `.env.example` when introducing a required secret.
