# Collection

Collection covers three stages: discovery, fetching, and extraction. Each
university is described once in `src/university_news/sources/catalog.py`.

## University catalog

A `SiteConfig` declares a portal's base URL, listing URL, paginated URL
template, listing selector, page range, and optional selectors for content,
title, subtitle, author, date, and tags. `excluded_article_path_prefixes`
drops sections that are not news, such as opinion columns.

The catalog also maps each university to a Brazilian region (`SITE_REGIONS`).
Search filters and the website use this mapping.

Current coverage, by region:

| Region | Universities |
| --- | --- |
| Norte | UFAM, UFPA, UFT |
| Nordeste | UFAL, UFC |
| Centro-Oeste | UnB, UFMS |
| Sudeste | USP, UNICAMP, UNESP, UFRJ, UERJ, UFABC, UFMG, UFLA, UNIFESP |
| Sul | UFRGS, UFPR, UEM, FURG |

To add a university, define a `SiteConfig`, register it in the catalog, add
its region, and cover any site-specific parsing in `pipeline/site_rules.py`
with tests.

## Production collection

Use `university-news-daily` and `university-news-worker` as described in
[operations.md](operations.md). Workers download HTML into memory and persist
the bytes, checksum, encoding, content type, final URL, status and fetch time in
PostgreSQL `raw_documents`. Extraction reads this table directly; no local HTML
or JSON intermediate is needed.

## Extraction

The production worker uses trafilatura, portal selectors, and site-specific rules
to extract articles from the HTML stored in PostgreSQL.

## Daily discovery and recovering gaps

`university-news-daily` (05:00, `university-news-daily.timer`) walks each
listing from the newest page and stops after `stop_after_empty_pages` (3)
consecutive pages without new URLs, up to `max_pages` (200). A normal day
therefore reads only a few pages per site, while a gap (downtime, or a site
fixed after weeks) is recovered automatically on the next run. Check what each
site found with:

```sql
SELECT s->>'site', s->>'pages_checked', s->>'urls_new', s->>'errors'
FROM pipeline_runs r, jsonb_array_elements(r.stats->'sites') s
WHERE r.kind = 'daily-discovery' ORDER BY r.started_at DESC, 1 LIMIT 20;
```

A site with `urls_seen = 0` and no errors usually means its listing HTML
changed and `listing_selector` no longer matches; one with errors on every page
usually means a network or TLS problem.

## TLS chains

Some university servers send only their own certificate, without the
intermediate CA, and fail verification. `shared/http_client.py` builds the TLS
context from certifi's roots plus every PEM in `config/certs/`. The RNP
intermediate (`RNP ICPEdu GR46 OV TLS CA 2025`, used by UFPR and other federal
universities) is bundled there; trust still ends at a root in certifi. To add
another, download the "CA Issuers" URL from the server certificate and convert
it with `openssl x509 -inform der -in issuer.crt -out config/certs/<name>.pem`.

## Known site limits

- **UFAM** lists news at `https://www.ufam.edu.br/noticias.html`, with offsets
  `?start=10`, `?start=20`, etc. The configured archive covers 216 pages
  (offsets 0–2150); extend `end_page` as it grows. Metadata comes from
  `.documentFirstHeading`, `.documentAuthor`, and `.documentPublished` inside
  `.item-page`. This source covers the Notícias section, not every category
  or subdomain maintained by UFAM. Daily discovery includes it by default;
  use `uv run university-news-daily --sites ufam` to target it.

- **USP** blocks HTML pagination beyond page 15 ("Limite de paginação
  excedido"), so it is listed through the WordPress REST API instead
  (`wordpress_api` and `wordpress_types = ("posts", "podcast")` in the
  catalog), 100 articles per request and without that limit. If the API fails,
  daily discovery falls back to the HTML listing. To recover a gap on a
  WordPress site, list by date:

  ```bash
  uv run university-news-daily --sites usp --after 2026-07-09 --max-pages 100 --stop-after-empty-pages 100
  ```
- **UFC** uses `https://ufcinforma.ufc.br/pt/marcador/noticias` as its listing
  source, with subsequent pages at `?pagina=N`. The catalog covers pages 1–17;
  extend `end_page` as the archive grows. Article selectors support both the
  new portal and previously downloaded HTML from `agencia.ufc.br`.
