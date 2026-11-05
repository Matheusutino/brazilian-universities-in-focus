"""Versioned, non-sensitive application configuration."""

from __future__ import annotations

import argparse
import json
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    candidates = (Path.cwd(), Path(__file__).resolve().parents[2])
    return next(
        (candidate for candidate in candidates if (candidate / "config").is_dir()),
        candidates[-1],
    )


PROJECT_ROOT = _project_root()
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.toml"


@lru_cache(maxsize=4)
def load_settings(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    try:
        with path.open("rb") as source:
            value = tomllib.load(source)
    except OSError as exc:
        raise RuntimeError(f"Unable to read application config: {path}") from exc
    if not isinstance(value, dict):  # pragma: no cover - tomllib always returns dict
        raise TypeError(f"Application config must be a TOML table: {path}")
    return value


def config_section(name: str, *, path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    value = load_settings(path).get(name)
    if not isinstance(value, dict):
        raise TypeError(f"Missing [{name}] section in {path}")
    return value


def config_value(
    section: str,
    key: str,
    *,
    default: Any = None,
    path: Path = DEFAULT_CONFIG_PATH,
) -> Any:
    return config_section(section, path=path).get(key, default)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="university-news-config",
        description="Read a value from config/settings.toml.",
    )
    parser.add_argument("section")
    parser.add_argument("key")
    args = parser.parse_args()
    value = config_value(args.section, args.key)
    if value is None:
        parser.error(f"Unknown setting: {args.section}.{args.key}")
    print(json.dumps(value) if isinstance(value, (dict, list, bool)) else value)
