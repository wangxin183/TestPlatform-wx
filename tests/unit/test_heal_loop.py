"""StageAgentHarness / HealLoop 单测。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.services.heal_loop import (
    FailureCategory,
    FailureInfo,
    HealBudget,
    HealLedger,
    HealPlan,
    HealScope,
    run_heal_loop,
)


@pytest.mark.asyncio
async def test_heal_loop_first_pass():
    async def attempt():
        return "ok"

    async def diagnose(_f):
        raise AssertionError("should not diagnose")

    async def apply(_p, _f):
        raise AssertionError("should not apply")

    result = await run_heal_loop(
        stage="execution",
        scope=HealScope.SETUP,
        attempt_fn=attempt,
        diagnose_fn=diagnose,
        apply_fn=apply,
        budget=2,
    )
    assert result.success
    assert result.resolve_method == "first_pass"
    assert result.output == "ok"


@pytest.mark.asyncio
async def test_heal_loop_recovers_after_plan(tmp_path: Path):
    calls = {"n": 0}

    async def attempt():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("卡在阅读器找不到入口")
        return "recovered"

    async def diagnose(failure: FailureInfo):
        assert failure.category == FailureCategory.BLOCKED_UI
        return HealPlan(
            action="recover_page",
            arguments={"max_backs": 3},
            rationale="back out",
        )

    applied = {"ok": False}

    async def apply(plan: HealPlan, _f: FailureInfo):
        assert plan.action == "recover_page"
        applied["ok"] = True

    ledger = HealLedger(tmp_path / "heal_ledger.jsonl")
    result = await run_heal_loop(
        stage="execution",
        scope=HealScope.SETUP,
        attempt_fn=attempt,
        diagnose_fn=diagnose,
        apply_fn=apply,
        budget=2,
        ledger=ledger,
    )
    assert result.success
    assert result.resolve_method.startswith("healed:")
    assert applied["ok"]
    assert ledger.path.exists()
    text = ledger.path.read_text(encoding="utf-8")
    assert "heal_plan" in text
    assert "heal_success" in text


@pytest.mark.asyncio
async def test_heal_loop_give_up():
    async def attempt():
        raise RuntimeError("断言失败：应出现错误提示")

    async def diagnose(_f):
        return HealPlan.give_up("product defect", category="product_defect")

    async def apply(_p, _f):
        raise AssertionError("must not apply")

    result = await run_heal_loop(
        stage="execution",
        scope=HealScope.CASE,
        attempt_fn=attempt,
        diagnose_fn=diagnose,
        apply_fn=apply,
        budget=2,
        skip_initial_attempt=True,
        initial_error=RuntimeError("断言失败：应出现错误提示"),
    )
    assert not result.success
    assert result.resolve_method == "give_up"


def test_heal_budget_scopes():
    b = HealBudget(setup=3, step=1, case=2)
    assert b.for_scope(HealScope.SETUP) == 3
    assert b.for_scope("step") == 1
    assert b.for_scope(HealScope.CASE) == 2


def test_heal_budget_from_config():
    from execution_runtime.config import RunConfig, RuntimeConfig
    from execution_runtime.heal import heal_budget_from_config

    cfg = RuntimeConfig(
        run=RunConfig(
            max_heal_attempts=2,
            heal_budget_setup=3,
            heal_budget_step=1,
            heal_budget_case=2,
        )
    )
    budget = heal_budget_from_config(cfg)
    assert budget.setup == 3
    assert budget.step == 1
    assert budget.case == 2
