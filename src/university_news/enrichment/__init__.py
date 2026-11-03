"""LLM-backed metadata enrichment for university news."""

from .client import ClippingEnricher, EnrichmentSettings
from .schema import ClippingSchema, ClippingValidationError

__all__ = [
    "ClippingEnricher",
    "ClippingSchema",
    "ClippingValidationError",
    "EnrichmentSettings",
]
