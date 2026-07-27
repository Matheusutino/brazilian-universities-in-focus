from datetime import date

from university_news.search.hybrid import (
    HybridNewsSearch,
    LanceDBRetriever,
    LexicalOnlyRetriever,
    SearchFilters,
    article_id,
    lance_filter,
)


class StaticSemantic:
    def __init__(self, identifiers):
        self.identifiers = identifiers

    def search(self, query, limit, filters=SearchFilters()):
        assert query == "pesquisa climática"
        self.filters = filters
        return self.identifiers[:limit]


def test_hybrid_search_fuses_bm25_with_semantic_and_filters_results() -> None:
    articles = [
        {"url": "https://example/usp", "journal": "USP", "date": "2025-01-10", "title": "Pesquisa climática", "text": "Estudo sobre mudanças climáticas no Brasil."},
        {"url": "https://example/ufms", "journal": "UFMS", "date": "2025-01-12", "title": "Pesquisa", "text": "Notícia diferente."},
        {"url": "https://example/uem", "journal": "UEM", "date": "2024-01-12", "title": "Clima", "text": "Mudanças climáticas e pesquisa científica."},
    ]
    search = HybridNewsSearch(articles, StaticSemantic([article_id(articles[1]), article_id(articles[0])]))

    results = search.search(
        "pesquisa climática",
        filters=SearchFilters(regions=("Sudeste",), start_date=date(2025, 1, 1)),
        candidates=30,
        limit=10,
    )

    assert [result.article["journal"] for result in results] == ["USP"]
    assert results[0].semantic_rank == 2
    assert results[0].lexical_rank == 1


def test_hybrid_search_rejects_invalid_dates_when_filtering() -> None:
    article = {
        "url": "https://example/furg",
        "journal": "FURG",
        "date": "0137-11-26",
        "title": "Pesquisa",
        "text": "Pesquisa universitária.",
    }
    search = HybridNewsSearch([article], StaticSemantic([article_id(article)]))

    results = search.search(
        "pesquisa climática",
        filters=SearchFilters(start_date=date(2000, 1, 1)),
    )

    assert results == []


def test_hybrid_search_can_run_without_vector_service() -> None:
    articles = [
        {
            "url": "https://example/usp",
            "journal": "USP",
            "title": "Vacina brasileira avança",
            "text": "Pesquisa em saúde pública.",
        },
        {
            "url": "https://example/ufpa",
            "journal": "UFPA",
            "title": "Festival de música",
            "text": "Programação cultural.",
        },
    ]

    results = HybridNewsSearch(articles, LexicalOnlyRetriever()).search("vacina")

    assert [result.article["journal"] for result in results] == ["USP"]
    assert results[0].lexical_rank == 1
    assert results[0].semantic_rank is None


def test_lance_filter_escapes_values_and_applies_dates_before_search() -> None:
    filters = SearchFilters(
        journals=("USP", "D'ÁVILA"),
        regions=("Sudeste",),
        start_date=date(2024, 1, 1),
        end_date=date(2025, 12, 31),
    )

    assert lance_filter(filters) == (
        "journal IN ('USP', 'D''ÁVILA') AND region IN ('Sudeste') "
        "AND date >= '2024-01-01' AND date <= '2025-12-31'"
    )


class StaticEmbedder:
    def encode_query(self, query):
        assert query == "pesquisa"
        return [1.0, 2.0]


class FakeLanceQuery:
    def __init__(self):
        self.condition = None

    def distance_type(self, value):
        assert value == "cosine"
        return self

    def select(self, columns):
        assert columns == ["article_id"]
        return self

    def limit(self, value):
        assert value == 5
        return self

    def where(self, condition, prefilter):
        assert prefilter is True
        self.condition = condition
        return self

    def to_arrow(self):
        import pyarrow as pa

        return pa.table({"article_id": ["first", "second"]})


class FakeLanceTable:
    def __init__(self):
        self.query = FakeLanceQuery()

    def search(self, vector, vector_column_name):
        assert vector == [1.0, 2.0]
        assert vector_column_name == "vector"
        return self.query


def test_lancedb_retriever_uses_prefilter() -> None:
    table = FakeLanceTable()
    retriever = LanceDBRetriever(table, StaticEmbedder())

    identifiers = retriever.search(
        "pesquisa",
        5,
        SearchFilters(journals=("USP",)),
    )

    assert identifiers == ["first", "second"]
    assert table.query.condition == "journal IN ('USP')"
