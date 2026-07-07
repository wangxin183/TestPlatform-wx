"""FastAPI middleware — API request logging and diagnostics."""

import time
import json

from starlette.types import ASGIApp, Receive, Scope, Send, Message

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

MAX_BODY_LOG_LENGTH = 2000
SENSITIVE_KEYS = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "api_key",
    "apikey",
    "x_api_key",
}


def _redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in SENSITIVE_KEYS:
                out[k] = "***"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _safe_body_for_log(body_bytes: bytes, content_type: str) -> str:
    """Best-effort body logging with redaction.

    - For JSON: parse + redact sensitive fields
    - For others: decode + truncate
    """
    if not body_bytes:
        return ""
    if "application/json" in (content_type or ""):
        try:
            payload = json.loads(body_bytes.decode("utf-8", errors="replace"))
            payload = _redact(payload)
            s = json.dumps(payload, ensure_ascii=False)
        except Exception:
            s = body_bytes.decode("utf-8", errors="replace")
    else:
        s = body_bytes.decode("utf-8", errors="replace")

    if len(s) > MAX_BODY_LOG_LENGTH:
        return s[:MAX_BODY_LOG_LENGTH] + "..."
    return s


class RequestLoggingMiddleware:
    """Log every API request with method, path, body, status, and duration.

    Uses raw ASGI interface to safely capture request body without
    interfering with downstream route handlers.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        start = time.monotonic()
        method = scope.get("method", "")
        query = scope.get("query_string", b"").decode("utf-8", "latin-1")
        headers = dict(scope.get("headers", []))
        content_type = headers.get(b"content-type", b"").decode("utf-8", "latin-1")
        is_multipart = "multipart/form-data" in content_type

        # Capture request body for logging
        body_chunks: list[bytes] = []
        body_logged = b""
        request_body_consumed = False

        async def receive_wrapper() -> Message:
            nonlocal request_body_consumed, body_logged
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                if body:
                    body_chunks.append(body)
                if not message.get("more_body", False):
                    request_body_consumed = True
                    body_logged = b"".join(body_chunks)
            return message

        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception:
            status_code = 500
            raise
        finally:
            duration_ms = round((time.monotonic() - start) * 1000)

            body_str = ""
            # Only log body for write methods and non-multipart.
            # Body is redacted best-effort when JSON.
            if method in ("POST", "PUT", "PATCH", "DELETE") and not is_multipart:
                body_str = _safe_body_for_log(body_logged, content_type)
            elif is_multipart:
                cl = headers.get(b"content-length", b"?").decode()
                body_str = f"[multipart, size={cl}b]"

            client = scope.get("client", ("-", 0))
            client_ip = client[0] if client else "-"

            log_kwargs = {
                "method": method,
                "path": path,
                "status": status_code,
                "duration_ms": duration_ms,
                "client": client_ip,
            }
            if query:
                log_kwargs["query"] = query
            if body_str:
                log_kwargs["body"] = body_str

            if status_code >= 500:
                logger.error("api_request", **log_kwargs)
            elif status_code >= 400:
                logger.warning("api_request", **log_kwargs)
            else:
                logger.info("api_request", **log_kwargs)
