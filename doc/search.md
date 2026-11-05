# Text and semantic search

PostgreSQL stores the authoritative articles and annotations. Its
`site_documents` projection is updated transactionally and indexed by
ParadeDB `pg_search` for BM25. LanceDB remains the vector database.

## BM25

Run `uv run university-news-site init-search` once during deployment after
installing/preloading pg_search 0.26.4+. No SQLite export is involved.
Title, keywords, subtitle, and LLM summary contribute at weights 4, 2, 1.5,
and 1. Tokenization uses Unicode-aware simple word splitting with lowercase
and ASCII folding; it does not apply Portuguese stemming. User input is
tokenized as literal words, not passed to a query-language parser.

## Embeddings and index

Qwen3 Embedding 0.6B (GGUF, Q8_0) runs through llama-cpp-python. Article
vectors live in LanceDB with article ID, content hash, university, region,
and date. Only query embeddings are calculated during search.

New production articles use the durable `embed` worker. File-based indexing
and the old recreate script have been removed to protect the active index.
All paths share canonical UUID article IDs.
Reconcile or rebuild an old index if its IDs were generated from
noncanonical URLs.

Document context, batching, threads, and GPU layers are configured under
`[embedding]`. Query settings live under `[site]`; CPU is the default.
The query model is cached and guarded by a lock.

The active index is `data/indexes/lancedb-v2`; the older `lancedb` directory
is retained separately. When rebuilding to a new path, stop embedding workers,
configure that path, and use `uv run university-news-db enqueue-backfill
--stage embed --reindex` to enqueue accepted articles including completed or
failed embedding jobs. Existing queued/running jobs are not reset. Restart one
embedding worker; its state is updated after each successful write. The current
configuration uses two CPU threads because the shared GPU is occupied; a full
corpus rebuild can take many days. Recheck GPU capacity before changing it.

The index contract now includes the actual GGUF SHA-256, dimensions, cosine
metric, pooling, query instruction, and document context. Query context may be
shorter than document context. Indexes with missing/older contracts must be
rebuilt; the API will not silently assume compatibility. The API checks for
committed LanceDB versions on every read. The embedding worker creates the ANN
index at 256 rows and maintains it at compaction intervals; smaller collections
use exact vector scans. Quantized ANN candidates are refined against original
vectors with a factor of four.

The worker publishes its in-memory backend only after the table and contract
are fully validated. Failed initialization is retried and cannot silently
bypass the contract on a later job. PostgreSQL and LanceDB are separate stores:
a crash after a LanceDB write but before the job commits can require an
idempotent retry; they do not form one atomic transaction.

## Proving retrieval works

The background monitor runs a real query embedding, vector retrieval, and
PostgreSQL ID resolution every `[site].semantic_health_interval_seconds` (60).
`/healthz` reports loaded/empty/error/stale states, last successful probe,
indexed/unindexed rows, ANN availability, and embedding/search timings. An empty
fresh index is `awaiting_data`, not ready. A failed real user query marks
semantic health failed immediately; BM25 continues to serve results.
`coverage_estimate` compares LanceDB row count with published PostgreSQL rows;
it is an estimate, not proof that every ID matches. `/healthz` reports degraded
while that estimate is incomplete, even though `/readyz` can serve searches.

`/readyz` returns 503 when hybrid mode is configured but semantic retrieval has
not passed its probe, has expired, or lacks ANN at a corpus size requiring it.
In explicit lexical-only mode it only requires PostgreSQL/BM25. A small index
can be semantically ready using an exact scan; `ann_ready` remains false.

For diagnostics without a BM25 fallback:

```bash
uv run university-news-site check-search \
  --query "Como evitar infecções propagadas pelo Aedes aegypti?" \
  --top-k 10 --repeat 5 --concurrency 2
# Add --expected-id UUID to check a known relevant article.
```

The command is read-only and exits nonzero on incompatible contracts, missing
data, unresolved hits, ANN recall below `--minimum-recall` (0.9), or missing
reference IDs. It compares ANN with exact search, reports p50/p95 timings, and
bypasses the query cache to expose encoder queuing. ANN recall measures index
approximation, not semantic relevance: use expected IDs and several real
queries to evaluate relevance. No universal latency threshold is assumed.

The ordinary suite uses real LanceDB indexes with controlled vectors. The
opt-in test below also runs the actual local Qwen model, PostgreSQL, and LanceDB
on three reference news stories (a functional check, not a production benchmark):

```bash
RUN_REAL_EMBEDDINGS=1 TEST_DATABASE_URL=... CUDA_VISIBLE_DEVICES= \
  uv run pytest tests/test_site.py::test_real_qwen_lancedb_and_postgres_retrieve_reference_news -q -s
```

Use only a dedicated disposable test database; the integration fixture wipes it.

## Hybrid retrieval

RRF combines BM25 and semantic candidates. Date/university/region are
prefiltered in LanceDB; PostgreSQL enforces all filters and removes stale
vector hits. For selective filters, the API expands ANN retrieval up to
1,920 candidates before taking the best 60 matching hits. The API reports
candidate counts, not exhaustive search counts.

Set `[search].mode = "lexical"` or serve with `--lexical-only` to disable
semantic retrieval. PostgreSQL BM25 remains required.

## Maintenance

LanceDB compaction runs at the end of full indexing and every 1,000 articles
in the embedding worker. To compact manually:

```bash
uv run university-news-index --optimize --keep-versions-hours 0
```

Stop the embedding worker before expiring all historical versions.
PostgreSQL autovacuum maintains dead tuples; retain regular backups and test
restoration. Full website deployment instructions are in [site.md](site.md).

Primary references: [pg_search](https://github.com/paradedb/paradedb),
[BM25 scoring](https://www.paradedb.com/docs/reference/full-text/score),
[ASCII folding](https://www.paradedb.com/docs/reference/token-filters/ascii-folding).
