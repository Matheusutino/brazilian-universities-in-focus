import os
from types import SimpleNamespace

from university_news.pipeline.llm_extraction import LLMArticleExtractor


class FakeStrategy:
    def __init__(self, response):
        self.response = response
        self.sections: list[str] = []

    def run(self, url, sections):
        self.sections = sections
        return self.response


def test_llm_extractor_normalizes_structured_response() -> None:
    strategy = FakeStrategy(
        [
            {
                "title": " Título ",
                "author": " Autora ",
                "date": "2026-01-02",
                "text": " Texto integral. ",
                "tags": ["USP", "USP"],
                "links": ["/fonte/#secao", "mailto:teste@example.com"],
                "images": ["/imagem.jpg", "/imagem.jpg"],
                "error": False,
            }
        ]
    )
    extractor = LLMArticleExtractor(strategy=strategy)

    article = extractor.extract(
        (
            "<html><body><article>Card incorreto do menu</article>"
            "<main id='main'><p>Notícia correta</p></main></body></html>"
        ),
        "https://jornal.usp.br/noticia/",
    )

    assert article == {
        "url": "https://jornal.usp.br/noticia/",
        "title": "Título",
        "subtitle": None,
        "author": "Autora",
        "date": "2026-01-02",
        "text": "Texto integral.",
        "tags": ["USP"],
        "links": ["https://jornal.usp.br/fonte/"],
        "images": ["https://jornal.usp.br/imagem.jpg"],
    }
    assert "Card incorreto" not in strategy.sections[0]
    assert "Notícia correta" in strategy.sections[0]


def test_llm_extractor_uses_configured_content_selector() -> None:
    strategy = FakeStrategy(
        [{"title": "UFAL", "text": "Texto", "links": [], "images": []}]
    )
    extractor = LLMArticleExtractor(
        strategy=strategy,
        content_selector="article#content",
    )

    extractor.extract(
        "<main>Conteúdo errado</main><article id='content'>Conteúdo UFAL</article>",
        "https://noticias.ufal.br/materia",
    )

    assert "Conteúdo UFAL" in strategy.sections[0]
    assert "Conteúdo errado" not in strategy.sections[0]


def test_llm_extractor_combines_multiple_configured_sections() -> None:
    strategy = FakeStrategy([{"title": "Unesp", "text": "Texto"}])
    extractor = LLMArticleExtractor(
        strategy=strategy,
        content_selector=".article-header, article.type-post, .article-date",
    )

    extractor.extract(
        """<header class="article-header">Título e subtítulo</header>
        <article class="type-post">Autor e corpo</article>
        <div class="article-date">26/06/2026</div><aside>Ruído</aside>""",
        "https://jornal.unesp.br/2026/06/26/materia/",
    )

    section = strategy.sections[0]
    assert "Título e subtítulo" in section
    assert "Autor e corpo" in section
    assert "26/06/2026" in section
    assert "Ruído" not in section


def test_llm_extractor_requires_openrouter_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    extractor = LLMArticleExtractor()

    try:
        extractor.extract("<main>Texto</main>", "https://jornal.usp.br/noticia/")
    except ValueError as exc:
        assert "OPENROUTER_API_KEY" in str(exc)
    else:
        raise AssertionError("A chave do OpenRouter deveria ser obrigatoria")


def test_model_environment_accepts_openrouter_prefix(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "openrouter/example/model")
    extractor = LLMArticleExtractor()

    strategy = extractor._strategy()

    assert strategy.llm_config.provider == "openrouter/example/model"
    assert os.environ["OPENROUTER_API_KEY"] == "test-key"


def test_real_crawl4ai_strategy_contract_without_network(monkeypatch) -> None:
    content = (
        '{"title":"Título","author":"Autor","date":"2026-01-02",'
        '"text":"Texto completo","tags":[],"links":[],"images":[]}'
    )
    response = SimpleNamespace(
        usage=SimpleNamespace(
            completion_tokens=20,
            prompt_tokens=100,
            total_tokens=120,
            completion_tokens_details=None,
            prompt_tokens_details=None,
        ),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
    )
    monkeypatch.setattr(
        "crawl4ai.extraction_strategy.perform_completion_with_backoff",
        lambda *args, **kwargs: response,
    )
    extractor = LLMArticleExtractor(api_key="test-key", model="example/model")

    article = extractor.extract(
        "<main><h1>Título</h1><p>Texto completo</p></main>",
        "https://jornal.usp.br/noticia/",
    )

    assert article["title"] == "Título"
    assert article["author"] == "Autor"
    assert extractor._strategy().total_usage.total_tokens == 120
