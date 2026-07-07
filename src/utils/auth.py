"""Simple API-key based auth for write endpoints.

This project currently has no user system. For a fast security baseline,
we protect write endpoints (POST/PUT/PATCH/DELETE) with a single API key.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from src.core.config import settings


def _extract_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return authorization.strip() or None


async def require_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Dependency for protecting write endpoints.

    - Disabled by default in debug mode, unless explicitly enabled.
    - When enabled, accepts either:
      - Header: Authorization: Bearer <key>
      - Header: X-API-Key: <key>
    """
    if settings.security.api_key_auth_enabled is False:
        return

    expected = settings.security.api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API Key auth is enabled but no api_key is configured",
        )

    token = _extract_token(authorization) or (x_api_key.strip() if x_api_key else None)
    if not token or token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )

