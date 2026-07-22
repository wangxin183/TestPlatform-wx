"""testcase.compile_advisor 单元测试。"""

from __future__ import annotations

import pytest

from src.agent_runtime.types import AgentRunResult
from src.services.testcase_compile_advisor import (
    advise_compile_case,
    advise_prepared_cases,
    fallback_compile_errors,
    needs_compile_advice,
)
from src.services.testcase_contract_compiler import prepare_executable_case
from execution_runtime.config import RuntimeConfig, TargetApp


def _cfg() -> RuntimeConfig:
    return RuntimeConfig(
        target_app=TargetApp(
            name="爱奇艺叭嗒",
            platform="android",
            bundle_id="com.iqiyi.acg",
        )
    )


def test_needs_compile_advice_only_for_problem_statuses() -> None:
    assert needs_compile_advice({"compile_status": "failed"})
    assert needs_compile_advice({"compile_status": "agent_required"})
    assert not needs_compile_advice({"compile_status": "ok"})


@pytest.mark.asyncio
async def test_advise_skipped_for_ok_status(monkeypatch) -> None:
    called = {"n": 0}

    async def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("should not call agent")

    monkeypatch.setattr(
        "src.services.testcase_compile_advisor.agent_runtime.run",
        _boom,
    )
    prepared = prepare_executable_case(
        {
            "case_id": "ok1",
            "title": "可见追更",
            "module": "漫画阅读器",
            "steps": [
                {
                    "step": 1,
                    "action": "确认右下角「追更」可见",
                    "expected": "阅读器右下角可见「追更」",
                }
            ],
        },
        _cfg(),
    )
    assert prepared["compile_status"] in {"ok", "agent_required"}
    if prepared["compile_status"] == "ok":
        out = await advise_compile_case(prepared, task_id="t")
        assert called["n"] == 0
        assert out["compile_status"] == "ok"


@pytest.mark.asyncio
async def test_advise_calls_agent_and_fills_fields(monkeypatch) -> None:
    async def _fake_run(task):
        assert task.role == "testcase.compile_advisor"
        return AgentRunResult(
            success=True,
            raw_output=(
                '[{"step":1,"code":"WEAK_ASSERTION",'
                '"reason":"expected 只有可见没有引号文案",'
                '"suggestion":"改成弹窗内可见「关闭」",'
                '"need":"补充固定关闭按钮文案"}]'
            ),
            backend="cursor",
        )

    monkeypatch.setattr(
        "src.services.testcase_compile_advisor.agent_runtime.run",
        _fake_run,
    )
    prepared = prepare_executable_case(
        {
            "case_id": "w1",
            "title": "弱断言",
            "module": "漫画阅读器",
            "steps": [
                {
                    "step": 1,
                    "action": "点击简介区域「展开」入口",
                    "expected": "弹出简介弹窗可见完整正文",
                }
            ],
        },
        _cfg(),
    )
    assert prepared["compile_status"] == "failed"
    out = await advise_compile_case(prepared, task_id="t1")
    err = out["compile_errors"][0]
    assert err["reason"] == "expected 只有可见没有引号文案"
    assert "关闭" in err["suggestion"]
    assert err["need"]


@pytest.mark.asyncio
async def test_advise_fallback_when_agent_fails(monkeypatch) -> None:
    async def _fail(_task):
        return AgentRunResult(success=False, error="down", backend="cursor")

    monkeypatch.setattr(
        "src.services.testcase_compile_advisor.agent_runtime.run",
        _fail,
    )
    prepared = {
        "compile_status": "failed",
        "compile_errors": [{"code": "WEAK_ASSERTION", "message": "弱断言", "step": 1}],
        "title": "x",
        "steps": [],
    }
    out = await advise_compile_case(prepared, task_id="t2")
    assert out["compile_errors"][0]["suggestion"]
    assert "暂不可用" in out["compile_errors"][0]["suggestion"]


@pytest.mark.asyncio
async def test_advise_prepared_cases_concurrency(monkeypatch) -> None:
    calls = {"n": 0}

    async def _fake_run(task):
        calls["n"] += 1
        return AgentRunResult(
            success=True,
            raw_output='[{"reason":"r","suggestion":"s","need":"n"}]',
            backend="cursor",
        )

    monkeypatch.setattr(
        "src.services.testcase_compile_advisor.agent_runtime.run",
        _fake_run,
    )
    items = [
        {"compile_status": "ok", "compile_errors": [], "title": "a", "steps": []},
        {
            "compile_status": "failed",
            "compile_errors": [{"code": "X", "message": "m"}],
            "title": "b",
            "steps": [],
        },
        {
            "compile_status": "agent_required",
            "compile_errors": [{"code": "Y", "message": "m2"}],
            "title": "c",
            "steps": [],
        },
    ]
    out = await advise_prepared_cases(items, task_id="batch", max_concurrency=2)
    assert calls["n"] == 2
    assert out[0]["compile_status"] == "ok"
    assert out[1]["compile_errors"][0]["suggestion"] == "s"
    assert out[2]["compile_errors"][0]["need"] == "n"


def test_fallback_compile_errors_minimal() -> None:
    rows = fallback_compile_errors([{"code": "X", "message": "规则失败"}])
    assert rows[0]["reason"] == "规则失败"
    assert rows[0]["suggestion"]
    assert rows[0]["need"]
