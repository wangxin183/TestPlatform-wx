"""AgentRuntime 路由与 fallback 顺序单元测试。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pytest

from src.agent_runtime.runtime import AgentRuntime
from src.agent_runtime.types import AgentBackend, AgentRunResult, AgentTask


class FakeBackend(AgentBackend):
    """可编程 fake backend，用于精确断言路由行为。"""

    def __init__(
        self,
        name: str,
        config: dict[str, Any],
        available: bool = True,
        outcome: str = "success",
        raw_output: str = "",
        error: str = "",
    ) -> None:
        super().__init__(name, config)
        self._available = available
        self._outcome = outcome
        self._raw_output = raw_output or f"[{name}] ok"
        self._error = error or f"[{name}] fail"
        self.calls: list[AgentTask] = []

    def is_available(self) -> bool:
        return self._available

    async def run(self, task: AgentTask) -> AgentRunResult:
        self.calls.append(task)
        if self._outcome == "success":
            return AgentRunResult(success=True, raw_output=self._raw_output)
        return AgentRunResult(success=False, error=self._error)


def _build_runtime(
    backends: dict[str, AgentBackend],
    roles: dict[str, dict[str, Any]],
    strict: bool = False,
) -> AgentRuntime:
    return AgentRuntime(backends=backends, roles=roles, strict_startup_check=strict)


@pytest.mark.asyncio
async def test_primary_success_no_fallback():
    """primary 成功时不应尝试任何 fallback。"""
    a = FakeBackend("a", {}, outcome="success")
    b = FakeBackend("b", {}, outcome="success")
    runtime = _build_runtime(
        backends={"a": a, "b": b},
        roles={"analysis.role": {"primary": "a", "fallbacks": ["b"]}},
    )

    result = await runtime.run(AgentTask(role="analysis.role", prompt="hi"))

    assert result.success is True
    assert result.backend == "a"
    assert result.attempt_index == 0
    assert result.fallback_from is None
    assert len(a.calls) == 1
    assert len(b.calls) == 0


@pytest.mark.asyncio
async def test_primary_fail_falls_back_in_order():
    """primary 失败 → 按 fallbacks 顺序依次尝试。"""
    a = FakeBackend("a", {}, outcome="fail")
    b = FakeBackend("b", {}, outcome="fail")
    c = FakeBackend("c", {}, outcome="success")
    runtime = _build_runtime(
        backends={"a": a, "b": b, "c": c},
        roles={"analysis.role": {"primary": "a", "fallbacks": ["b", "c"]}},
    )

    result = await runtime.run(AgentTask(role="analysis.role", prompt="hi"))

    assert result.success is True
    assert result.backend == "c"
    assert result.attempt_index == 2
    assert result.fallback_from == "b"
    assert len(a.calls) == 1 and len(b.calls) == 1 and len(c.calls) == 1


@pytest.mark.asyncio
async def test_unavailable_backend_is_skipped():
    """标记 unavailable 的 backend 不应被调用。"""
    a = FakeBackend("a", {}, available=False, outcome="success")
    b = FakeBackend("b", {}, outcome="success")
    runtime = _build_runtime(
        backends={"a": a, "b": b},
        roles={"analysis.role": {"primary": "a", "fallbacks": ["b"]}},
    )

    result = await runtime.run(AgentTask(role="analysis.role", prompt="hi"))

    assert result.success is True
    assert result.backend == "b"
    assert len(a.calls) == 0
    assert len(b.calls) == 1


@pytest.mark.asyncio
async def test_all_backends_fail_returns_aggregated_error():
    """全链失败时聚合错误消息，指向最后一个 backend。"""
    a = FakeBackend("a", {}, outcome="fail", error="boom_a")
    b = FakeBackend("b", {}, outcome="fail", error="boom_b")
    runtime = _build_runtime(
        backends={"a": a, "b": b},
        roles={"analysis.role": {"primary": "a", "fallbacks": ["b"]}},
    )

    result = await runtime.run(AgentTask(role="analysis.role", prompt="hi"))

    assert result.success is False
    assert result.backend == "b"
    assert "boom_a" in result.error and "boom_b" in result.error
    assert result.fallback_from == "a"


@pytest.mark.asyncio
async def test_force_fallback_skips_primary():
    """force_fallback=True 时跳过 primary，从 fallbacks[0] 开始。"""
    a = FakeBackend("a", {}, outcome="success")
    b = FakeBackend("b", {}, outcome="success")
    runtime = _build_runtime(
        backends={"a": a, "b": b},
        roles={"analysis.role": {"primary": "a", "fallbacks": ["b"]}},
    )

    result = await runtime.run(AgentTask(role="analysis.role", prompt="hi", force_fallback=True))

    assert result.success is True
    assert result.backend == "b"
    assert len(a.calls) == 0
    assert len(b.calls) == 1


@pytest.mark.asyncio
async def test_unknown_role_returns_clear_error():
    """未配置的 role 应立即失败并给出明确错误。"""
    runtime = _build_runtime(backends={}, roles={})

    result = await runtime.run(AgentTask(role="mystery.role", prompt="hi"))

    assert result.success is False
    assert "未配置 role" in result.error


def test_strict_startup_check_fails_when_no_available_backend():
    """strict=True 且 role 全链 unavailable → 启动直接抛错。"""
    a = FakeBackend("a", {}, available=False)
    with pytest.raises(RuntimeError, match="无任何可用 backend"):
        AgentRuntime(
            backends={"a": a},
            roles={"analysis.role": {"primary": "a", "fallbacks": []}},
            strict_startup_check=True,
        )


def test_get_role_chain_respects_force_fallback():
    a = FakeBackend("a", {})
    b = FakeBackend("b", {})
    c = FakeBackend("c", {})
    runtime = _build_runtime(
        backends={"a": a, "b": b, "c": c},
        roles={"analysis.role": {"primary": "a", "fallbacks": ["b", "c"]}},
    )

    assert runtime.get_role_chain("analysis.role") == ["a", "b", "c"]
    assert runtime.get_role_chain("analysis.role", force_fallback=True) == ["b", "c"]
    assert runtime.get_role_chain("nope") == []
