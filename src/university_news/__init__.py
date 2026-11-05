"""Collect, process, and search news from Brazilian universities."""

from .sources.catalog import SITES, SiteConfig

__all__ = ["SITES", "SiteConfig"]
