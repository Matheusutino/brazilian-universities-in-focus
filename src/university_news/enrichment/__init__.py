"""LLM-backed metadata enrichment for university news."""

from .client import ClippingEnricher, EnrichmentSettings, OpenWebUISettings
from .schema import ClippingSchema, ClippingValidationError

__all__ = [
    "ClippingEnricher",
    "ClippingSchema",
    "ClippingValidationError",
    "EnrichmentSettings",
    "OpenWebUISettings",
]
