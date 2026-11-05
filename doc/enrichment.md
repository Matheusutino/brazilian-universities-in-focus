# LLM enrichment

Enrichment adds a compact set of analytical metadata to each article. The
contract lives in `config/schemas/university_clipping.json` (version 2.1.0).

## Contract

| Section | Fields | Filled by |
| --- | --- | --- |
| `document` | ID, URL, university, title, subtitle, author, publication date | code |
| `summary` | one-sentence summary, abstract | model |
| `classification` | content type, institutional axes, news category, centrality of scientific content | model |
| `topics` | main topic, CNPq major areas, keywords | model |
| `entities` | name, type, role, affiliation | model |
| `context` | locations, geographic scope, event date | model |
| `research` | presence, stage, findings, funding agencies, collaboration types | model |
| `societal_impact` | impact dimensions, beneficiaries, SDGs | model |
| `communication` | audience, tone, accessibility (0–4), scientific depth (0–4), hype | model |
| `quality` | analysis status, human-review flag | model |

The fields serve the analyses the corpus is meant to support: classification,
tone and bias, trends by topic, region, and time, and search. Metadata already
known from extraction is filled by code and never requested from the model.

Every field is required. Options separated by `|` become enums, `|null` makes
a field nullable, `YYYY-MM-DD` is validated as a date, `sdgs` must be between
1 and 17, and fields ending in `_0_4` must be between 0 and 4.

### Changing the contract

1. Edit the JSON file and bump `schema_version`. Annotations are stored per
   version in `data/enriched/clipping/v<version>/`, so versions never mix.
2. If you add or rename a model section, update `_SECTION_BUNDLES` in
   `enrichment/client.py` so that each section belongs to exactly one bundle.
   `test_section_groups_cover_every_model_section_once` checks this.

Version 1.0.0 had 376 fields: 5W1H, 11 metric scores, claims, an evidence
system with cross-referenced IDs, and per-item confidence. On three test
articles it needed about 155 s and 14,700 output tokens per article, spread
over three calls. Version 2 keeps only the fields listed above.

## Providers, keys, and rate limits

The client uses the OpenAI Python SDK against any compatible endpoint. Two
providers are configured in `config/settings.toml`, both with models that do
not reason: `nvidia_nim` (Nemotron 3 Ultra, thinking disabled) and `openwebui`
(DeepSeek V3). Only API keys belong in `.env`. See
[configuration.md](configuration.md).

`[enrichment].providers` lists the providers that run at the same time
(currently only `openwebui`, with DeepSeek V3). Each
gets its own worker process, and both claim jobs from the same queue, so every
article is annotated once, by whichever provider takes it first. If one
provider goes down or is rate limited, its jobs return to the queue and the
other provider completes them. Each annotation records its profile, so the
model that produced it is always known.

`requests_per_minute` in a provider section limits each API key (36 for NIM,
below its 40/min quota). The client spaces requests evenly, so bursts never
exceed the limit, and correction requests count against it too. If the server
still answers HTTP 429, the client pauses that key (for `Retry-After`, or 20 s)
and repeats the request, up to 8 times, without consuming a validation attempt
or failing the job. The SDK's own retries are disabled for paced providers so
that every request goes through the pacer. With several keys (`NVIDIA_API_KEYS=a,b`), each
request takes the key with the earliest free slot, so two keys double the
throughput. Every attempt records which key index it used, how long it
waited, and whether it was rate limited; the totals appear in `status`. The limit is enforced within one
process; do not run two enrichment processes on the same keys.

Before adding keys from several accounts, check the provider's terms of use;
working around per-account limits is often not allowed.

## Running

For the corpus, use the database-backed worker: it resumes after any stop,
avoids repeat calls for annotations already saved and records runs and failures.
A request may be repeated after a crash or lease loss before saving. The
procedure is in the [runbook](database.md#runbook-enrich-the-whole-corpus).

```bash
uv run university-news-db enqueue-backfill --stage enrich --limit 20
uv run university-news-worker --stages enrich --exit-when-empty
uv run university-news-db status
uv run university-news-db export-enrichments
```

For quick experiments without a database, `university-news-enrich` writes JSONL
files directly:

```bash
uv run university-news-enrich --sites usp --limit 10
uv run university-news-enrich --sites usp --limit 10 --model openai/gpt-oss-120b --provider openwebui
```

`[enrichment].workers` (16 by default) strictly limits simultaneous requests in
both paths. Use `--workers` to override it for one run.

## Profiles

Every annotation belongs to a profile: provider, model, schema version, prompt
hash, and generation settings. Changing any of them creates a new profile, so
results from different configurations are never mixed and can be compared.
See [database.md](database.md#enrichment-profiles).

One call per article is the default. For models with a small context window,
`--calls 2` (or `calls = 2` in the provider section) splits the contract into
two semantic halves. Each call returns its own `quality` section, and the two
are merged: the worst analysis status is kept, and human review is flagged if
either call flags it. Entities from the first call are passed to the second.

## Validation and outputs

Every response is validated with JSON Schema. Invalid output is sent back for
correction; `validation_attempts` controls how many attempts are made.

The database worker stores annotations in PostgreSQL; the website reads them
directly, selecting the most recently stored profile per accepted article.
For research, `export-enrichments`
publishes them to `data/enriched/profiles/<profile>/` as JSONL, a flat Parquet
table, and a checksummed manifest. The experimental JSONL command writes:

```text
data/enriched/clipping/v2.1.0/
├── annotations.jsonl   # validated records
├── errors.jsonl        # failures with elapsed time
└── manifest.json       # latest run summary
```

Each annotation stores `generation_metrics`: total time, time and sections per
call, every validation attempt, correction count, request IDs, and token
usage. Token fields are `null` when the endpoint does not report usage. The
run manifest aggregates requests, corrections, tokens, token-reporting
coverage, and duration.

## Resumability

The database worker skips any article that already has an annotation for the
active profile. The JSONL command skips any article whose content hash, schema
version, model, provider, and strategy already match a stored annotation. In
both cases completed calls are never repeated and results from different
endpoints never mix. `--force` requests
all selected records again. If an endpoint rejects `response_format`, use
`--no-json-mode`; responses are still parsed and validated locally.
