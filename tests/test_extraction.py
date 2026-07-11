import json

from jornais_new.config import UFAL, UFMS, UFPA, UFRGS, UNB, UNESP
from jornais_new.extraction import StructuredExtractor
from jornais_new.fetching import ContentFetcher
from jornais_new.io_utils import read_jsonl
from jornais_new.llm_extraction import LLMArticleExtractor


def test_extraction_normalizes_fields_and_resumes(tmp_path, monkeypatch) -> None:
    url = "https://jornal.usp.br/ciencias/materia/"
    raw_directory = tmp_path / "raw"
    raw_directory.mkdir()
    raw_path = raw_directory / ContentFetcher.filename_for_url(url)
    raw_path.write_text("<html><article>Texto</article></html>", encoding="utf-8")

    payload = {
        "title": " Titulo ",
        "author": None,
        "date": "2024-01-02",
        "text": " Conteudo da materia ",
        "tags": "ciencia; USP; ciencia",
    }

    def fake_extract(*args, **kwargs):
        if kwargs["output_format"] == "json":
            return json.dumps(payload)
        return """<doc><main>
          <p>Leia <ref target="/outra/#trecho">outra matéria</ref>.</p>
          <p><ref target="https://example.com/fonte">Fonte</ref></p>
          <graphic src="/wp-content/uploads/imagem.jpg" />
          <graphic src="/wp-content/uploads/imagem.jpg" />
        </main></doc>"""

    monkeypatch.setattr("jornais_new.extraction.extract", fake_extract)
    articles_path = tmp_path / "articles.jsonl"
    extractor = StructuredExtractor(raw_directory, articles_path)

    first = extractor.run([url])
    second = extractor.run([url])
    article = read_jsonl(articles_path)[0]

    assert first.extracted == 1
    assert second.skipped == 1
    assert article == {
        "url": url,
        "title": "Titulo",
        "subtitle": None,
        "author": None,
        "date": "2024-01-02",
        "text": "Conteudo da materia",
        "tags": ["USP", "ciencia"],
        "links": [
            "https://example.com/fonte",
            "https://jornal.usp.br/outra/",
        ],
        "images": ["https://jornal.usp.br/wp-content/uploads/imagem.jpg"],
    }


def test_extraction_records_empty_text_and_missing_html(tmp_path, monkeypatch) -> None:
    existing_url = "https://jornal.usp.br/vazio/"
    missing_url = "https://jornal.usp.br/ausente/"
    raw_directory = tmp_path / "raw"
    raw_directory.mkdir()
    (raw_directory / ContentFetcher.filename_for_url(existing_url)).write_text(
        "<html></html>", encoding="utf-8"
    )
    monkeypatch.setattr(
        "jornais_new.extraction.extract",
        lambda *args, **kwargs: json.dumps({"title": "Sem texto", "text": ""}),
    )

    result = StructuredExtractor(raw_directory, tmp_path / "articles.jsonl").run(
        [existing_url, missing_url]
    )

    assert result.extracted == 0
    assert {error["error"] for error in result.errors} == {"ValueError", "HTMLNotFound"}


def test_old_articles_schema_requires_force(tmp_path) -> None:
    articles_path = tmp_path / "articles.jsonl"
    articles_path.write_text(
        json.dumps({"url": "https://jornal.usp.br/antiga/", "text": "Texto"}) + "\n",
        encoding="utf-8",
    )

    try:
        StructuredExtractor(tmp_path / "raw", articles_path).run([])
    except ValueError as exc:
        assert "--force" in str(exc)
    else:
        raise AssertionError("Schema antigo deveria exigir reextracao")


def test_hybrid_uses_llm_when_trafilatura_has_no_author(tmp_path, monkeypatch) -> None:
    url = "https://jornal.usp.br/noticia/"
    raw_path = tmp_path / "article.html"
    raw_path.write_text("<main>Notícia</main>", encoding="utf-8")

    def fake_trafilatura(*args, **kwargs):
        if kwargs["output_format"] == "json":
            return json.dumps(
                {
                    "title": "Título",
                    "author": None,
                    "date": "2026-01-02",
                    "text": "x" * 300,
                }
            )
        return "<doc><main><p>Texto</p></main></doc>"

    class HybridStrategy:
        def run(self, received_url, sections):
            return [
                {
                    "title": "Título",
                    "author": "Autora recuperada",
                    "date": "2026-01-02",
                    "text": "Texto extraído pela LLM",
                    "tags": [],
                    "links": [],
                    "images": [],
                }
            ]

    monkeypatch.setattr("jornais_new.extraction.extract", fake_trafilatura)
    extractor = StructuredExtractor(
        tmp_path,
        tmp_path / "articles.jsonl",
        mode="hybrid",
        llm_extractor=LLMArticleExtractor(strategy=HybridStrategy()),
    )

    article = extractor.extract_file(raw_path, url)

    assert article["author"] == "Autora recuperada"
    assert article["text"] == "Texto extraído pela LLM"


def test_ufal_metadata_is_enriched_from_stable_html(tmp_path, monkeypatch) -> None:
    url = "https://noticias.ufal.br/ufal/noticias/2026/6/materia"
    raw_path = tmp_path / "article.html"
    raw_path.write_text(
        """<article id="content">
          <header><h1>Matéria da Ufal</h1><strong class="autor">Por Maria Silva – jornalista</strong>
          <time class="data" datetime="2026-06-26 12:37:17-03:00"></time></header>
          <div id="content-core"><p>Corpo editorial</p></div>
          <div class="keywords-viewlet"><a>Pesquisa</a><a>Ufal</a></div>
        </article>""",
        encoding="utf-8",
    )

    def fake_trafilatura(*args, **kwargs):
        if kwargs["output_format"] == "json":
            return json.dumps(
                {
                    "title": "Matéria da Ufal",
                    "author": None,
                    "date": None,
                    "text": "x" * 300,
                }
            )
        return "<doc><main><p>Corpo editorial</p></main></doc>"

    monkeypatch.setattr("jornais_new.extraction.extract", fake_trafilatura)
    extractor = StructuredExtractor(
        tmp_path,
        tmp_path / "articles.jsonl",
        mode="trafilatura",
        site=UFAL,
    )

    article = extractor.extract_file(raw_path, url)

    assert article["author"] == "Maria Silva – jornalista"
    assert article["date"] == "2026-06-26"
    assert article["tags"] == ["Pesquisa", "Ufal"]


def test_unesp_metadata_is_enriched_from_stable_html(tmp_path, monkeypatch) -> None:
    url = "https://jornal.unesp.br/2026/06/26/materia/"
    raw_path = tmp_path / "article.html"
    raw_path.write_text(
        """<header class="article-header"><h2>Título Unesp</h2>
          <p class="linha-fina">Subtítulo da matéria</p></header>
          <div class="col-12 col-lg-9 pr-lg-5"><div class="row">
            <div><div class="article-date"><p>26/06/2026, 15h28</p></div>
              <div class="widget_tag_cloud"><nav role="navigation">
                <a class="tag-cloud-link">Ciência</a><a class="tag-cloud-link">Unesp</a>
              </nav></div></div>
            <div><article class="type-post"><p><a rel="tag">Leandro Rocha</a></p>
              <p>Corpo editorial</p></article></div>
          </div></div>""",
        encoding="utf-8",
    )

    def fake_trafilatura(*args, **kwargs):
        if kwargs["output_format"] == "json":
            return json.dumps(
                {
                    "title": "Título Unesp",
                    "author": None,
                    "date": None,
                    "text": "x" * 300,
                }
            )
        return "<doc><main><p>Corpo editorial</p></main></doc>"

    monkeypatch.setattr("jornais_new.extraction.extract", fake_trafilatura)
    extractor = StructuredExtractor(
        tmp_path,
        tmp_path / "articles.jsonl",
        mode="trafilatura",
        site=UNESP,
    )

    article = extractor.extract_file(raw_path, url)

    assert article["subtitle"] == "Subtítulo da matéria"
    assert article["author"] == "Leandro Rocha"
    assert article["date"] == "2026-06-26"
    assert article["tags"] == ["Ciência", "Unesp"]


def test_unb_metadata_is_enriched_from_stable_html(tmp_path, monkeypatch) -> None:
    url = "https://noticias.unb.br/pesquisas-estudos-e-projetos/8537-materia"
    raw_path = tmp_path / "article.html"
    raw_path.write_text(
        """<div class="item-page">
          <span class="chapeu">RECONHECIMENTO ACADÊMICO</span>
          <div class="page-header"><h2>Título UnB</h2></div>
          <p>Subtítulo da matéria da UnB</p>
          <ul class="article-info">
            <li class="createdby"><span>Da Secretaria de Comunicação da UnB</span></li>
            <li class="published"><time datetime="2026-05-25T14:35:54-03:00">25/05/2026</time></li>
          </ul>
          <div itemprop="articleBody"><p>Corpo editorial</p></div>
          <div class="tags"><a class="label label-info">Pesquisa</a><a class="label label-info">ODS 4</a></div>
        </div>""",
        encoding="utf-8",
    )

    def fake_trafilatura(*args, **kwargs):
        if kwargs["output_format"] == "json":
            return json.dumps(
                {
                    "title": None,
                    "author": None,
                    "date": None,
                    "text": "x" * 300,
                }
            )
        return "<doc><main><p>Corpo editorial</p></main></doc>"

    monkeypatch.setattr("jornais_new.extraction.extract", fake_trafilatura)
    extractor = StructuredExtractor(
        tmp_path,
        tmp_path / "articles.jsonl",
        mode="trafilatura",
        site=UNB,
    )

    article = extractor.extract_file(raw_path, url)

    assert article["title"] == "Título UnB"
    assert article["subtitle"] == "Subtítulo da matéria da UnB"
    assert article["author"] == "Da Secretaria de Comunicação da UnB"
    assert article["date"] == "2026-05-25"
    assert article["tags"] == ["ODS 4", "Pesquisa"]


def test_ufms_metadata_is_enriched_from_stable_html(tmp_path, monkeypatch) -> None:
    url = "https://www.ufms.br/materia/"
    raw_path = tmp_path / "article.html"
    raw_path.write_text(
        """<div class="post-inner-content">
          <header class="entry-header page-header">
            <h1 class="entry-title">Título UFMS</h1>
            <div class="entry-meta">
              <span class="posted-on">
                <time class="entry-date published" datetime="2026-07-06 15:01:39">06/07/2026</time>
              </span>
              <span class="cat-links">
                <a href="https://www.ufms.br/category/destaque/">Destaque</a>
                <a href="https://www.ufms.br/category/noticias/">Notícias</a>
              </span>
            </div>
          </header>
          <div class="entry-content"><p>Corpo editorial</p></div>
        </div>""",
        encoding="utf-8",
    )

    def fake_trafilatura(*args, **kwargs):
        if kwargs["output_format"] == "json":
            return json.dumps(
                {
                    "title": None,
                    "author": None,
                    "date": None,
                    "text": "x" * 300,
                }
            )
        return "<doc><main><p>Corpo editorial</p></main></doc>"

    monkeypatch.setattr("jornais_new.extraction.extract", fake_trafilatura)
    extractor = StructuredExtractor(
        tmp_path,
        tmp_path / "articles.jsonl",
        mode="trafilatura",
        site=UFMS,
    )

    article = extractor.extract_file(raw_path, url)

    assert article["title"] == "Título UFMS"
    assert article["date"] == "2026-07-06"
    assert article["tags"] == ["Destaque", "Notícias"]


def test_ufrgs_metadata_is_enriched_from_stable_html(tmp_path, monkeypatch) -> None:
    url = "https://www.ufrgs.br/site/noticias/materia/"
    raw_path = tmp_path / "article.html"
    raw_path.write_text(
        """<div class="box-text-blog">
          <div class="breadcrumbs">Página Inicial / Todas as notícias / Matéria</div>
          <h1>Título UFRGS</h1>
          <small class="date">19 de abril de 2021 às 14:49</small>
          <img class="full-img-article" src="/site/wp-content/uploads/imagem.jpg">
          <div><p>Corpo editorial</p></div>
        </div>""",
        encoding="utf-8",
    )

    def fake_trafilatura(*args, **kwargs):
        if kwargs["output_format"] == "json":
            return json.dumps(
                {
                    "title": None,
                    "author": None,
                    "date": None,
                    "text": "x" * 300,
                }
            )
        return """<doc><main><p>Corpo editorial</p>
          <graphic src="/site/wp-content/uploads/imagem.jpg" /></main></doc>"""

    monkeypatch.setattr("jornais_new.extraction.extract", fake_trafilatura)
    extractor = StructuredExtractor(
        tmp_path,
        tmp_path / "articles.jsonl",
        mode="trafilatura",
        site=UFRGS,
    )

    article = extractor.extract_file(raw_path, url)

    assert article["title"] == "Título UFRGS"
    assert article["date"] == "2021-04-19"
    assert article["images"] == [
        "https://www.ufrgs.br/site/wp-content/uploads/imagem.jpg"
    ]


def test_ufpa_metadata_is_enriched_from_stable_html(tmp_path, monkeypatch) -> None:
    url = "https://ufpa.br/materia/"
    raw_path = tmp_path / "article.html"
    raw_path.write_text(
        """<div class="elementor-location-single">
          <div class="elementor-element-27a903d">
            <a class="elementor-post-info__terms-list-item">Internacionalização</a>
          </div>
          <div class="elementor-element-8e06085">
            <span class="elementor-post-info__terms-list-item">Cooperação Científica</span>
          </div>
          <div class="elementor-element-a06f44d">
            <h1 class="elementor-heading-title">Título UFPA</h1>
          </div>
          <div class="elementor-element-d4ffd3d">
            <time>09 de julho de 2026</time>
          </div>
          <div class="elementor-element-d30b770">
            <img src="/wp-content/uploads/2026/07/destaque.png">
          </div>
          <div class="elementor-element-664a8e87">
            <p>Corpo editorial com <a href="/documento/">documento</a>.</p>
          </div>
          <div class="elementor-element-3627d6a">
            <h2>TEXTO: Orlando Haber e Aymê Vitória/Ascom Prointer</h2>
          </div>
        </div>""",
        encoding="utf-8",
    )

    def fake_trafilatura(*args, **kwargs):
        if kwargs["output_format"] == "json":
            return json.dumps(
                {
                    "title": None,
                    "author": None,
                    "date": None,
                    "text": "x" * 300,
                }
            )
        return "<doc><main><p>Corpo editorial</p></main></doc>"

    monkeypatch.setattr("jornais_new.extraction.extract", fake_trafilatura)
    extractor = StructuredExtractor(
        tmp_path,
        tmp_path / "articles.jsonl",
        mode="trafilatura",
        site=UFPA,
    )

    article = extractor.extract_file(raw_path, url)

    assert article["title"] == "Título UFPA"
    assert article["author"] == "Orlando Haber e Aymê Vitória/Ascom Prointer"
    assert article["date"] == "2026-07-09"
    assert article["tags"] == ["Cooperação Científica", "Internacionalização"]
    assert article["links"] == ["https://ufpa.br/documento/"]
    assert article["images"] == [
        "https://ufpa.br/wp-content/uploads/2026/07/destaque.png"
    ]
