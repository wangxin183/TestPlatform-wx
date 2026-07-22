"""EXE 阶段 StageAgentHarness 适配：diagnoser → ToolGateway 策略。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.agent_runtime import AgentTask, agent_runtime
from src.agent_runtime.cli_shared import extract_json
from src.llm.prompts.skill_loader import load_skill
from src.services.heal_loop import (
    FailureCategory,
    FailureInfo,
    HealBudget,
    HealLedger,
    HealPlan,
    HealScope,
    default_classify,
    run_heal_loop,
)

from execution_runtime.config import RuntimeConfig
from execution_runtime.tools.action_catalog import tool_schemas_for_prompt
from execution_runtime.tools.gateway import ToolGateway, ToolGatewayError

ROLE_DIAGNOSER = "execution.diagnoser"
_ALLOWED_ACTIONS = frozenset(
    {
        "recover_page",
        "dismiss_and_retry",
        "reenter_module",
        "retry_agent_step",
        "retry_dsl",
        "launch_app",
        "give_up",
        "give_up_defect",
    }
)


def heal_budget_from_config(cfg: RuntimeConfig) -> HealBudget:
    run = cfg.run
    # 兼容旧 max_heal_attempts：未配置分档时用其作为 case/step 默认
    legacy = int(getattr(run, "max_heal_attempts", 2) or 2)
    setup = int(getattr(run, "heal_budget_setup", 0) or 0) or legacy
    step = int(getattr(run, "heal_budget_step", 0) or 0) or legacy
    case = int(getattr(run, "heal_budget_case", 0) or 0) or legacy
    return HealBudget(setup=setup, step=step, case=case, stage=legacy)


def classify_execution_error(exc: BaseException, *, scope: str = "") -> FailureInfo:
    info = default_classify(exc, stage="execution", scope=scope)
    msg = str(exc)
    if "缺陷" in msg or "product" in msg.lower():
        info.category = FailureCategory.PRODUCT_DEFECT
    return info


async def diagnose_execution_failure(
    failure: FailureInfo,
    *,
    gateway: ToolGateway,
    run_dir: Path,
    case_id: str = "",
    module: str = "",
    contract: dict[str, Any] | None = None,
) -> HealPlan:
    """调用 execution.diagnoser；失败时回退到保守 recover_page 计划。"""
    skill = load_skill("execution-healer")
    skill_body = skill.body if skill else ""
    try:
        page = gateway.observer.observe().as_agent_dict()
    except Exception:  # noqa: BLE001
        page = {}
    payload = {
        "failure": {
            "category": failure.category.value,
            "message": failure.message,
            "scope": failure.scope,
            "evidence": failure.evidence,
        },
        "case_id": case_id,
        "module": module,
        "contract": contract or {},
        "page": page,
        "allowed_actions": sorted(_ALLOWED_ACTIONS),
        "tools": tool_schemas_for_prompt(),
    }
    prompt = (
        (skill_body + "\n\n" if skill_body else "")
        + "你是执行自愈诊断器。只输出一个 JSON 对象，不要解释：\n"
        '{"category":"...","action":"recover_page|dismiss_and_retry|reenter_module|'
        'retry_agent_step|retry_dsl|launch_app|give_up_defect",'
        '"arguments":{},"goal_still_valid":true,"rationale":"..."}\n\n'
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    try:
        result = await agent_runtime.run(
            AgentTask(
                role=ROLE_DIAGNOSER,
                prompt=prompt,
                workdir=str(run_dir),
                timeout=120,
                stage_name="execution_diagnose",
                task_id=case_id,
            )
        )
        if result.success:
            extracted = extract_json(result.raw_output or "")
            data = extracted.data if extracted.success else None
            if isinstance(data, dict):
                plan = HealPlan.from_dict(data)
                if plan.action not in _ALLOWED_ACTIONS:
                    plan.action = "recover_page"
                    plan.arguments = {"max_backs": 3, "relaunch": False}
                return plan
    except Exception:  # noqa: BLE001
        pass
    # 保守默认：页面恢复，不直接 give_up
    return HealPlan(
        action="recover_page",
        arguments={"max_backs": 3, "relaunch": False},
        category=failure.category.value,
        rationale="diagnoser 不可用，回退 recover_page",
        goal_still_valid=True,
    )


async def apply_execution_plan(
    plan: HealPlan,
    failure: FailureInfo,
    *,
    gateway: ToolGateway,
    module: str = "",
) -> None:
    """将 HealPlan 落到 ToolGateway（不得绕过）。"""
    action = plan.action
    args = dict(plan.arguments or {})
    if action in {"give_up", "give_up_defect"}:
        return
    if action in {"retry_agent_step", "retry_dsl", "dismiss_and_retry"}:
        # 战役级「再试」：先做一次轻量恢复再交给外层 attempt_fn
        action = "recover_page"
        args.setdefault("max_backs", 2)
        args.setdefault("relaunch", False)
    if action == "launch_app":
        gateway.call("launch_app", {})
        return
    if action == "reenter_module":
        gateway.call("recover_page", {"max_backs": 3, "relaunch": True})
        if module:
            # 模块重进由外层 navigate 负责；此处只保证 App 活着且回退干净
            pass
        return
    if action == "recover_page":
        gateway.call(
            "recover_page",
            {
                "max_backs": int(args.get("max_backs") or 3),
                "relaunch": bool(args.get("relaunch", False)),
                **(
                    {"until": args["until"]}
                    if isinstance(args.get("until"), (dict, str)) and args.get("until")
                    else {}
                ),
            },
        )
        return
    raise ToolGatewayError(f"不支持的自愈动作: {action}")


async def heal_run(
    *,
    scope: HealScope,
    cfg: RuntimeConfig,
    gateway: ToolGateway,
    run_dir: Path,
    attempt_fn,
    case_id: str = "",
    module: str = "",
    contract: dict[str, Any] | None = None,
    initial_error: BaseException | None = None,
) -> Any:
    """EXE 便捷入口：按 scope 取预算并跑 HealLoop。"""
    if not cfg.run.self_heal_enabled:
        if initial_error:
            raise initial_error
        return await attempt_fn()

    budget = heal_budget_from_config(cfg).for_scope(scope)
    ledger = HealLedger(run_dir / "heal_ledger.jsonl")

    async def _diagnose(failure: FailureInfo) -> HealPlan:
        return await diagnose_execution_failure(
            failure,
            gateway=gateway,
            run_dir=run_dir,
            case_id=case_id,
            module=module,
            contract=contract,
        )

    async def _apply(plan: HealPlan, failure: FailureInfo) -> None:
        await apply_execution_plan(plan, failure, gateway=gateway, module=module)

    result = await run_heal_loop(
        stage="execution",
        scope=scope,
        attempt_fn=attempt_fn,
        diagnose_fn=_diagnose,
        apply_fn=_apply,
        budget=budget,
        ledger=ledger,
        classify_fn=lambda exc: classify_execution_error(exc, scope=scope.value),
        skip_initial_attempt=initial_error is not None,
        initial_error=initial_error,
    )
    if not result.success:
        raise RuntimeError(
            result.final_error or f"自愈耗尽({scope.value})"
        )
    return result.output
