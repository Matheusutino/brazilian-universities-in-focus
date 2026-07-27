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

Clean extracted articles and build `data/compiled/articles.json`:

```bash
uv run university-news-preprocess --data-root data --workers 8
```

Rebuild only the consolidated file:

```bash
uv run university-news-compile --data-root data
```

Each preprocessing run also writes `articles.rejected.jsonl` inside each site
directory. Every record includes the rejection reason and the original article.

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

## Run the dashboard

```bash
uv run streamlit run src/university_news/dashboard/app.py
```

The dashboard reads `data/compiled/articles.json` and uses hybrid search by default.
Set `SEARCH_MODE=lexical` in `.env` to run without the vector index.

## Project layout

```text
src/university_news/
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
├── compiled/           # consolidated dataset and reproducibility manifest
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
