"""Transparent encryption/decryption for sensitive JSON fields using Fernet."""

from __future__ import annotations

import json
import os
from typing import Optional

from cryptography.fernet import Fernet

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

_ENCRYPTION_KEY: Optional[str] = os.environ.get("ENCRYPTION_KEY")
_cipher: Optional[Fernet] = None


def _get_cipher() -> Fernet | None:
    global _cipher
    if _cipher is not None:
        return _cipher
    if not _ENCRYPTION_KEY:
        logger.warning("encryption_key_not_set")
        return None
    try:
        _cipher = Fernet(_ENCRYPTION_KEY.encode())
        return _cipher
    except Exception as e:
        logger.error("encryption_key_invalid", error=str(e))
        return None


def encrypt_dict(data: dict | None) -> dict | None:
    """Encrypt a JSON-serializable dict. Returns the input unchanged if encryption is unavailable."""
    if not data or not isinstance(data, dict):
        return data
    cipher = _get_cipher()
    if cipher is None:
        return data
    plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")
    token = cipher.encrypt(plaintext)
    return {"__encrypted__": True, "data": token.decode("utf-8")}


def decrypt_dict(data: dict | None) -> dict | None:
    """Decrypt a dict that was encrypted with encrypt_dict. Returns the input unchanged if not encrypted."""
    if not isinstance(data, dict):
        return data
    if not data.get("__encrypted__"):
        return data
    cipher = _get_cipher()
    if cipher is None:
        return data
    try:
        token = data["data"].encode("utf-8")
        plaintext = cipher.decrypt(token)
        return json.loads(plaintext.decode("utf-8"))
    except Exception as e:
        logger.error("decrypt_failed", error=str(e))
        return data
