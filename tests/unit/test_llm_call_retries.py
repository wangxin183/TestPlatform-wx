from __future__ import annotations

import pytest

from src.llm.types import LLMRequest, LLMResponse, TokenUsage


@pytest.mark.asyncio
async def test_llm_call_uses_settings_default_retries(monkeypatch):
    """llm_call(max_retries=None) should use settings.llm.max_retries."""
    from src.core.config import settings
    from src.llm import caller as caller_mod

    # Force deterministic retry count
    settings.llm.max_retries = 2

    class DummyProvider:
        async def complete(self, request: LLMRequest) -> LLMResponse:
            raise RuntimeError("boom")

    async def dummy_route(request: LLMRequest):
        return DummyProvider(), "dummy-model"

    monkeypatch.setattr(caller_mod.llm_router, "route", dummy_route)

    with pytest.raises(RuntimeError) as exc:
        await caller_mod.llm_call(LLMRequest(system_prompt="s", user_prompt="u"), max_retries=None)

    # 2 retries => total attempts = 3
    assert "已重试2次" in str(exc.value)


@pytest.mark.asyncio
async def test_llm_call_retries_on_parse_failure(monkeypatch):
    from src.core.config import settings
    from src.llm import caller as caller_mod

    settings.llm.max_retries = 1
    calls = {"n": 0}

    class DummyProvider:
        async def complete(self, request: LLMRequest) -> LLMResponse:
            calls["n"] += 1
            if calls["n"] == 1:
                return LLMResponse(
                    content="not-json",
                    usage=TokenUsage(),
                    model="dummy",
                    latency_ms=1,
                    parsed_json=None,
                )
            return LLMResponse(
                content='{"ok": true}',
                usage=TokenUsage(total_tokens=10),
                model="dummy",
                latency_ms=1,
                parsed_json={"ok": True},
            )

    async def dummy_route(request: LLMRequest):
        return DummyProvider(), "dummy-model"

    monkeypatch.setattr(caller_mod.llm_router, "route", dummy_route)

    resp = await caller_mod.llm_call(
        LLMRequest(system_prompt="s", user_prompt="u", expect_json=True),
        max_retries=None,
    )
    assert resp.parsed_json == {"ok": True}
    assert calls["n"] == 2

