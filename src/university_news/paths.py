from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data"


def sites_directory(data_root: Path) -> Path:
    return data_root / "sites"


def compiled_directory(data_root: Path) -> Path:
    return data_root / "compiled"


def reports_directory(data_root: Path) -> Path:
    return data_root / "reports"


def indexes_directory(data_root: Path) -> Path:
    return data_root / "indexes"


def logs_directory(data_root: Path) -> Path:
    return data_root / "logs"


def clipping_enrichment_directory(data_root: Path, schema_version: str) -> Path:
    normalized_version = schema_version.removeprefix("v")
    return data_root / "enriched" / "clipping" / f"v{normalized_version}"


def compiled_articles_path(data_root: Path) -> Path:
    return compiled_directory(data_root) / "articles.json"


def compiled_dashboard_articles_path(data_root: Path) -> Path:
    return compiled_directory(data_root) / "dashboard_articles.parquet"


def compiled_manifest_path(data_root: Path) -> Path:
    return compiled_directory(data_root) / "manifest.json"
