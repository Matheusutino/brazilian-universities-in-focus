"""Stable identity rules for articles entering the incremental pipeline."""

import hashlib
import json
import uuid
from typing import Any
from urllib.parse import urldefrag, urlsplit, urlunsplit


def canonicalize_url(value: str) -> str:
    candidate, _fragment = urldefrag(value.strip())
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"Invalid article URL: {value}")
    path = parts.path or "/"
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
    )


def stable_article_id(url: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, canonicalize_url(url))


def payload_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode()).hexdigest()
