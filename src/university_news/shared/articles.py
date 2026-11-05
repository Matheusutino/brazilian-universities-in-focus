"""Presentation of persisted annotations without a second data store."""

from typing import Any


def flatten_annotation(record: dict[str, Any]) -> dict[str, Any]:
    """Expose dotted analysis fields while keeping entities as native objects."""
    result = {key: value for key, value in record.items() if key != "analysis"}
    for section, value in (record.get("analysis") or {}).items():
        if isinstance(value, dict):
            result.update({f"{section}.{key}": item for key, item in value.items()})
        else:
            result[section] = value
    return result
