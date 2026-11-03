# Configuration

Configuration is split by sensitivity:

| File | Contents | Committed |
| --- | --- | --- |
| `config/settings.toml` | providers, models, limits, paths, ports | yes |
| `.env` | API keys and `DATABASE_URL` only | no |
| `config/schemas/university_clipping.json` | enrichment output contract | yes |

Create the local secrets file from the template:

```bash
cp .env.example .env
```

| Variable | Needed for |
| --- | --- |
| `NVIDIA_API_KEY` or `NVIDIA_API_KEYS` | enrichment with NVIDIA NIM (default); the plural form takes a comma-separated list |
| `OPENWEBUI_API_KEY` or `OPENWEBUI_API_KEYS` | enrichment with OpenWebUI |
| `OPENROUTER_API_KEY` | `--extractor llm` or `--extractor hybrid` |
| `DATABASE_URL` | the PostgreSQL pipeline; written by `scripts/setup_local_postgres.sh` |
| `TEST_DATABASE_URL` | optional integration tests; the database is wiped |

When a new secret becomes required, add it to `.env.example`.

## Settings sections

| Section | Purpose |
| --- | --- |
| `[enrichment]` | default provider, `providers` that run at the same time, worker concurrency, fallback ETA |
| `[enrichment.openwebui]`, `[enrichment.nvidia_nim]` | URL, model, calls per article, timeouts, retries, input/output limits, thinking, JSON mode, `requests_per_minute` per key, `workers` for that provider's process |
| `[workers]` | queue worker counts, lease and heartbeat durations, attempt limit, polling interval |
| `[daily_discovery]` | listing pages checked per site and stop conditions |
| `[openrouter]` | model used by LLM-based extraction |
| `[search]` | `hybrid` or `lexical` mode and the LanceDB location |
| `[embedding]` | GGUF model, context lengths, GPU layers, threads, batch size |
| `[site]` | build directory, bind address and port, query model on CPU or GPU, threads, query cache size |
| `[runtime]` | log directory and log/PID file names used by the scripts |

Read any value from the shell. The service scripts use this command too:

```bash
uv run university-news-config enrichment provider
uv run university-news-config site port
```
