"""Collect, process, and search news from Brazilian universities."""

from .pipeline.orchestration import NewsPipeline, PipelineStats
from .sources.catalog import SITES, SiteConfig

__all__ = ["NewsPipeline", "PipelineStats", "SITES", "SiteConfig"]
