"""Pipeline de coleta e extracao de noticias universitarias."""

from .config import SITES, SiteConfig
from .pipeline import NewsPipeline, PipelineStats

__all__ = ["NewsPipeline", "PipelineStats", "SITES", "SiteConfig"]
