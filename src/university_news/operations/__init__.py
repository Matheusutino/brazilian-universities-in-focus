"""Durable incremental operation of the university-news pipeline."""

from .store import Job, PostgresStore

__all__ = ["Job", "PostgresStore"]
