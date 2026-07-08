"""集成测试：SelfHealingOrchestrator 与 AgentRuntime 协作。

重点覆盖：
1. 基础设施故障 → runtime 重试成功 → 自愈返回 success
2. 全部 backend 失败 → force_fallback → 全失败 → 自愈返回 exhausted
3. 输出故障 → 通过 utility.diagnoser role 完成诊断
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import pytest

from src.agent_runtime.runtime import AgentRuntime
from src.agent_runtime.types import AgentBackend, AgentRunResult, AgentTask
from src.services.self_healing import (
    FailureCategory,
    FailureInfo,
    HealingContext,
    SelfHealingOrchestrator,
)


class ProgrammableBackend(AgentBackend):
    """按调用次数依次返回预置结果的 fake backend。"""

    def __init__(self, name: str, results: list[AgentRunResult]) -> None:
        super().__init__(name, {})
        self._results = results
        self.calls: list[AgentTask] = []

    def is_available(self) -> bool:
        return True

    async def run(self, task: AgentTask) -> AgentRunResult:
        self.calls.append(task)
        if not self._results:
            return AgentRunResult(success=False, error=f"{self.name}: no more preset results")
        return self._results.pop(0)


class NoopFeishu:
    async def notify_text(self, *_a, **_kw):
        pass

    async def notify_failed(self, *_a, **_kw):
        pass


class MemAlog:
    def __init__(self):
        self.entries: list[dict[str, Any]] = []
        self.snapshots: dict[str, str] = {}
        self.dir_path = "/tmp/mem-alog"

    def log(self, step: str, **fields):
        self.entries.append({"step": step, **fields})

    def save_snapshot(self, name: str, content: str) -> None:
        self.snapshots[name] = content

    def save_json(self, *_a, **_kw):
        pass


@pytest.mark.asyncio
async def test_infra_retry_succeeds_via_primary():
    """primary 第一次 timeout，重试成功 → 自愈返回 success (infra_retry)。"""
    primary = ProgrammableBackend(
        "claude_code",
        [AgentRunResult(success=True, raw_output="RECOVERED")],
    )
    runtime = AgentRuntime(
        backends={"claude_code": primary},
        roles={"requirement.analyzer": {"primary": "claude_code", "fallbacks": []}},
        strict_startup_check=False,
    )
    healer = SelfHealingOrchestrator(runtime=runtime, feishu=NoopFeishu())

    failure = FailureInfo(
        category=FailureCategory.INFRA_TIMEOUT,
        step_name="requirement.analyzer",
        agent_tool="claude_code",
        error_message="命令执行超时",
        prompt="hello",
    )
    ctx = HealingContext(analysis_id="TA-1", role="requirement.analyzer")
    alog = MemAlog()

    result = await healer.handle(failure, ctx, alog)

    assert result.success is True
    assert result.resolve_method == "infra_retry"
    assert result.raw_output == "RECOVERED"
    assert len(primary.calls) == 1
    assert any(e["step"] == "self_heal_complete" for e in alog.entries)


@pytest.mark.asyncio
async def test_infra_retry_falls_back_to_secondary_backend():
    """primary 一直失败 → force_fallback → fallback 成功。"""
    primary = ProgrammableBackend(
        "claude_code",
        [AgentRunResult(success=False, error="still broken")],
    )
    fallback = ProgrammableBackend(
        "codex",
        [AgentRunResult(success=True, raw_output="FALLBACK_OK")],
    )
    runtime = AgentRuntime(
        backends={"claude_code": primary, "codex": fallback},
        roles={"requirement.analyzer": {"primary": "claude_code", "fallbacks": ["codex"]}},
        strict_startup_check=False,
    )
    healer = SelfHealingOrchestrator(runtime=runtime, feishu=NoopFeishu())

    failure = FailureInfo(
        category=FailureCategory.INFRA_CLI_ERROR,
        step_name="requirement.analyzer",
        agent_tool="claude_code",
        error_message="exit code 1",
        prompt="hello",
    )
    ctx = HealingContext(analysis_id="TA-2", role="requirement.analyzer")
    alog = MemAlog()

    result = await healer.handle(failure, ctx, alog)

    assert result.success is True
    assert result.resolve_method == "backend_switch"
    assert result.raw_output == "FALLBACK_OK"
    assert len(primary.calls) == 1  # only infra_retry attempt
    assert len(fallback.calls) == 1  # force_fallback attempt


@pytest.mark.asyncio
async def test_output_diagnosis_uses_diagnoser_role():
    """输出故障 → utility.diagnoser 诊断并返回 corrected_output。"""
    corrected = {
        "meta": {},
        "functional_requirements": [{"id": "FR-001"}],
        "non_functional_requirements": [],
        "test_points": [{"id": "TP-001"}],
        "risks": [],
        "analysis_notes": {},
    }
    diagnosis_output = json.dumps({
        "diagnosis": {"root_cause": "格式错误", "failure_category": "json_escape"},
        "corrected_output": corrected,
    }, ensure_ascii=False)

    diagnoser = ProgrammableBackend(
        "codex",
        [AgentRunResult(success=True, raw_output=diagnosis_output)],
    )
    runtime = AgentRuntime(
        backends={"codex": diagnoser},
        roles={
            "requirement.analyzer": {"primary": "codex", "fallbacks": []},
            "utility.diagnoser": {"primary": "codex", "fallbacks": []},
        },
        strict_startup_check=False,
    )
    healer = SelfHealingOrchestrator(runtime=runtime, feishu=NoopFeishu())

    failure = FailureInfo(
        category=FailureCategory.OUTPUT_PARSE,
        step_name="requirement.analyzer",
        agent_tool="codex",
        error_message="JSON parse failed",
        raw_output="invalid",
    )
    ctx = HealingContext(
        analysis_id="TA-3",
        role="requirement.analyzer",
        diagnoser_role="utility.diagnoser",
        skill_body="skill body",
        doc_summary="doc summary",
    )
    alog = MemAlog()

    result = await healer.handle(failure, ctx, alog)

    assert result.success is True
    assert result.resolve_method == "self_diagnosis"
    assert result.output == corrected
    assert len(diagnoser.calls) == 1
    diag_call = diagnoser.calls[0]
    assert diag_call.role == "utility.diagnoser"
