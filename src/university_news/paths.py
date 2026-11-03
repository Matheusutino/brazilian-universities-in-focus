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


def enrichment_export_directory(data_root: Path, profile_ids: list[str]) -> Path:
    """Explicitly requested profiles: one gets its own directory."""
    if len(profile_ids) == 1:
        return data_root / "enriched" / "profiles" / profile_ids[0]
    return combined_enrichments_directory(data_root)


def combined_enrichments_directory(data_root: Path) -> Path:
    """Default export of every configured provider; read by the website."""
    return data_root / "enriched" / "combined"


def compiled_articles_path(data_root: Path) -> Path:
    return compiled_directory(data_root) / "articles.json"


def compiled_site_articles_path(data_root: Path) -> Path:
    return compiled_directory(data_root) / "site_articles.parquet"


def compiled_manifest_path(data_root: Path) -> Path:
    return compiled_directory(data_root) / "manifest.json"
