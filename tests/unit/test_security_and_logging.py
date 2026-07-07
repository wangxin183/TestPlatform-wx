from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient


def test_request_body_redaction_json():
    from src.utils.middleware import _safe_body_for_log

    body = json.dumps(
        {
            "username": "u",
            "password": "p",
            "token": "t",
            "nested": {"api_key": "k", "ok": True},
        },
        ensure_ascii=False,
    ).encode("utf-8")

    s = _safe_body_for_log(body, "application/json")
    assert '"password": "***"' in s
    assert '"token": "***"' in s
    assert '"api_key": "***"' in s


def test_write_auth_middleware_blocks_without_key(monkeypatch):
    from src.core.config import settings
    from src.main import create_app

    settings.database.auto_create_tables = False
    settings.security.api_key_auth_enabled = True
    settings.security.api_key = "secret"

    app = create_app()
    client = TestClient(app)

    r = client.post("/api/v1/pipelines", json={})
    assert r.status_code == 401
    assert r.json()["success"] is False


def test_write_auth_middleware_allows_with_key(monkeypatch):
    from src.core.config import settings
    from src.main import create_app

    settings.database.auto_create_tables = False
    settings.security.api_key_auth_enabled = True
    settings.security.api_key = "secret"

    app = create_app()
    client = TestClient(app)

    # Any write endpoint should pass middleware; handler may still reject.
    r = client.post("/api/v1/pipelines", json={}, headers={"X-API-Key": "secret"})
    assert r.status_code != 401

