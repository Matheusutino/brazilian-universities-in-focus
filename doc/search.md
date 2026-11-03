# Semantic index and search

## Embeddings and index

Articles are embedded locally with Qwen3 Embedding 0.6B (GGUF, Q8_0) through
`llama-cpp-python` and stored in LanceDB with their university, region, and
date. These columns let filters run before the vector search.

```bash
uv run university-news-index --recreate --gpu-layers -1 --threads 8 --batch-size 8

# Same, in the background with nohup (PID and log in data/logs/)
./scripts/start_gpu_indexing.sh
```

The model file is `data/models/Qwen3-Embedding-0.6B-Q8_0.gguf`
(`[embedding].model_path`); with an empty `model_path`, `model_file` is
downloaded from `model_repo` into the Hugging Face cache instead. GPU layers,
threads, batch size, the model, and LanceDB paths come from
`[embedding]` and `[search]`. Document indexing uses the large
`context_length`. Queries use `query_context_length = 1024` because their
prompts are short.

`llama-cpp-python` is built with CUDA (`CMAKE_ARGS=-DGGML_CUDA=ON` in
`pyproject.toml`). Without an NVIDIA toolchain, avoid rebuilding it unless
necessary.

## Hybrid retrieval

The website combines SQLite FTS5 (BM25) with approximate vector search in
LanceDB through Reciprocal Rank Fusion (`rrf_fuse`, k = 60); documents ranked
well by both rise to the top. Only the query is embedded at search time, with a
cache, a lock around the model, and a lexical fallback. Details and the tuning
options for small servers are in [site.md](site.md#search).

Set `mode = "lexical"` under `[search]` to run without the vector index.

## Keeping the index small

Every LanceDB write adds a table version and a small data file. The full
indexing run compacts the table when it finishes, and the embedding worker
compacts it every 1,000 articles. To compact by hand (stop the embedding worker
first when using `--keep-versions-hours 0`):

```bash
uv run university-news-index --optimize --keep-versions-hours 0
```
