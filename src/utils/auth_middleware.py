"""ASGI middleware for protecting write API endpoints with API key."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.core.config import settings
from src.utils.auth import _extract_token


class WriteAuthMiddleware:
    """Protect write endpoints under /api/v1/*.

    This is intentionally coarse-grained to quickly establish a security baseline
    without touching every route handler.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if settings.security.api_key_auth_enabled is False:
            await self.app(scope, receive, send)
            return

        method = (scope.get("method") or "").upper()
        if method in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        if not path.startswith("/api/v1/"):
            await self.app(scope, receive, send)
            return

        expected = settings.security.api_key
        if not expected:
            resp = JSONResponse(
                {"success": False, "data": None, "error": "API Key 未配置"},
                status_code=500,
            )
            await resp(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        auth = headers.get(b"authorization", b"").decode("utf-8", "latin-1")
        x_api_key = headers.get(b"x-api-key", b"").decode("utf-8", "latin-1")

        token = _extract_token(auth) or (x_api_key.strip() if x_api_key else None)
        if not token or token != expected:
            resp = JSONResponse(
                {"success": False, "data": None, "error": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await resp(scope, receive, send)
            return

        await self.app(scope, receive, send)

