"""Shared retrieval helpers: metadata filters and Reciprocal Rank Fusion."""

from dataclasses import dataclass
from datetime import date

RRF_K = 60


@dataclass(frozen=True, slots=True)
class SearchFilters:
    journals: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    start_date: date | None = None
    end_date: date | None = None


def rrf_fuse(*rankings: list[str], k: int = RRF_K) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, identifier in enumerate(ranking, start=1):
            scores[identifier] = scores.get(identifier, 0.0) + 1 / (k + rank)
    return scores


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def lance_filter(filters: SearchFilters) -> str | None:
    clauses = []
    if filters.journals:
        values = ", ".join(sql_string(value.upper()) for value in filters.journals)
        clauses.append(f"journal IN ({values})")
    if filters.regions:
        values = ", ".join(sql_string(value) for value in filters.regions)
        clauses.append(f"region IN ({values})")
    if filters.start_date:
        clauses.append(f"date >= {sql_string(filters.start_date.isoformat())}")
    if filters.end_date:
        clauses.append(f"date <= {sql_string(filters.end_date.isoformat())}")
    return " AND ".join(clauses) or None
