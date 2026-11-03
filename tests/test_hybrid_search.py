from datetime import date

from university_news.search.hybrid import SearchFilters, lance_filter, rrf_fuse
from university_news.site.search import lancedb_semantic


def test_rrf_fuse_rewards_documents_ranked_by_both_retrievers() -> None:
    scores = rrf_fuse(["semantic", "both"], ["lexical", "both"])

    assert max(scores, key=scores.get) == "both"
    assert scores["semantic"] == scores["lexical"]


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


def test_semantic_search_pushes_filters_into_lancedb() -> None:
    table = FakeLanceTable()
    search = lancedb_semantic(table)

    identifiers = search([1.0, 2.0], 5, SearchFilters(journals=("USP",)))

    assert identifiers == ["first", "second"]
    assert table.query.condition == "journal IN ('USP')"
