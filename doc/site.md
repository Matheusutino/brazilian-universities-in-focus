# Public website

The website is built for a small server: most pages are static HTML generated at
publication time, and one small Python process serves them together with the
search API. The server never loads the corpus into memory and does not need
PostgreSQL or a GPU.

```bash
uv run university-news-site build     # pages + data bundle, from the published exports
uv run university-news-site serve     # http://127.0.0.1:8501
./scripts/start_site.sh               # build, then serve in the background (PID and log in data/logs/)
```

The systemd user service `university-news-site` does the same, and the hourly
publish service rebuilds the site after exporting new articles and
annotations. Address, port, and query-model settings are under `[site]` in
`config/settings.toml`.

## How it fits together

```text
publication (lab machine, hourly)                    small server
  site_articles.parquet ┐
  enrichments (combined)     ├─► university-news-site build ─► data/site/
  manifest.json              ┘       ├── public/      static pages, CSS, JS, logos
                                     ├── site.sqlite  articles + annotations + FTS5 index
                                     └── stats.json
                                                     university-news-site serve
                                                       ├── /                 static files
                                                       ├── /noticia/<id>     article page (rendered on request)
                                                       └── /api/articles     listing and search (JSON)
```

`data/indexes/lancedb` (the vector index) must also be present on the server
for semantic search. Copy `data/site/` and the LanceDB directory to deploy;
nothing else from `data/` is needed.

## Pages

| URL | Kind | Contents |
| --- | --- | --- |
| `/` | static | overview, search box, corpus figures, recent publications |
| `/explorar/` | static + JS | search, filters (region, university, period, category, CNPq area, type, analyzed only), pagination; the state lives in the URL, so every search can be shared |
| `/analises/` | static + JS | coverage and charts of the LLM annotations; switching the region or university redraws the charts from data embedded in the page, without a request |
| `/universidades/` | static + JS | one card per university with its logo; each links to its news in Explorar |
| `/criterios/` | static | every annotation field, its possible categories, and what each means (from `site/criteria.py`; a test keeps it equal to the contract) |
| `/sobre/` | static | authorship and methodology |
| `/noticia/<id>` | server-rendered | summary, classification, keywords, places, research, SDGs, entities, and provenance; cacheable and indexable |

Static pages cost almost no CPU: the server only sends files. The JavaScript is
plain (no framework or build step) in `site/static/js/`.

## Search

`GET /api/articles?q=…` combines two rankings with Reciprocal Rank Fusion
(k = 60):

| Ranking | Engine | Cost |
| --- | --- | --- |
| Lexical | SQLite FTS5 with BM25 over title (×4), keywords (×2), subtitle (×1.5), and LLM summary; accents are ignored | milliseconds |
| Semantic | Qwen3 query embedding + approximate nearest-neighbor search (IVF-HNSW, 8-bit) in LanceDB, with region, university, and date pushed into the index as a prefilter | the embedding dominates; ANN takes tens of ms |

Choices made for weak hardware:

- **Only the query is embedded at search time.** Article vectors are computed
  once, at indexing.
- **Query embeddings are cached** (`query_cache_size`, LRU), so a repeated
  search costs ~10 ms.
- **The model runs on the CPU** by default (`query_gpu_layers = 0`,
  `query_threads = 2`) behind a lock, because a llama.cpp context is not
  thread-safe; concurrent searches queue instead of crashing the process.
- **Progressive results.** The page requests `mode=lexical` and the hybrid
  ranking at the same time, shows word matches immediately, and replaces them
  when the hybrid ranking arrives.
- **Graceful degradation.** The model loads in the background after start-up;
  until it is ready, or if it fails, search answers lexically and the response
  says so (`mode`, `notes`).
- Every response reports `elapsed_ms` and per-stage `timings` (`lexical`,
  `embedding`, `ann`), to tune the server once its hardware is known.

`uv run university-news-site serve --lexical-only` skips the model entirely
for the smallest machines. Set `mode = "lexical"` under `[search]` for the
same effect from configuration.

### API

| Endpoint | Parameters |
| --- | --- |
| `GET /api/articles` | `q`, `region`, `university`, `from`, `to` (YYYY-MM-DD), `category`, `field`, `type`, `analyzed`, `mode` (`hybrid` or `lexical`), `page`, `page_size` (≤ 48) |
| `GET /api/articles/{id}` | full record with annotation |
| `GET /healthz` | `{"ok": true, "semantic": <model loaded>}` |
| `GET /api/docs` | interactive OpenAPI documentation |

Listings are cacheable for 5 minutes and searches for 1 minute
(`Cache-Control`), so a reverse proxy or CDN in front absorbs repeated traffic.

## Data bundle

`site.sqlite` holds every article (URL, title, subtitle, author, date,
university, region), its annotation as JSON, a few indexed annotation columns
for filtering (category, content type, CNPq areas), and a contentless FTS5
index. The build writes it to a temporary file and renames it, so readers never
see a partial file, and the server reopens it when it changes. The static pages
are swapped the same way.

## Changing the site

- Templates (Jinja2) are in `site/templates/`, styles in
  `site/static/css/site.css`, and scripts in `site/static/js/`.
- Charts are HTML bars that follow one rule set: one series per chart in the
  brand green, thin bars with a rounded data end, values at the bar end, a
  tooltip on hover or keyboard focus, and an optional table view. Ordered
  scales (scientific centrality, hype, SDGs) keep their natural order.
- University logos are in `site/static/logos/`, with source, author, and
  license of each file in `CREDITS.json`. A university without a logo shows its
  initials. To add or replace one, put the original image (named after the
  site key, for example `ufc.png`) in a folder and normalize it:

  ```bash
  uv run --with pillow python scripts/normalize_logos.py ORIGINALS_DIR src/university_news/site/static/logos
  ```

  The script trims empty borders and places each logo on a square canvas at
  the same visual area (wide logos use the full width), so every card shows a
  logo of the same apparent size.

## Keywords

LLM keywords are deduplicated when the site is built (`site/keywords.py`):
spellings that differ only in case, accents, punctuation, or Portuguese plural
("Pós-Graduação", "pos_graduacao"; "processos seletivos", "processo seletivo")
merge into the most frequent spelling in the corpus, repeated keywords within
an article are removed, and the article's own university acronym is dropped
(it describes the source, not the news). Article pages, search, and the
keyword ranking on Análises all use the cleaned list; the raw annotations in
the database are unchanged.
- After changing templates, styles, or scripts, run `university-news-site
  build`; after changing Python code, restart `serve`.
