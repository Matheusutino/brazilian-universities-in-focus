import json
import os

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from university_news.site.api import create_app
from university_news.site.build import build_site
from university_news.operations.identity import stable_article_id
from university_news.operations.store import PostgresStore
from university_news.site.search_schema import initialize_search
from university_news.site.search import ArticleIndex, ArticleQuery, CachedEncoder, search_text
from university_news.site.statistics import deduplicate_keywords, site_statistics


def article_id(url):
    return str(stable_article_id(url))


def test_ufam_logo_and_decision_support_are_rendered() -> None:
    from university_news.site.render import make_environment

    environment = make_environment()
    stats = {
        "articles": 10, "annotated": 3, "earliest_year": 2020, "latest_year": 2026,
        "regions": ["Norte"],
        "universities": [{"journal": "UFAM", "region": "Norte", "news": 10,
                          "earliest": "2020-01-01", "latest": "2026-01-01", "annotated": 3}],
    }
    context = {"stats": stats, "version": "test", "page": "home"}
    home = environment.get_template("home.html").render(**context, latest=[])
    universities = environment.get_template("universities.html").render(**context)
    assert "apoiar a tomada de decisões" in home
    assert "3 notícias analisadas com IA" in home
    assert "/static/logos/ufam.png" in universities
    assert 'alt="Logo UFAM"' in universities

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


def test_article_shows_new_contract_and_handles_legacy_annotations() -> None:
    from university_news.site.render import make_environment

    template = make_environment().get_template("article.html")
    record = annotation("https://usp.br/a", "evento", [])
    article = ARTICLES[0] | {"region": "Sudeste", "journal": "USP"}
    context = {"article": article, "version": "test", "stats": {"articles": 1, "universities": []}}
    legacy = template.render(**context, a=record)
    assert "A notícia em 5W1H" not in legacy
    assert "Alinhamento com a Agenda Nacional" not in legacy
    updated = record | {
        "five_w_one_h.what": "Pesquisa sobre vacina",
        "agenda_nacional.macrotemas": ["M9", "M10"],
    }
    rendered = template.render(**context, a=updated)
    assert "Pesquisa sobre vacina" in rendered
    assert "M9 · Saúde, Qualidade de Vida e Tecnologias" in rendered
    assert "M10 · Biodiversidade, Biotecnologia e Bioeconomia" in rendered
    assert "não comprova impacto social" in rendered
    assert "Dimensões de impacto" not in rendered

    updated["agenda_nacional.macrotemas"] = []
    assert "Alinhamento com a Agenda Nacional" in template.render(
        **context, a=updated
    )


@pytest.fixture()
def site(tmp_path):
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set (dedicated disposable PostgreSQL)")
    store = PostgresStore.open(dsn)
    store.connection.execute("DROP SCHEMA public CASCADE")
    store.connection.execute("CREATE SCHEMA public")
    store.initialize()
    initialize_search(store.connection)
    store.register_source("usp", "https://usp.br/")
    store.register_source("furg", "https://furg.br/")
    for article in ARTICLES:
        site_key = article["journal"].lower()
        store.bootstrap_articles(site_key, [article])
    store.register_enrichment_profile({
        "id": "profile", "provider": "nvidia_nim", "model": "test/model",
        "schema_version": "2.1.0", "prompt_sha256": "test", "settings": {},
    })
    for url, category, fields in [
        ("https://usp.br/a", "resultado_de_pesquisa", ["ciencias_da_saude"]),
        ("https://furg.br/c", "projeto_de_pesquisa", ["ciencias_biologicas"]),
    ]:
        flat = annotation(url, category, fields)
        analysis = {}
        for key, value in flat.items():
            if "." in key:
                section, name = key.split(".", 1)
                analysis.setdefault(section, {})[name] = value
        analysis["entities"] = json.loads(flat["entities"])
        store.save_enrichment(stable_article_id(url), {"analysis": analysis}, profile_id="profile")
    index = ArticleIndex(dsn)
    index.open()
    output = tmp_path / "site"
    result = build_site(output, index=index)
    try:
        yield output, result, index, store
    finally:
        index.close()
        store.close()


def test_build_writes_static_pages_and_statistics(site) -> None:
    output, result, index, store = site

    assert (result.articles, result.annotated) == (4, 2)
    for page in ("index.html", "explorar/index.html", "analises/index.html", "universidades/index.html", "sobre/index.html", "404.html"):
        assert (output / "public" / page).is_file()
    assert (output / "public" / "static" / "logos" / "usp.png").is_file()
    assert not (output / "site.sqlite").exists()
    records, _ = index.publication_snapshot()
    assert {row["journal"] for row in records} == {"USP", "FURG"}
    assert {row["region"] for row in records} == {"Sudeste", "Sul"}
    assert next(row["date"] for row in records if row["title"] == "Data inválida") is None
    analysis = (output / "public" / "analises" / "index.html").read_text(encoding="utf-8")
    assert "Resultado de pesquisa" in analysis


def test_lexical_search_ignores_accents_and_respects_filters(site) -> None:
    output, _result, index, store = site

    page = index.search(ArticleQuery(text="vacína dengue"), offset=0, limit=10)
    assert page.mode == "lexical"
    assert page.results[0]["url"] == "https://usp.br/a"

    filtered = index.search(ArticleQuery(text="dengue", region="Sul"), offset=0, limit=10)
    assert filtered.results == []

    annotated = index.latest(ArticleQuery(analyzed_only=True, field="ciencias_biologicas"), offset=0, limit=10)
    assert [item["url"] for item in annotated.results] == ["https://furg.br/c"]


def test_hybrid_search_fuses_semantic_hits_and_applies_analysis_filters(site) -> None:
    output, _result, index, store = site
    semantic_hits = [article_id("https://furg.br/c"), article_id("https://usp.br/b")]
    index.semantic = lambda vector, limit, filters: semantic_hits
    index.encoder = CachedEncoder(type("Encoder", (), {"encode_query": staticmethod(lambda query: [0.0])})())

    page = index.search(ArticleQuery(text="oceano", analyzed_only=True), offset=0, limit=10)

    assert page.mode == "hybrid"
    assert [item["url"] for item in page.results] == ["https://furg.br/c"]
    assert set(page.timings) == {"lexical", "embedding", "ann"}


def test_semantic_failure_falls_back_to_lexical(site) -> None:
    output, _result, index, store = site

    def broken(*_args):
        raise RuntimeError("model crashed")

    index.semantic = broken
    index.encoder = CachedEncoder(type("E", (), {"encode_query": staticmethod(lambda q: [0.0])})())
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


def test_search_text_does_not_expose_query_parser_syntax() -> None:
    assert search_text('vacina "dengue" OR (x) AND -') == "vacina dengue or and"
    assert search_text("?!") == ""


def test_api_serves_pages_listing_search_and_article(site) -> None:
    output, _result, index, store = site
    client = TestClient(create_app(output, index=index))

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
    health = client.get("/healthz").json()
    assert health["ok"] and health["database"] and not health["semantic"]
    assert health["semantic_status"]["state"] == "disabled"
    assert not health["degraded"]


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


def test_statistics_deduplicate_keywords_without_a_data_bundle() -> None:
    first = annotation("https://usp.br/a", "evento", ["ciencias_da_saude"]) | {"topics.keywords": ["USP", "Vacinas", "vacina"]}
    second = annotation("https://usp.br/b", "evento", []) | {"topics.keywords": ["vacina", "Cultura"]}
    annotations = {row["url"]: row for row in (first, second)}
    deduplicate_keywords(annotations)
    stats = site_statistics(pd.DataFrame(ARTICLES[:2]), annotations)

    assert annotations["https://usp.br/a"]["topics.keywords"] == ["vacina"]
    assert stats["analysis"]["all"]["keywords"][0]["count"] == 2


def test_criteria_page_lists_exactly_the_contract_categories() -> None:
    from university_news.enrichment.schema import ClippingSchema
    from university_news.site.criteria import CRITERIA

    template = ClippingSchema.load().template
    for criterion in CRITERIA:
        section, field = criterion["path"].split(".")
        value = template[section][field]
        allowed = (value[0] if isinstance(value, list) else value).split("|")
        assert list(criterion["values"]) == allowed, criterion["path"]


def test_article_changes_are_visible_without_rebuilding_pages(site) -> None:
    _output, _result, index, store = site
    url = "https://usp.br/nova"
    identifier = stable_article_id(url)
    store.bootstrap_articles("usp", [{
        "url": url, "title": "Nova descoberta sobre dengue", "date": "2026-11-03",
    }])

    assert index.latest(ArticleQuery(), offset=0, limit=1).results[0]["id"] == str(identifier)
    assert str(identifier) in index.lexical(ArticleQuery(text="descoberta"))
    store.save_article(identifier, site="usp", url=url, status="rejected", payload={})
    assert index.article(str(identifier)) is None
    assert str(identifier) not in index.lexical(ArticleQuery(text="descoberta"))


def test_latest_annotation_is_deterministic_and_deletion_restores_previous(site) -> None:
    _output, _result, index, store = site
    identifier = stable_article_id("https://usp.br/a")
    store.register_enrichment_profile({
        "id": "second", "provider": "openwebui", "model": "new/model",
        "schema_version": "2.1.0", "prompt_sha256": "new", "settings": {},
    })
    store.save_enrichment(identifier, {"analysis": {
        "summary": {"one_sentence": "Resumo atualizado"},
        "classification": {"news_category": "evento"},
    }}, profile_id="second")
    # Fix ordering explicitly; timestamps can tie at the clock's resolution.
    store.connection.execute(
        "UPDATE enrichments SET created_at = now() + interval '1 minute' "
        "WHERE article_id = %s AND profile_id = 'second'", (identifier,)
    )
    record = index.article(str(identifier))
    assert record["annotation"]["profile_id"] == "second"
    assert record["annotation"]["model"] == "new/model"
    assert str(identifier) in index.lexical(ArticleQuery(text="atualizado", category="evento"))
    store.connection.execute(
        "DELETE FROM enrichments WHERE article_id = %s AND profile_id = 'second'",
        (identifier,),
    )
    assert index.article(str(identifier))["annotation"]["profile_id"] == "profile"


def test_projection_rolls_back_with_source_transaction_and_reader_cannot_write(site) -> None:
    from psycopg.errors import ReadOnlySqlTransaction

    _output, _result, index, store = site
    identifier = stable_article_id("https://usp.br/a")
    with pytest.raises(RuntimeError, match="rollback"):
        with store.connection.transaction():
            store.connection.execute("DELETE FROM articles WHERE id = %s", (identifier,))
            raise RuntimeError("rollback")
    assert index.article(str(identifier)) is not None
    with index.pool.connection() as connection:
        with pytest.raises(ReadOnlySqlTransaction):
            connection.execute("DELETE FROM site_documents")


def test_concurrent_profiles_keep_one_consistent_site_document(site) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _output, _result, index, store = site
    identifier = stable_article_id("https://usp.br/b")
    for name in ("parallel-a", "parallel-b"):
        store.register_enrichment_profile({
            "id": name, "provider": "openwebui", "model": name,
            "schema_version": "2.1.0", "prompt_sha256": name, "settings": {},
        })
    barrier = Barrier(2)

    def annotate(name):
        connection = PostgresStore.open(os.environ["TEST_DATABASE_URL"])
        try:
            barrier.wait(timeout=5)
            return connection.save_enrichment(identifier, {"analysis": {
                "summary": {"one_sentence": name}
            }}, profile_id=name)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert all(executor.map(annotate, ("parallel-a", "parallel-b")))
    expected = store.connection.execute(
        "SELECT profile_id FROM enrichments WHERE article_id = %s "
        "ORDER BY created_at DESC, profile_id LIMIT 1", (identifier,)
    ).fetchone()["profile_id"]
    record = index.article(str(identifier))
    assert record["annotation"]["profile_id"] == expected
    assert record["annotation"]["summary.one_sentence"] == expected


def test_stale_semantic_ids_are_filtered_and_search_counts_are_bounded(site) -> None:
    _output, _result, index, store = site
    index.encoder = CachedEncoder(type("Encoder", (), {"encode_query": staticmethod(lambda q: [0.0])})())
    index.semantic = lambda vector, limit, filters: [
        article_id("https://deleted.br/noticia"), article_id("https://furg.br/c")
    ]
    result = index.search(ArticleQuery(text="inexistente"), offset=0, limit=10)
    assert result.total == 1
    assert result.total_is_exact is False
    assert result.results[0]["journal"] == "FURG"


def test_api_reports_database_failure_and_has_managed_connection_lifecycle(site, monkeypatch) -> None:
    output, _result, index, store = site
    with TestClient(create_app(output, index=index)) as client:
        assert client.get("/healthz").status_code == 200

        def unavailable():
            raise RuntimeError("unavailable")

        monkeypatch.setattr(index, "health", unavailable)
        assert client.get("/healthz").status_code == 503
    assert index.pool.closed


def test_build_publishes_the_criteria_page(site) -> None:
    output, _result, index, store = site
    page = (output / "public" / "criterios" / "index.html").read_text(encoding="utf-8")

    assert "Resultado de pesquisa" in page
    assert 'id="hype"' in page and "Sensacionalismo" in page
    assert "ODS 17" in page


def test_readiness_distinguishes_bm25_availability_from_verified_semantics(site):
    output, _result, index, store = site
    app = create_app(output, index=index, semantic={"health_interval_seconds": 60})
    # Do not start the background loader here: explicitly drive its verified state.
    client = TestClient(app)
    assert client.get("/healthz").json()["ok"]
    assert client.get("/healthz").json()["degraded"]
    assert client.get("/readyz").status_code == 503
    index.semantic_health.record(ready=True, state="ready", efficient=True, hits=1)
    assert client.get("/readyz").status_code == 200
    index.semantic_health.record(ready=True, state="ready", efficient=True, coverage_estimate=0.2)
    assert client.get("/healthz").json()["degraded"]
    assert client.get("/readyz").status_code == 200
    index.semantic_health.failure(RuntimeError("storage failed"))
    assert client.get("/readyz").status_code == 503


def test_request_failure_updates_semantic_health_instead_of_hiding_fallback(site):
    from university_news.search.semantic import SemanticHealth

    output, _result, index, store = site
    index.semantic_health = SemanticHealth(enabled=True)
    index.semantic_health.record(ready=True, state="ready", efficient=True)
    index.encoder = CachedEncoder(type("Encoder", (), {"encode_query": staticmethod(lambda q: [1.0])})())

    def unavailable(*args):
        raise RuntimeError("storage failed")

    index.semantic = unavailable
    result = index.search(ArticleQuery(text="dengue"), offset=0, limit=10)
    assert result.mode == "lexical" and result.results
    assert result.retrieval["semantic_candidates"] == 0
    assert not index.semantic_health.snapshot()["ready"]


def test_check_search_cli_checks_reference_ids_without_bm25_fallback(site, tmp_path, monkeypatch, capsys):
    from university_news.search.semantic import SemanticRuntime
    from university_news.search.vector_index import connect_database, embedding_contract, lance_schema
    from university_news.site import cli as site_cli

    class Encoder:
        dimension = 2
        context_length = 32
        model_repo, model_file, model_path = "test", "test.gguf", None

        def encode_query(self, text):
            return [1.0, 0.0]

    output, result, index, store = site
    table = connect_database(tmp_path / "cli-lance").create_table("articles", schema=lance_schema(2))
    expected = article_id("https://usp.br/a")
    table.add([{"article_id": expected, "content_hash": "test", "journal": "USP", "region": "Sudeste",
                "date": "2026-07-10", "truncated": False, "vector": [1.0, 0.0]}])
    runtime = SemanticRuntime(table, Encoder(), metadata=embedding_contract(Encoder(), "articles"),
                               table_name="articles", document_context_length=32)
    monkeypatch.setenv("SITE_DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setattr(site_cli, "semantic_settings", lambda: {"gpu_layers": 0})
    monkeypatch.setattr(SemanticRuntime, "load", classmethod(lambda cls, settings: runtime))
    assert site_cli.run(["check-search", "--repeat", "1", "--expected-id", expected]) == 0
    assert json.loads(capsys.readouterr().out)["reference_found"]
    assert site_cli.run(["check-search", "--repeat", "1", "--expected-id", "missing"]) == 1
    assert not json.loads(capsys.readouterr().out)["ok"]


@pytest.mark.skipif(os.getenv("RUN_REAL_EMBEDDINGS") != "1", reason="explicit local GGUF model test")
def test_real_qwen_lancedb_and_postgres_retrieve_reference_news(site, tmp_path, monkeypatch):
    from pathlib import Path

    from university_news.search.diagnostics import check_search
    from university_news.search.embeddings import QwenGGUFEmbedder
    from university_news.search.semantic import SemanticHealth, SemanticRuntime
    from university_news.search.vector_index import (
        article_content, connect_database, index_records, lance_schema, write_metadata,
    )
    from university_news.settings import config_section

    model_path = Path(str(config_section("embedding")["model_path"]))
    assert model_path.is_file(), "Install the configured GGUF model before running this test"
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    output, _result, index, store = site
    documents = [
        {"url": "https://usp.br/reference/dengue", "journal": "USP", "date": "2026-11-01",
         "title": "Vacina protege contra a dengue",
         "text": "Pesquisadores estudam imunização para impedir a transmissão da dengue. "
                 "O vírus é transmitido pelo mosquito Aedes aegypti. A vacina protege a população "
                 "contra a infecção e reduz internações. O trabalho avalia prevenção e saúde pública."},
        {"url": "https://furg.br/reference/oceano", "journal": "FURG", "date": "2026-11-01",
         "title": "Expedição investiga correntes oceânicas",
         "text": "Um navio oceanográfico mede a temperatura e a salinidade do mar. Cientistas "
                 "analisam correntes marítimas e a biodiversidade de recifes no litoral brasileiro."},
        {"url": "https://usp.br/reference/energia", "journal": "USP", "date": "2026-11-01",
         "title": "Novas células solares produzem eletricidade",
         "text": "Engenheiros desenvolveram painéis fotovoltaicos mais eficientes. Os materiais "
                 "convertem luz solar em energia elétrica e podem reduzir o custo da geração renovável."},
    ]
    for document in documents:
        store.bootstrap_articles(document["journal"].lower(), [document])
    encoder = QwenGGUFEmbedder(model_path=model_path, context_length=2048, gpu_layers=0, threads=2)
    embedded = encoder.encode_documents([article_content(doc) for doc in documents])
    path = tmp_path / "real-lance"
    table = connect_database(path).create_table("real_articles", schema=lance_schema(encoder.dimension))
    index_records(table, [
        {"article_id": article_id(doc["url"]), "content_hash": "test", "journal": doc["journal"],
         "region": "Sul" if doc["journal"] == "FURG" else "Sudeste", "date": doc["date"],
         "truncated": False, "vector": vector}
        for doc, vector in zip(documents, embedded.vectors)
    ])
    write_metadata(path, table="real_articles", embedder=encoder)
    runtime = SemanticRuntime.load({
        "lancedb_path": str(path), "lancedb_table": "real_articles",
        "model_path": str(model_path), "model_repo": encoder.model_repo,
        "model_file": encoder.model_file, "query_context_length": 1024,
        "document_context_length": 2048, "gpu_layers": 0, "threads": 2,
    })
    query = "Como evitar infecções propagadas pelo Aedes aegypti?"
    expected = article_id(documents[0]["url"])
    report = check_search(runtime, query, top_k=1, repeat=3, concurrency=2,
                          expected_ids=[expected],
                          resolve=lambda ids: index._matching_rows(ids, ArticleQuery()))
    assert report["ok"], report
    print(json.dumps({key: value for key, value in report.items() if key != "samples"}, ensure_ascii=False))
    index.encoder = CachedEncoder(runtime.encoder)
    index.semantic = runtime.search
    index.semantic_health = SemanticHealth(enabled=True)
    index.semantic_health.record(**report["probe"])
    page = index.search(ArticleQuery(text=query), offset=0, limit=10)
    assert page.mode == "hybrid" and page.retrieval["semantic_candidates"] == 3
