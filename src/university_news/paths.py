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


def compiled_articles_path(data_root: Path) -> Path:
    return compiled_directory(data_root) / "articles.json"


def compiled_manifest_path(data_root: Path) -> Path:
    return compiled_directory(data_root) / "manifest.json"
