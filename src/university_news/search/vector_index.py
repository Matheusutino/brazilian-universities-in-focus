from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tqdm import tqdm

from ..paths import DEFAULT_DATA_ROOT, compiled_articles_path, indexes_directory
from ..settings import config_section
from ..operations.identity import stable_article_id
from ..sources.catalog import region_for_site
from .embeddings import (
    DEFAULT_CONTEXT_LENGTH,
    DEFAULT_MODEL_FILE,
    DEFAULT_MODEL_REPO,
    QwenGGUFEmbedder,
    QUERY_INSTRUCTION,
)

if TYPE_CHECKING:
    import pyarrow as pa


DEFAULT_DATABASE = indexes_directory(DEFAULT_DATA_ROOT) / "lancedb"
DEFAULT_TABLE = "university_articles"
INDEX_METADATA = "index_metadata.json"
ANN_MIN_ROWS = 256


@dataclass(frozen=True, slots=True)
class IndexStats:
    table: str
    indexed: int
    skipped: int
    removed: int
    duplicate_urls_removed: int
    truncated: int
    source: str
    database: str


def load_articles(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Invalid consolidated JSON at {path}") from exc
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("The consolidated JSON must contain a list of objects")
    return value


def deduplicate_articles(
    articles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    unique: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    duplicates = 0
    for article in articles:
        url = article.get("url")
        if not isinstance(url, str) or not (url_key := url.strip()):
            unique.append(article)
            continue
        if url_key in seen_urls:
            duplicates += 1
            continue
        seen_urls.add(url_key)
        unique.append(article)
    return unique, duplicates


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
            "run again with --recreate"
        )
    return table


def existing_hashes(table: Any) -> dict[str, str]:
    count = table.count_rows()
    if count == 0:
        return {}
    rows = (
        table.search()
        .select(["article_id", "content_hash"])
        .limit(count)
        .to_arrow()
    )
    return dict(
        zip(
            rows.column("article_id").to_pylist(),
            rows.column("content_hash").to_pylist(),
        )
    )


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


def index_articles(
    source: Path,
    *,
    database_path: Path = DEFAULT_DATABASE,
    table_name: str = DEFAULT_TABLE,
    batch_size: int = 8,
    recreate: bool = False,
    embedder: QwenGGUFEmbedder | None = None,
    database: Any | None = None,
) -> IndexStats:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    eligible_articles = [
        article
        for article in load_articles(source)
        if article.get("url") and article_content(article)
    ]
    articles, duplicate_urls_removed = deduplicate_articles(eligible_articles)
    embedder = embedder or QwenGGUFEmbedder()
    database = database or connect_database(database_path)
    table = open_or_create_table(
        database,
        table_name,
        embedder.dimension,
        recreate=recreate,
    )
    metadata = load_index_metadata(database_path)
    expected_model = embedding_contract(embedder, table_name)
    if not recreate and table.count_rows():
        if not metadata:
            raise ValueError(
                "The existing table has no model metadata; "
                "run again with --recreate"
            )
        mismatched = [
            key for key, value in expected_model.items() if metadata.get(key) != value
        ]
        if mismatched:
            raise ValueError(
                "The model does not match the existing index "
                f"({', '.join(mismatched)}); run again with --recreate"
            )
    # Writing this before the first expensive embedding batch makes an
    # interrupted first index resumable on the next invocation.
    write_metadata(database_path, table=table_name, embedder=embedder)
    known = {} if recreate else existing_hashes(table)
    pending: list[tuple[dict[str, Any], str, str]] = []
    desired_ids: set[str] = set()
    skipped = 0
    for article in articles:
        identifier = article_id(article)
        desired_ids.add(identifier)
        content = article_content(article)
        digest = indexed_hash(article, content)
        if known.get(identifier) == digest:
            skipped += 1
        else:
            pending.append((article, content, digest))

    truncated_total = 0
    for start in tqdm(
        range(0, len(pending), batch_size),
        desc="Gerando embeddings",
        unit="lote",
    ):
        batch = pending[start : start + batch_size]
        embedded = embedder.encode_documents([content for _, content, _ in batch])
        records = []
        for (article, _, digest), vector, truncated in zip(
            batch,
            embedded.vectors,
            embedded.truncated,
        ):
            journal = str(article.get("journal", "")).upper()
            records.append(
                {
                    "article_id": article_id(article),
                    "content_hash": digest,
                    "journal": journal,
                    "region": region_for_site(journal),
                    "date": valid_date(article.get("date")),
                    "truncated": truncated,
                    "vector": vector,
                }
            )
            truncated_total += int(truncated)
        if records:
            index_records(table, records)

    stale_ids = sorted(set(known).difference(desired_ids))
    for start in range(0, len(stale_ids), 500):
        batch = stale_ids[start : start + 500]
        values = ", ".join(f"'{identifier}'" for identifier in batch)
        table.delete(f"article_id IN ({values})")

    ensure_indexes(table, rebuild=recreate)
    compact_table(table)
    write_metadata(database_path, table=table_name, embedder=embedder)
    return IndexStats(
        table=table_name,
        indexed=len(pending),
        skipped=skipped,
        removed=len(stale_ids),
        duplicate_urls_removed=duplicate_urls_removed,
        truncated=truncated_total,
        source=str(source),
        database=str(database_path),
    )


def build_parser() -> argparse.ArgumentParser:
    search = config_section("search")
    embedding = config_section("embedding")
    configured_model_path = str(embedding.get("model_path", "")).strip()
    parser = argparse.ArgumentParser(
        prog="university-news-index",
        description="Index compiled articles in LanceDB with Qwen3 Embedding GGUF.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=compiled_articles_path(DEFAULT_DATA_ROOT),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(str(search.get("lancedb_path", DEFAULT_DATABASE))),
    )
    parser.add_argument(
        "--table",
        default=str(search.get("lancedb_table", DEFAULT_TABLE)),
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        default=Path(configured_model_path) if configured_model_path else None,
    )
    parser.add_argument(
        "--model-repo",
        default=str(embedding.get("model_repo", DEFAULT_MODEL_REPO)),
    )
    parser.add_argument(
        "--model-file",
        default=str(embedding.get("model_file", DEFAULT_MODEL_FILE)),
    )
    parser.add_argument(
        "--context-length",
        type=int,
        default=int(embedding.get("context_length", DEFAULT_CONTEXT_LENGTH)),
    )
    parser.add_argument("--threads", type=int, default=int(embedding.get("threads", 8)))
    parser.add_argument(
        "--gpu-layers",
        type=int,
        default=int(embedding.get("gpu_layers", -1)),
        help="-1 offloads every layer to the GPU; 0 forces CPU",
    )
    parser.add_argument(
        "--batch-size", type=int, default=int(embedding.get("batch_size", 8))
    )
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument(
        "--optimize",
        action="store_true",
        help="Only compact the table and delete old versions (no model is loaded)",
    )
    parser.add_argument(
        "--keep-versions-hours",
        type=float,
        default=DEFAULT_VERSION_RETENTION.total_seconds() / 3600,
        help="With --optimize: keep versions newer than this (0 when no writer runs)",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.optimize:
        try:
            table = connect_database(args.database).open_table(args.table)
            rows = table.count_rows()
            compact_table(table, keep_versions_for=timedelta(hours=args.keep_versions_hours))
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Optimization failed: {exc}")
            return 1
        print(json.dumps({"table": args.table, "rows": rows, "optimized": True}))
        return 0
    try:
        embedder = QwenGGUFEmbedder(
            model_path=args.model_path,
            model_repo=args.model_repo,
            model_file=args.model_file,
            context_length=args.context_length,
            threads=args.threads,
            gpu_layers=args.gpu_layers,
        )
        stats = index_articles(
            args.source,
            database_path=args.database,
            table_name=args.table,
            batch_size=args.batch_size,
            recreate=args.recreate,
            embedder=embedder,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Indexing failed: {exc}")
        return 1
    print(json.dumps(asdict(stats), ensure_ascii=False))
    return 0


def main() -> None:
    raise SystemExit(run())
