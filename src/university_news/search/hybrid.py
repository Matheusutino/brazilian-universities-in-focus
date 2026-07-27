import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from rank_bm25 import BM25Okapi

from ..sources.catalog import region_for_site
from .embeddings import QwenGGUFEmbedder


TOKEN_PATTERN = re.compile(r"\b\w+\b", flags=re.UNICODE)
RRF_K = 60


@dataclass(frozen=True, slots=True)
class SearchFilters:
    journals: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    start_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    article: dict[str, Any]
    score: float
    semantic_rank: int | None
    lexical_rank: int | None


class SemanticRetriever(Protocol):
    def search(
        self,
        query: str,
        limit: int,
        filters: SearchFilters = SearchFilters(),
    ) -> list[str]: ...


class LexicalOnlyRetriever:
    """Empty retriever used to run BM25 without vector infrastructure."""

    def search(
        self,
        query: str,
        limit: int,
        filters: SearchFilters = SearchFilters(),
    ) -> list[str]:
        del query, limit, filters
        return []


def article_id(article: dict[str, Any]) -> str:
    url = article.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("Article has no URL")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, url))


def tokenize(value: str) -> list[str]:
    return TOKEN_PATTERN.findall(value.lower())


def article_text(article: dict[str, Any]) -> str:
    return "\n".join(
        str(value).strip()
        for key in ("title", "subtitle", "text")
        if isinstance((value := article.get(key)), str) and value.strip()
    )


def matches_filters(article: dict[str, Any], filters: SearchFilters) -> bool:
    journal = str(article.get("journal", "")).upper()
    if filters.journals and journal not in {value.upper() for value in filters.journals}:
        return False
    if filters.regions and region_for_site(journal) not in filters.regions:
        return False
    value = article.get("date")
    parsed = None
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value[:10])
        except ValueError:
            pass
    if parsed and parsed.year < 1990:
        parsed = None
    if filters.start_date and (parsed is None or parsed < filters.start_date):
        return False
    return not filters.end_date or (parsed is not None and parsed <= filters.end_date)


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


class LanceDBRetriever:
    def __init__(self, table, embedder: QwenGGUFEmbedder) -> None:
        self.table = table
        self.embedder = embedder

    def search(
        self,
        query: str,
        limit: int,
        filters: SearchFilters = SearchFilters(),
    ) -> list[str]:
        try:
            vector = self.embedder.encode_query(query)
            search = (
                self.table.search(vector, vector_column_name="vector")
                .distance_type("cosine")
                .select(["article_id"])
                .limit(limit)
            )
            condition = lance_filter(filters)
            if condition:
                search = search.where(condition, prefilter=True)
            response = search.to_arrow()
        except Exception:
            # The lexical ranking remains available if the optional local
            # vector index or embedding model cannot be loaded.
            return []
        return [str(value) for value in response.column("article_id").to_pylist()]


class HybridNewsSearch:
    def __init__(self, articles: list[dict[str, Any]], semantic: SemanticRetriever) -> None:
        self.articles = {article_id(article): article for article in articles if article.get("url")}
        self.semantic = semantic
        self.identifiers = list(self.articles)
        self.lexical_tokens = [
            tokenize(article_text(self.articles[item])) for item in self.identifiers
        ]
        self.bm25 = BM25Okapi(self.lexical_tokens)

    def search(
        self,
        query: str,
        *,
        filters: SearchFilters = SearchFilters(),
        candidates: int = 30,
        limit: int = 8,
    ) -> list[SearchResult]:
        if not query.strip() or candidates < 1 or limit < 1:
            return []
        semantic_ranking = self.semantic.search(query, candidates, filters)
        query_tokens = tokenize(query)
        query_vocabulary = set(query_tokens)
        lexical_scores = self.bm25.get_scores(query_tokens)
        lexical_ranking = [
            identifier
            for identifier, score, tokens in sorted(
                zip(self.identifiers, lexical_scores, self.lexical_tokens),
                key=lambda item: item[1],
                reverse=True,
            )
            # BM25's IDF is exactly zero when a term occurs in half of a tiny
            # corpus. Preserve exact lexical matches in that edge case.
            if score > 0 or query_vocabulary.intersection(tokens)
        ][:candidates]
        scores = rrf_fuse(semantic_ranking, lexical_ranking)
        semantic_positions = {identifier: rank for rank, identifier in enumerate(semantic_ranking, start=1)}
        lexical_positions = {identifier: rank for rank, identifier in enumerate(lexical_ranking, start=1)}
        results = []
        for identifier, score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
            article = self.articles.get(identifier)
            if article is not None and matches_filters(article, filters):
                results.append(SearchResult(article, score, semantic_positions.get(identifier), lexical_positions.get(identifier)))
            if len(results) == limit:
                break
        return results
