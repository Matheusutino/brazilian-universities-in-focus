# Brazilian Universities in Focus

A structured, searchable corpus of news published by Brazilian public
universities, along with the pipeline that collects, cleans, enriches, and
indexes it.

## Why this matters

Universities shape academic training, scientific development, and culture.
They train professionals, produce research that advances science and
technology, and encourage critical reflection on social, political, and
economic issues. They are also spaces for debate and innovation, where
solutions to contemporary challenges take shape. Their influence reaches
beyond the classroom into social change and public policy.

University journalism is how much of this work reaches the public. Institutional
news portals report on research, events, academic policies, and initiatives,
both to the academic community and to society at large. Analyzing this news
shows how universities position themselves on public issues, how they frame
their narratives, and what patterns appear in scientific communication. In a
time of growing misinformation, understanding how scientific news is produced
and circulated matters more than ever.

Yet this material is hard to study at scale:

- **No organized datasets exist.** University news is not compiled in a form
  ready for automated analysis.
- **Portuguese is underserved.** Most NLP research targets English, which
  limits the models and studies available for Portuguese.
- **Sources are fragmented.** Each institution publishes in its own portal,
  with its own structure. This makes comparative studies of discourse, bias,
  and thematic coverage difficult.

This repository addresses that gap. It began with the news portals of four of
Brazil's top-ranked universities (USP, UNICAMP, UFRJ, and UNESP) and now covers
19 universities across all five regions of the country, with more than 128,000
articles.

## What the corpus enables

The data is produced by web scraping and structured content extraction, then
validated and deduplicated. It supports both quantitative and qualitative
studies of institutional journalism, and Web and NLP applications such as:

- academic content classification;
- bias detection and discourse analysis;
- trend and thematic-coverage analysis across institutions and regions;
- recommendation and search systems for university media.

By connecting journalism, education, and Web technologies, the project serves
research at the intersection of media studies, web-based communication, and
intelligent systems.

## What is in this repository

| Component | Description |
| --- | --- |
| Collection | asynchronous discovery and fetching from official university portals, with structured article extraction |
| Preprocessing | field validation, Portuguese language detection, URL deduplication, and an auditable rejection log |
| Enrichment | LLM-generated metadata (summary, classification, topics, entities, research, societal impact, communication style), validated against a JSON Schema |
| Search | SQLite FTS5 (BM25) combined with approximate vector search over local Qwen3 embeddings through Reciprocal Rank Fusion |
| Operations | a PostgreSQL-backed durable queue with resumable, audited runs for collection and enrichment |
| Website | static pages plus a small search API, built to run on a modest server |

Universities currently covered:

| Region | Universities |
| --- | --- |
| Norte | UFPA, UFT |
| Nordeste | UFAL, UFC |
| Centro-Oeste | UnB, UFMS |
| Sudeste | USP, UNICAMP, UNESP, UFRJ, UERJ, UFABC, UFMG, UFLA, UNIFESP |
| Sul | UFRGS, UFPR, UEM, FURG |

## Quick start

Requirements: Python 3.12+, [`uv`](https://docs.astral.sh/uv/), and an NVIDIA
GPU with the CUDA toolchain for GPU-accelerated embeddings.

```bash
uv sync --dev
cp .env.example .env                       # add only the API keys you need

uv run university-news usp --stage all     # collect one university
uv run university-news-preprocess --data-root data --workers 8
uv run university-news-index --recreate    # build the semantic index
uv run university-news-site build        # pages and search bundle
uv run university-news-site serve        # http://127.0.0.1:8501
```

## Documentation

Implementation details are in [`doc/`](doc/README.md):

- [Architecture](doc/architecture.md): data flow, package and data layout
- [Configuration](doc/configuration.md): settings and secrets
- [Collection](doc/collection.md): catalog, discovery, fetching, extraction
- [Preprocessing](doc/preprocessing.md): validation, compilation, quality reports
- [Enrichment](doc/enrichment.md): LLM clipping metadata
- [Search](doc/search.md): embeddings, LanceDB, hybrid retrieval
- [Database](doc/database.md): PostgreSQL setup, recovery, transparency, runbook
- [Operations](doc/operations.md): daily cycle and services
- [Website](doc/site.md): pages, search API, and deployment

## Tests

```bash
uv run pytest
```

## Data and content

Collected articles belong to their original university portals. The project
organizes metadata and links readers to the official publication. Generated
data, local models, and secrets are not committed.
