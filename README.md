# Brazilian Universities in Focus

A local-first pipeline for collecting, cleaning, indexing, and exploring news
published by Brazilian universities.

The project includes:

- asynchronous URL discovery and HTML fetching;
- structured article extraction and Portuguese-language preprocessing;
- local semantic indexing with Qwen3 Embedding GGUF and LanceDB;
- hybrid semantic and BM25 search;
- a Streamlit dashboard.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- An NVIDIA GPU and CUDA toolchain for GPU-accelerated embeddings

Install the project:

```bash
uv sync
cp .env.example .env
```

If `uv` was installed in the current shell but is not yet in `PATH`, run:

```bash
source "$HOME/.local/bin/env"
```

OpenRouter is optional and is only required for LLM-based extraction. Local
search uses the GGUF model configured in `.env`.

Clipping enrichment uses the OpenAI Python SDK with either OpenWebUI or NVIDIA
NIM. Configure secrets only in `.env`. OpenWebUI remains the default:

```dotenv
OPENWEBUI_URL=https://agents4gov.icmc.usp.br/
OPENWEBUI_API_KEY=replace-with-your-key
OPENWEBUI_MODEL=externo.revejo-qwen3.6-35b-a3b
OPENWEBUI_DISABLE_THINKING=true
```

For NVIDIA's hosted NIM API, fill these values and select the provider:

```dotenv
ENRICHMENT_PROVIDER=nvidia_nim
NVIDIA_NIM_URL=https://integrate.api.nvidia.com/v1
NVIDIA_API_KEY=replace-with-your-key
NVIDIA_NIM_MODEL=provider/model-name
NVIDIA_NIM_ENRICHMENT_CALLS=1
NVIDIA_NIM_MAX_INPUT_CHARS=120000
NVIDIA_NIM_MAX_OUTPUT_TOKENS=16384
NVIDIA_NIM_DISABLE_THINKING=true
```

The URL can instead point to a self-hosted NIM, such as
`http://localhost:8000`. Input and output limits are model-dependent, so adjust
them to the selected model's context window.
Thinking is disabled by default for NIM because structured extraction benefits
more from a direct JSON response. Set `NVIDIA_NIM_DISABLE_THINKING=false` when a
task genuinely needs the model's extended reasoning mode.

## Collect news

Run every pipeline stage for one university:

```bash
uv run university-news usp --stage all
```

Stages can also run independently:

```bash
uv run university-news usp --stage discovery
uv run university-news usp --stage fetching
uv run university-news usp --stage extraction
```

Run multiple configured universities concurrently:

```bash
uv run university-news-all --jobs 3
```

University definitions and pagination limits live in
`src/university_news/sources/catalog.py`.

## Preprocess and compile

Clean extracted articles and build the full `articles.json` corpus plus the
compact `dashboard_articles.parquet` projection:

```bash
uv run university-news-preprocess --data-root data --workers 8
```

Compilation keeps the first occurrence of each URL and removes subsequent
duplicates before they can reach the vector index. Counts are recorded globally
and per university in `data/compiled/manifest.json`.

Rebuild only the consolidated artifacts:

```bash
uv run university-news-compile --data-root data
```

Each preprocessing run also writes `articles.rejected.jsonl` inside each site
directory. Every record includes the rejection reason and the original article.

## Enrich clipping metadata with an LLM

Start with a small sample from one university:

```bash
uv run university-news-enrich --sites usp --limit 10 --calls 3
```

Test a long-context NVIDIA NIM model with the complete schema in one call:

```bash
uv run university-news-enrich --provider nvidia_nim --sites usp --limit 10 --calls 1
```

Process every preprocessed university article:

```bash
uv run university-news-enrich
```

The command validates every model response against
`schema_clipping_university.json`. Successful records are appended to
`data/enriched/clipping/v1.0.0/annotations.jsonl`; errors and the latest run
manifest are stored beside them. A rerun skips the same combination of article
content, schema version, and model, so completed API calls are not repeated.
Invalid model output is sent back once for correction by default; configure
`OPENWEBUI_VALIDATION_ATTEMPTS` to change that behavior.

Each annotation also stores `generation_metrics`: total time per article,
time and sections per call, every validation attempt, correction count, request
IDs, and token usage returned by the endpoint. Token fields remain `null` when
the compatible API does not provide usage data. The run manifest aggregates
requests, corrections, tokens, coverage of token reporting, and total duration;
failed articles record their elapsed time in `errors.jsonl`.

The complete schema can be filled in one to five model calls. Three calls per
article are the default because the configured ReVejo endpoint has a 16K-token
context window. Each call receives a semantic group of sections plus the same
article; evidence IDs are namespaced and the fragments are merged and validated
as one complete JSON document. Override this with `--calls` or
`OPENWEBUI_ENRICHMENT_CALLS`.
NVIDIA NIM defaults to one call and uses the corresponding `NVIDIA_NIM_*`
settings. The provider is included in resumability signatures, annotations, and
run manifests, so results from different endpoints are not mixed.

Use `--force` only to request all selected records again. If the compatible
endpoint does not accept `response_format`, use `--no-json-mode`; response JSON
is still parsed and validated locally.

## Analyze data quality

Generate a per-site and global quality report:

```bash
uv run university-news-analyze summary
```

The report is saved to `data/reports/quality_report.json` and includes removal reasons,
acceptance rates, duplicate URLs, field completeness, text-length distributions,
and date coverage.

Inspect rejected articles:

```bash
uv run university-news-analyze rejected --site usp --reason missing_author
uv run university-news-analyze rejected --reason non_portuguese --limit 50
uv run university-news-analyze rejected --site ufal --full
```

## Build the local search index

Index the consolidated articles with LanceDB and Qwen3 Embedding:

```bash
uv run university-news-index --recreate --gpu-layers -1 --threads 8 --batch-size 8
```

To run indexing in the background with `nohup`:

```bash
./scripts/start_gpu_indexing.sh
```

The PID and log are written to `data/logs/`. GPU layers, threads, and batch
size can be changed through `EMBEDDING_GPU_LAYERS`, `INDEX_THREADS`, and
`INDEX_BATCH_SIZE`.

Document indexing keeps the large context window. Dashboard queries use
`EMBEDDING_QUERY_CONTEXT_LENGTH=1024` by default because their prompt is short.

## Run the dashboard

```bash
uv run streamlit run src/university_news/dashboard/app.py
```

To keep it running after closing the terminal:

```bash
./scripts/start_dashboard.sh
```

The PID and log are written to `data/logs/dashboard.pid` and
`data/logs/dashboard.log`.

The dashboard reads the compact `data/compiled/dashboard_articles.parquet`
artifact, falling back to `articles.json` for older datasets. It warms the local
embedding model during startup and uses hybrid search by default. Set
`SEARCH_MODE=lexical` in `.env` to run without the vector index.

The interface is organized into four pages:

- **Home:** project overview, corpus coverage, and recent publications.
- **Explore:** hybrid search, filters, and paginated news cards.
- **Universities:** coverage and recent publications by institution.
- **About:** project methodology, authorship, and verified profile links.

## Project layout

```text
src/university_news/
├── enrichment/  # validated OpenWebUI/OpenAI clipping enrichment
├── pipeline/    # discovery, fetching, extraction, and preprocessing
├── search/      # embeddings, LanceDB indexing, and hybrid search
├── dashboard/   # Streamlit application
├── sources/     # university catalog and metadata
└── shared/      # HTTP and file utilities
```

Generated artifacts use this layout:

```text
data/
├── sites/<site>/       # raw and processed artifacts per university
├── compiled/           # full JSON, dashboard Parquet, and manifest
├── enriched/           # versioned LLM annotations, errors, and run manifests
├── reports/            # preprocessing and quality reports
├── indexes/lancedb/    # local vector index
└── logs/               # operational logs and PID files
```

`data/compiled/manifest.json` records counts per university, pipeline errors by
stage and type, rejection categories, field completeness, date coverage,
artifact paths, preprocessing parameters, file size, and a SHA-256 checksum.
For older runs, rejection categories are recovered from per-site preprocessing
statistics; the manifest reports separately how many full rejection records are
available for inspection.
Generated data and local models are not committed.

## Tests

```bash
uv run pytest
```
