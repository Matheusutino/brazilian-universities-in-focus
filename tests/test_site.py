import json
import sqlite3

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from university_news.site.api import create_app
from university_news.site.build import build_site
from university_news.site.bundle import article_id
from university_news.site.search import ArticleIndex, ArticleQuery, CachedEncoder, fts_query

ARTICLES = [
    {"url": "https://usp.br/a", "title": "Vacina contra a dengue avança em testes", "subtitle": "Estudo clínico da USP", "author": "Ana", "date": "2026-07-10", "journal": "usp"},
    {"url": "https://usp.br/b", "title": "Festival de música no campus", "subtitle": "Programação cultural", "author": None, "date": "2026-07-12", "journal": "USP"},
    {"url": "https://furg.br/c", "title": "Navio estuda o litoral de Abrolhos", "subtitle": "Oceanografia", "author": "Bia", "date": "2026-07-01", "journal": "FURG"},
    {"url": "https://furg.br/d", "title": "Data inválida", "subtitle": None, "author": None, "date": "0137-11-26", "journal": "FURG"},
]


def annotation(url, category, fields):
    return {
        "url": url,
        "university": "USP" if "usp" in url else "FURG",
        "profile_id": "profile",
        "provider": "nvidia_nim",
        "model": "test/model",
        "schema_version": "2.1.0",
        "enriched_at": "2026-11-02T00:00:00+00:00",
        "summary.one_sentence": "Resumo da notícia sobre imunização",
        "summary.abstract": "Texto",
        "classification.news_category": category,
        "classification.content_type": "noticia",
        "classification.institutional_axes": ["pesquisa"],
        "classification.scientific_content": "central",
        "topics.scientific_fields": fields,
        "topics.keywords": ["imunização"],
        "topics.main_topic": "Saúde",
        "context.locations": [],
        "context.geographic_scope": "national",
        "research.present": True,
        "research.stage": "in_progress",
        "research.findings": ["Achado"],
        "research.funding_agencies": [],
        "research.collaboration_types": [],
        "societal_impact.dimensions": ["health"],
        "societal_impact.beneficiaries": [],
        "societal_impact.sdgs": [3],
        "communication.audience": "general_public",
        "communication.tone": "informative",
        "communication.hype": "none",
        "communication.accessibility_0_4": 3,
        "communication.scientific_depth_0_4": 2,
        "quality.analysis_status": "complete",
        "quality.requires_human_review": False,
        "entities": json.dumps([{"name": "USP", "type": "university", "role": "partner", "affiliation": None}]),
    }


@pytest.fixture()
def site(tmp_path):
    articles = tmp_path / "articles.parquet"
    pd.DataFrame(ARTICLES).to_parquet(articles)
    annotations = tmp_path / "annotations.parquet"
    pd.DataFrame(
        [
            annotation("https://usp.br/a", "resultado_de_pesquisa", ["ciencias_da_saude"]),
            annotation("https://furg.br/c", "projeto_de_pesquisa", ["ciencias_biologicas"]),
        ]
    ).to_parquet(annotations)
    output = tmp_path / "site"
    result = build_site(output, articles_path=articles, annotations_path=annotations)
    return output, result


def test_build_writes_bundle_pages_and_statistics(site) -> None:
    output, result = site

    assert (result.articles, result.annotated) == (4, 2)
    for page in ("index.html", "explorar/index.html", "analises/index.html", "universidades/index.html", "sobre/index.html", "404.html"):
        assert (output / "public" / page).is_file()
    assert (output / "public" / "static" / "logos" / "usp.png").is_file()
    with sqlite3.connect(output / "site.sqlite") as connection:
        journals, regions = zip(*connection.execute("SELECT journal, region FROM articles ORDER BY rowid"))
        invalid = connection.execute("SELECT date FROM articles WHERE title = 'Data inválida'").fetchone()
    assert set(journals) == {"USP", "FURG"}
    assert set(regions) == {"Sudeste", "Sul"}
    assert invalid == (None,)
    analysis = (output / "public" / "analises" / "index.html").read_text(encoding="utf-8")
    assert "Resultado de pesquisa" in analysis


def test_lexical_search_ignores_accents_and_respects_filters(site) -> None:
    output, _result = site
    index = ArticleIndex(output / "site.sqlite")

    page = index.search(ArticleQuery(text="vacína dengue"), offset=0, limit=10)
    assert page.mode == "lexical"
    assert page.results[0]["url"] == "https://usp.br/a"

    filtered = index.search(ArticleQuery(text="dengue", region="Sul"), offset=0, limit=10)
    assert filtered.results == []

    annotated = index.latest(ArticleQuery(analyzed_only=True, field="ciencias_biologicas"), offset=0, limit=10)
    assert [item["url"] for item in annotated.results] == ["https://furg.br/c"]


def test_hybrid_search_fuses_semantic_hits_and_applies_analysis_filters(site) -> None:
    output, _result = site
    semantic_hits = [article_id("https://furg.br/c"), article_id("https://usp.br/b")]
    index = ArticleIndex(
        output / "site.sqlite",
        semantic=lambda vector, limit, filters: semantic_hits,
        encoder=CachedEncoder(type("Encoder", (), {"encode_query": staticmethod(lambda query: [0.0])})()),
    )

    page = index.search(ArticleQuery(text="oceano", analyzed_only=True), offset=0, limit=10)

    assert page.mode == "hybrid"
    assert [item["url"] for item in page.results] == ["https://furg.br/c"]
    assert set(page.timings) == {"lexical", "embedding", "ann"}


def test_semantic_failure_falls_back_to_lexical(site) -> None:
    output, _result = site

    def broken(*_args):
        raise RuntimeError("model crashed")

    index = ArticleIndex(output / "site.sqlite", semantic=broken, encoder=CachedEncoder(type("E", (), {"encode_query": staticmethod(lambda q: [0.0])})()))
    page = index.search(ArticleQuery(text="dengue"), offset=0, limit=10)

    assert page.mode == "lexical"
    assert page.results
    assert "semantic search unavailable" in page.notes[0]


def test_cached_encoder_runs_the_model_once_per_query() -> None:
    calls = []

    class Encoder:
        def encode_query(self, query):
            calls.append(query)
            return [1.0]

    cached = CachedEncoder(Encoder(), size=1)
    cached.encode_query("Vacina  Dengue")
    cached.encode_query("vacina dengue")
    cached.encode_query("outra")
    cached.encode_query("vacina dengue")

    assert calls == ["vacina dengue", "outra", "vacina dengue"]


def test_fts_query_quotes_tokens_so_syntax_never_breaks() -> None:
    assert fts_query('vacina "dengue" OR (x) AND -') == '"vacina" OR "dengue" OR "or" OR "and"'
    assert fts_query("?!") is None


def test_api_serves_pages_listing_search_and_article(site) -> None:
    output, _result = site
    client = TestClient(create_app(output))

    for path in ("/", "/explorar/", "/analises/", "/universidades/", "/sobre/"):
        assert client.get(path).status_code == 200
    listing = client.get("/api/articles", params={"region": "Sudeste"}).json()
    assert listing["total"] == 2 and listing["mode"] == "latest"
    search = client.get("/api/articles", params={"q": "abrolhos", "mode": "lexical"}).json()
    assert search["results"][0]["journal"] == "FURG"
    identifier = article_id("https://usp.br/a")
    page = client.get(f"/noticia/{identifier}")
    assert page.status_code == 200 and "Resultado de pesquisa" in page.text and "test/model" in page.text
    assert client.get("/noticia/missing").status_code == 404
    assert client.get("/api/articles", params={"from": "not-a-date"}).status_code == 422
    assert client.get("/healthz").json() == {"ok": True, "semantic": False}


def test_keyword_variants_merge_into_the_most_frequent_spelling() -> None:
    from university_news.site.keywords import KeywordCanon, keyword_key

    assert keyword_key("Pós-Graduação") == keyword_key("pos_graduacao")
    assert keyword_key("processos seletivos") == keyword_key("Processo Seletivo")
    assert keyword_key("inscrições") == keyword_key("inscrição")
    assert keyword_key("ônibus") == "onibus"
    canon = KeywordCanon(
        [["pós-graduação", "Bolsas"], ["pós-graduação", "bolsa"], ["Pós-Graduação", "bolsa", "FURG"]]
    )

    cleaned = canon.clean(["Pós-Graduação", "pos_graduacao", "bolsas", "FURG", "USP"], exclude=["FURG"])

    assert cleaned == ["pós-graduação", "bolsa", "USP"]


def test_build_deduplicates_keywords_and_drops_own_university(tmp_path) -> None:
    articles = tmp_path / "articles.parquet"
    pd.DataFrame(ARTICLES[:2]).to_parquet(articles)
    first = annotation("https://usp.br/a", "evento", ["ciencias_da_saude"]) | {"topics.keywords": ["USP", "Vacinas", "vacina"]}
    second = annotation("https://usp.br/b", "evento", []) | {"topics.keywords": ["vacina", "Cultura"]}
    annotations = tmp_path / "annotations.parquet"
    pd.DataFrame([first, second]).to_parquet(annotations)
    output = tmp_path / "site"

    build_site(output, articles_path=articles, annotations_path=annotations)

    index = ArticleIndex(output / "site.sqlite")
    assert index.article(article_id("https://usp.br/a"))["annotation"]["topics.keywords"] == ["vacina"]
    analysis = (output / "public" / "analises" / "index.html").read_text(encoding="utf-8")
    assert "vacina <strong>2</strong>" in analysis
    assert "USP <strong>" not in analysis


def test_criteria_page_lists_exactly_the_contract_categories() -> None:
    from university_news.enrichment.schema import ClippingSchema
    from university_news.site.criteria import CRITERIA

    template = ClippingSchema.load().template
    for criterion in CRITERIA:
        section, field = criterion["path"].split(".")
        value = template[section][field]
        allowed = (value[0] if isinstance(value, list) else value).split("|")
        assert list(criterion["values"]) == allowed, criterion["path"]


def test_build_publishes_the_criteria_page(site) -> None:
    output, _result = site
    page = (output / "public" / "criterios" / "index.html").read_text(encoding="utf-8")

    assert "Resultado de pesquisa" in page
    assert 'id="hype"' in page and "Sensacionalismo" in page
    assert "ODS 17" in page
