from datetime import date

from university_news.search.hybrid import SearchFilters, lance_filter, rrf_fuse


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
