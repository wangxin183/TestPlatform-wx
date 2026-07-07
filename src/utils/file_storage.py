"""Async file storage utility using aiofiles."""

import os
from pathlib import Path
from typing import Optional

import aiofiles
import aiofiles.os as aio_os

from src.core.config import settings

STORAGE_ROOT = Path(settings.storage.root).resolve()
_STR_STORAGE_ROOT = str(STORAGE_ROOT)


def _resolve_safe(relative_path: str) -> Path:
    """Resolve a path under STORAGE_ROOT, rejecting traversal attempts."""
    full_path = (STORAGE_ROOT / relative_path).resolve()
    if not str(full_path).startswith(_STR_STORAGE_ROOT):
        raise ValueError(f"路径遍历被拒绝: {relative_path}")
    return full_path


async def save(relative_path: str, content: bytes) -> str:
    """Save content bytes to a file under storage/. Returns the relative path."""
    full_path = _resolve_safe(relative_path)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(str(full_path), "wb") as f:
        await f.write(content)
    return relative_path


async def read(relative_path: str) -> Optional[bytes]:
    """Read bytes from a file under storage/. Returns None if not found."""
    full_path = _resolve_safe(relative_path)
    if not full_path.exists():
        return None
    async with aiofiles.open(str(full_path), "rb") as f:
        return await f.read()


async def delete(relative_path: str) -> bool:
    """Delete a file under storage/. Returns True if deleted, False if not found."""
    full_path = _resolve_safe(relative_path)
    if not full_path.exists():
        return False
    await aio_os.remove(str(full_path))
    return True


async def exists(relative_path: str) -> bool:
    """Check if a file exists under storage/."""
    full_path = _resolve_safe(relative_path)
    return full_path.exists()
