from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..paths import DEFAULT_DATA_ROOT, indexes_directory
from ..settings import config_section
from ..operations.identity import stable_article_id
from ..sources.catalog import region_for_site
from .embeddings import (
    QwenGGUFEmbedder,
    QUERY_INSTRUCTION,
)

if TYPE_CHECKING:
    import pyarrow as pa


DEFAULT_DATABASE = indexes_directory(DEFAULT_DATA_ROOT) / "lancedb"
DEFAULT_TABLE = "university_articles"
INDEX_METADATA = "index_metadata.json"
ANN_MIN_ROWS = 256


def article_id(article: dict[str, Any]) -> str:
    url = article.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("Article has no URL")
    return str(stable_article_id(url))


def article_content(article: dict[str, Any]) -> str:
    fields = (
        ("Title", article.get("title")),
        ("Subtitle", article.get("subtitle")),
        ("Article", article.get("text")),
    )
    return "\n".join(
        f"{label}: {str(value).strip()}"
        for label, value in fields
        if isinstance(value, str) and value.strip()
    )


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def indexed_hash(article: dict[str, Any], content: str) -> str:
    journal = str(article.get("journal", "")).upper()
    indexed_value = "\0".join(
        (
            content,
            journal,
            region_for_site(journal),
            valid_date(article.get("date")) or "",
        )
    )
    return content_hash(indexed_value)


def valid_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return None
    return parsed.isoformat() if parsed.year >= 1990 else None


def lance_schema(dimension: int) -> pa.Schema:
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("article_id", pa.string(), nullable=False),
            pa.field("content_hash", pa.string(), nullable=False),
            pa.field("journal", pa.string(), nullable=False),
            pa.field("region", pa.string(), nullable=False),
            pa.field("date", pa.string()),
            pa.field("truncated", pa.bool_(), nullable=False),
            pa.field("vector", pa.list_(pa.float32(), dimension), nullable=False),
        ]
    )


def connect_database(path: Path):
    try:
        import lancedb
    except ImportError as exc:
        raise RuntimeError("lancedb is not installed; run `uv sync`") from exc
    path.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LANCE_INCLUDE_VECTOR_CENTROIDS", "false")
    # See committed writes from other workers/processes instead of keeping the
    # table version that happened to be current when the API started.
    return lancedb.connect(str(path), read_consistency_interval=timedelta(seconds=0))


def load_index_metadata(database_path: Path) -> dict[str, Any]:
    path = database_path / INDEX_METADATA
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def open_or_create_table(
    database: Any,
    table_name: str,
    dimension: int,
    *,
    recreate: bool,
):
    if recreate:
        return database.create_table(
            table_name,
            schema=lance_schema(dimension),
            mode="overwrite",
        )
    try:
        table = database.open_table(table_name)
    except (FileNotFoundError, KeyError, ValueError):
        return database.create_table(table_name, schema=lance_schema(dimension))
    vector_type = table.schema.field("vector").type
    existing_dimension = getattr(vector_type, "list_size", None)
    if existing_dimension != dimension:
        raise ValueError(
            f"The existing table uses {existing_dimension} dimensions; "
            "rebuild the index using the PostgreSQL embed worker"
        )
    return table


def index_records(table: Any, records: list[dict[str, Any]]) -> None:
    (
        table.merge_insert("article_id")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute(records)
    )


# Versions younger than this are kept, so a concurrent reader or writer never
# loses the files it is using.
DEFAULT_VERSION_RETENTION = timedelta(hours=1)


def compact_table(
    table: Any, *, keep_versions_for: timedelta = DEFAULT_VERSION_RETENTION
) -> None:
    """Merge small fragments, refresh indices, and delete old table versions.

    Every write creates a new version and a small data file. Without this,
    row-by-row indexing grows the table to many gigabytes and slows search.
    """
    table.optimize(cleanup_older_than=keep_versions_for)


def ensure_indexes(table: Any, *, rebuild: bool) -> None:
    from lancedb.index import Bitmap, BTree, HnswSq

    existing_columns = {
        column
        for index in table.list_indices()
        for column in index.columns
    }
    indexes = (
        ("vector", HnswSq(distance_type="cosine")),
        ("journal", Bitmap()),
        ("region", Bitmap()),
        ("date", BTree()),
    )
    for column, config in indexes:
        if column == "vector" and table.count_rows() < ANN_MIN_ROWS:
            continue  # Exact search is sufficient while a fresh corpus is tiny.
        if rebuild or column not in existing_columns:
            table.create_index(
                column,
                config=config,
                replace=True,
            )


def embedding_contract(embedder: QwenGGUFEmbedder, table: str) -> dict[str, Any]:
    return {
        "table": table, "model_repo": embedder.model_repo,
        "model_file": embedder.model_file, "context_length": embedder.context_length,
        "dimension": embedder.dimension, "distance": "cosine", "normalized": False,
        "model_sha256": getattr(embedder, "model_sha256", None),
        "query_instruction": QUERY_INSTRUCTION, "pooling": "last", "format_version": 1,
    }


def write_metadata(
    database_path: Path,
    *,
    table: str,
    embedder: QwenGGUFEmbedder,
) -> None:
    database_path.mkdir(parents=True, exist_ok=True)
    target = database_path / INDEX_METADATA
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {**embedding_contract(embedder, table), "model_path": embedder.model_path},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def build_parser() -> argparse.ArgumentParser:
    search = config_section("search")
    parser = argparse.ArgumentParser(
        prog="university-news-index", description="Compact the configured LanceDB index."
    )
    parser.add_argument("--optimize", action="store_true", required=True)
    parser.add_argument("--database", type=Path, default=Path(str(search.get("lancedb_path", DEFAULT_DATABASE))))
    parser.add_argument("--table", default=str(search.get("lancedb_table", DEFAULT_TABLE)))
    parser.add_argument("--keep-versions-hours", type=float, default=DEFAULT_VERSION_RETENTION.total_seconds() / 3600)
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        table = connect_database(args.database).open_table(args.table)
        rows = table.count_rows()
        compact_table(table, keep_versions_for=timedelta(hours=args.keep_versions_hours))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Optimization failed: {exc}")
        return 1
    print(json.dumps({"table": args.table, "rows": rows, "optimized": True}))
    return 0


def main() -> None:
    raise SystemExit(run())
