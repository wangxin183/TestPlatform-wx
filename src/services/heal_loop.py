"""StageAgentHarness 内核：跨阶段 HealLoop。

模型（Brain）只负责决策；本模块提供循环、预算、分类钩子与 ledger。
RA 现有 SelfHealingOrchestrator 可逐步委托至此；EXE 经 execution_runtime.heal 接入。

设计：docs/superpowers/plans/2026-07-22-agent-engineering-self-heal.md
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional


class HealScope(str, Enum):
    SETUP = "setup"
    STEP = "step"
    CASE = "case"
    STAGE = "stage"  # RA/TCG 整阶段尝试


class FailureCategory(str, Enum):
    INFRA = "infra"
    PARSE = "parse"
    QUALITY = "quality"
    BLOCKED_UI = "blocked_ui"
    CONTRACT = "contract_mismatch"
    COVERAGE = "coverage"
    PRODUCT_DEFECT = "product_defect"
    UNKNOWN = "unknown"


@dataclass
class HealBudget:
    setup: int = 2
    step: int = 2
    case: int = 2
    stage: int = 2

    def for_scope(self, scope: HealScope | str) -> int:
        key = scope.value if isinstance(scope, HealScope) else str(scope)
        return int(getattr(self, key, self.case) or 0)


@dataclass
class FailureInfo:
    category: FailureCategory
    message: str = ""
    stage: str = ""
    scope: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealPlan:
    action: str
    arguments: dict[str, Any] = field(default_factory=dict)
    category: str = ""
    rationale: str = ""
    goal_still_valid: bool = True

    @classmethod
    def give_up(cls, rationale: str, *, category: str = "unknown") -> "HealPlan":
        return cls(
            action="give_up",
            category=category,
            rationale=rationale,
            goal_still_valid=False,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HealPlan":
        return cls(
            action=str(data.get("action") or "give_up"),
            arguments=dict(data.get("arguments") or {}),
            category=str(data.get("category") or ""),
            rationale=str(data.get("rationale") or ""),
            goal_still_valid=bool(data.get("goal_still_valid", True)),
        )


@dataclass
class HealResult:
    success: bool
    attempts: int = 0
    resolve_method: str = ""
    final_error: str = ""
    plans: list[HealPlan] = field(default_factory=list)
    output: Any = None


class HealLedger:
    """追加写入 heal_ledger.jsonl。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, **payload: Any) -> None:
        from src.services.narrative_log import narrate

        line = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **payload,
        }
        if not line.get("message"):
            line["message"] = narrate(event, **payload)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


DiagnoseFn = Callable[[FailureInfo], Awaitable[HealPlan]]
ApplyFn = Callable[[HealPlan, FailureInfo], Awaitable[None]]
AttemptFn = Callable[[], Awaitable[Any]]
ClassifyFn = Callable[[BaseException], FailureInfo]


def default_classify(exc: BaseException, *, stage: str = "", scope: str = "") -> FailureInfo:
    msg = str(exc)
    lower = msg.lower()
    if any(k in lower for k in ("timeout", "timed out", "cli", "not found", "connection")):
        category = FailureCategory.INFRA
    elif any(k in msg for k in ("定位", "找不到", "卡在", "弹层", "recover", "导航")):
        category = FailureCategory.BLOCKED_UI
    elif "coverage" in lower or "缺失" in msg:
        category = FailureCategory.COVERAGE
    elif "assert" in lower or "断言" in msg or "expected" in lower:
        category = FailureCategory.CONTRACT
    else:
        category = FailureCategory.UNKNOWN
    return FailureInfo(
        category=category,
        message=msg,
        stage=stage,
        scope=scope,
        evidence={"exc_type": type(exc).__name__},
    )


async def run_heal_loop(
    *,
    stage: str,
    scope: HealScope | str,
    attempt_fn: AttemptFn,
    diagnose_fn: DiagnoseFn,
    apply_fn: ApplyFn,
    budget: int,
    ledger: HealLedger | None = None,
    classify_fn: Optional[ClassifyFn] = None,
    skip_initial_attempt: bool = False,
    initial_error: BaseException | None = None,
) -> HealResult:
    """通用自愈环。

    默认先跑 attempt_fn；失败则 diagnose → apply → 再 attempt，直到成功或预算耗尽。
    若调用方已失败一次，可设 skip_initial_attempt=True 并传入 initial_error。
    """
    scope_key = scope.value if isinstance(scope, HealScope) else str(scope)
    max_attempts = max(0, int(budget))
    plans: list[HealPlan] = []
    classify = classify_fn or (
        lambda exc: default_classify(exc, stage=stage, scope=scope_key)
    )

    if max_attempts <= 0 and not skip_initial_attempt:
        # 无自愈预算：只尝试一次主路径
        try:
            out = await attempt_fn()
            return HealResult(success=True, attempts=1, resolve_method="first_pass", output=out)
        except Exception as exc:  # noqa: BLE001
            return HealResult(success=False, attempts=1, final_error=str(exc))

    attempt_count = 0
    last_error = ""
    pending_failure: FailureInfo | None = None

    if skip_initial_attempt and initial_error is not None:
        pending_failure = classify(initial_error)
        last_error = str(initial_error)
        if ledger:
            ledger.append(
                "heal_seed_failure",
                stage=stage,
                scope=scope_key,
                category=pending_failure.category.value,
                message=pending_failure.message,
            )
    else:
        attempt_count += 1
        try:
            out = await attempt_fn()
            if ledger:
                ledger.append(
                    "heal_first_pass",
                    stage=stage,
                    scope=scope_key,
                    attempts=attempt_count,
                )
            return HealResult(
                success=True,
                attempts=attempt_count,
                resolve_method="first_pass",
                output=out,
            )
        except Exception as exc:  # noqa: BLE001
            pending_failure = classify(exc)
            last_error = str(exc)
            if ledger:
                ledger.append(
                    "heal_attempt_failed",
                    stage=stage,
                    scope=scope_key,
                    attempt=attempt_count,
                    category=pending_failure.category.value,
                    message=pending_failure.message,
                )

    heal_used = 0
    while pending_failure is not None and heal_used < max_attempts:
        if pending_failure.category == FailureCategory.PRODUCT_DEFECT:
            if ledger:
                ledger.append(
                    "heal_product_defect",
                    stage=stage,
                    scope=scope_key,
                    message=pending_failure.message,
                )
            return HealResult(
                success=False,
                attempts=attempt_count,
                resolve_method="product_defect",
                final_error=pending_failure.message,
                plans=plans,
            )

        heal_used += 1
        if ledger:
            ledger.append(
                "heal_diagnose_start",
                stage=stage,
                scope=scope_key,
                heal_index=heal_used,
                category=pending_failure.category.value,
            )
        plan = await diagnose_fn(pending_failure)
        plans.append(plan)
        if ledger:
            ledger.append(
                "heal_plan",
                stage=stage,
                scope=scope_key,
                heal_index=heal_used,
                plan=asdict(plan),
            )

        if plan.action in {"give_up", "give_up_defect"} or not plan.goal_still_valid:
            return HealResult(
                success=False,
                attempts=attempt_count,
                resolve_method="give_up",
                final_error=plan.rationale or pending_failure.message,
                plans=plans,
            )

        try:
            await apply_fn(plan, pending_failure)
        except Exception as apply_exc:  # noqa: BLE001
            last_error = f"apply_plan failed: {apply_exc}"
            if ledger:
                ledger.append(
                    "heal_apply_failed",
                    stage=stage,
                    scope=scope_key,
                    error=last_error,
                )
            pending_failure = classify(apply_exc)
            continue

        attempt_count += 1
        try:
            out = await attempt_fn()
            if ledger:
                ledger.append(
                    "heal_success",
                    stage=stage,
                    scope=scope_key,
                    attempts=attempt_count,
                    heal_used=heal_used,
                    action=plan.action,
                )
            return HealResult(
                success=True,
                attempts=attempt_count,
                resolve_method=f"healed:{plan.action}",
                output=out,
                plans=plans,
            )
        except Exception as exc:  # noqa: BLE001
            pending_failure = classify(exc)
            last_error = str(exc)
            if ledger:
                ledger.append(
                    "heal_retry_failed",
                    stage=stage,
                    scope=scope_key,
                    attempt=attempt_count,
                    category=pending_failure.category.value,
                    message=pending_failure.message,
                )

    if ledger:
        ledger.append(
            "heal_exhausted",
            stage=stage,
            scope=scope_key,
            attempts=attempt_count,
            heal_used=heal_used,
            final_error=last_error,
        )
    return HealResult(
        success=False,
        attempts=attempt_count,
        resolve_method="exhausted",
        final_error=last_error,
        plans=plans,
    )
