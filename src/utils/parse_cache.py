"""Deterministic parse cache — SHA256 hash of raw_text → parsed output.

Eliminates redundant LLM calls for identical documents.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.utils.file_storage import exists, read, save
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

CACHE_DIR = "storage/parse_cache"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_path(sha: str) -> str:
    return f"{CACHE_DIR}/{sha[:2]}/{sha}.json"


async def get(text: str) -> dict | None:
    """Return cached parsed result for given raw_text, or None if miss."""
    sha = _hash(text)
    path = _cache_path(sha)
    if not await exists(path):
        return None
    content = await read(path)
    if content is None:
        return None
    try:
        data = json.loads(content.decode("utf-8"))
        logger.info("parse_cache_hit", sha=sha[:12])
        return data
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("parse_cache_corrupt", sha=sha[:12])
        return None


async def set(text: str, data: dict) -> None:
    """Store parsed result in cache."""
    sha = _hash(text)
    path = _cache_path(sha)
    content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    await save(path, content)
    logger.info("parse_cache_set", sha=sha[:12], size=len(content))
