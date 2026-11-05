"""Publish one enrichment profile from PostgreSQL as analysis-ready files."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..shared.io import atomic_write_json

ANNOTATIONS_FILENAME = "annotations.jsonl"
TABLE_FILENAME = "annotations.parquet"
MANIFEST_FILENAME = "manifest.json"


def _flat_row(record: dict[str, Any]) -> dict[str, Any]:
    """One row per article: metadata plus dotted analysis fields."""
    analysis = dict(record.get("analysis") or {})
    document = analysis.pop("document", {}) or {}
    analysis.pop("schema_version", None)
    row: dict[str, Any] = {
        "article_id": record["article_id"],
        "university": record.get("university"),
        "url": record.get("url"),
        "title": document.get("title"),
        "publication_date": document.get("publication_date"),
        "profile_id": record.get("profile_id"),
        "provider": record.get("provider"),
        "model": record.get("model"),
        "schema_version": record.get("schema_version"),
        "run_id": record.get("run_id"),
        "enriched_at": record.get("enriched_at"),
    }
    for section, value in analysis.items():
        if isinstance(value, dict):
            for field, field_value in value.items():
                row[f"{section}.{field}"] = field_value
        else:
            # Lists of objects (entities) stay as JSON text for portability.
            row[section] = json.dumps(value, ensure_ascii=False)
    return row


def export_enrichments(
    records: Iterable[dict[str, Any]],
    *,
    profiles: list[dict[str, Any]],
    directory: Path,
) -> dict[str, Any]:
    """Write JSONL, a flat Parquet table, and a manifest with checksums."""
    directory.mkdir(parents=True, exist_ok=True)
    annotations_path = directory / ANNOTATIONS_FILENAME
    digest = hashlib.sha256()
    rows: list[dict[str, Any]] = []
    fd, temporary_name = tempfile.mkstemp(prefix=f".{ANNOTATIONS_FILENAME}.", dir=directory)
    try:
        with os.fdopen(fd, "wb") as temporary_file:
            for record in records:
                line = json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"
                temporary_file.write(line)
                digest.update(line)
                rows.append(_flat_row(record))
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, annotations_path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise

    table_path = directory / TABLE_FILENAME
    temporary_table = directory / f".{TABLE_FILENAME}.tmp"
    pd.DataFrame(rows).to_parquet(temporary_table, index=False)
    os.replace(temporary_table, table_path)

    manifest = {
        "exported_at": datetime.now(UTC).isoformat(),
        "profiles": profiles,
        "annotations": len(rows),
        "files": {
            ANNOTATIONS_FILENAME: {"sha256": digest.hexdigest()},
            TABLE_FILENAME: {
                "sha256": hashlib.sha256(table_path.read_bytes()).hexdigest()
            },
        },
    }
    atomic_write_json(directory / MANIFEST_FILENAME, manifest)
    return {"directory": str(directory), "annotations": len(rows)}
