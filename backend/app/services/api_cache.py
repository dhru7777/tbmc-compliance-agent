"""Disk-backed cache for LLM / search responses — avoids repeat token spend for identical inputs."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).resolve().parents[2] / "records" / "api_cache"
CACHE_ENABLED = os.getenv("API_CACHE_ENABLED", "true").lower() in ("1", "true", "yes")
_ttl_hours = os.getenv("API_CACHE_TTL_HOURS", "168")
CACHE_TTL_SECONDS = int(_ttl_hours) * 3600 if _ttl_hours.strip() else 0

_memory: dict[str, tuple[float, Any]] = {}


def cache_key(namespace: str, *parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{namespace}_{digest}"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _expired(saved_at: float, now: float) -> bool:
    return bool(CACHE_TTL_SECONDS) and now - saved_at > CACHE_TTL_SECONDS


def get(namespace: str, *parts: str) -> Any | None:
    if not CACHE_ENABLED:
        return None

    key = cache_key(namespace, *parts)
    now = time.time()

    if key in _memory:
        saved_at, value = _memory[key]
        if not _expired(saved_at, now):
            return value
        del _memory[key]

    path = _path(key)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        saved_at = float(payload.get("_cached_at", 0))
        if _expired(saved_at, now):
            path.unlink(missing_ok=True)
            return None
        value = payload.get("data")
        _memory[key] = (saved_at, value)
        return value
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def set(namespace: str, value: Any, *parts: str) -> None:
    if not CACHE_ENABLED:
        return

    key = cache_key(namespace, *parts)
    now = time.time()
    _memory[key] = (now, value)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _path(key).write_text(
        json.dumps({"_cached_at": now, "namespace": namespace, "data": value}, indent=2),
        encoding="utf-8",
    )


def mark_cached(value: dict[str, Any]) -> dict[str, Any]:
    out = dict(value)
    out["from_cache"] = True
    return out


def clear_all() -> int:
    """Delete all disk and in-memory cache entries. Returns files removed."""
    _memory.clear()
    if not CACHE_DIR.exists():
        return 0
    removed = 0
    for path in CACHE_DIR.glob("*.json"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed
