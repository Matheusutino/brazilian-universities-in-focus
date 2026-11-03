"""Workload estimates derived from the configured enrichment strategy."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from ..settings import config_section


@dataclass(frozen=True, slots=True)
class EnrichmentWorkload:
    articles: int
    workers: int
    calls_per_article: int
    validation_attempts: int
    transport_retries: int
    minimum_llm_calls: int
    maximum_validation_calls: int
    theoretical_maximum_http_attempts: int
    maximum_concurrent_requests: int
    estimated_article_seconds: float
    estimated_duration_seconds: int

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)


def enrichment_workload(
    articles: int,
    *,
    workers: int | None = None,
    calls_per_article: int | None = None,
    validation_attempts: int | None = None,
    transport_retries: int | None = None,
    measured_article_seconds: float | None = None,
) -> EnrichmentWorkload:
    if articles < 0:
        raise ValueError("articles cannot be negative")
    enrichment = config_section("enrichment")
    provider = str(enrichment.get("provider", "openwebui"))
    provider_settings = enrichment.get(provider)
    if not isinstance(provider_settings, dict):
        raise TypeError(f"Missing [enrichment.{provider}] configuration")
    configured_workers = int(workers or enrichment.get("workers", 1))
    if configured_workers < 1:
        raise ValueError("enrichment workers must be at least 1")
    calls = int(calls_per_article or provider_settings.get("calls", 1))
    validations = int(
        validation_attempts or provider_settings.get("validation_attempts", 1)
    )
    retries = int(
        transport_retries
        if transport_retries is not None
        else provider_settings.get("max_retries", 0)
    )
    fallback = float(enrichment.get("fallback_seconds_per_call", 60.0)) * calls
    article_seconds = float(measured_article_seconds or fallback)
    batches = math.ceil(articles / configured_workers) if articles else 0
    minimum = articles * calls
    validation_maximum = minimum * validations
    return EnrichmentWorkload(
        articles=articles,
        workers=configured_workers,
        calls_per_article=calls,
        validation_attempts=validations,
        transport_retries=retries,
        minimum_llm_calls=minimum,
        maximum_validation_calls=validation_maximum,
        theoretical_maximum_http_attempts=validation_maximum * (retries + 1),
        maximum_concurrent_requests=min(configured_workers, articles),
        estimated_article_seconds=round(article_seconds, 3),
        estimated_duration_seconds=math.ceil(batches * article_seconds),
    )
