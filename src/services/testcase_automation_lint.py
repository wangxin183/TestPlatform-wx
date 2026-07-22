"""用例自动化可编译性 lint（确定性规则，不调 LLM）。

用于推断/校验 automation_level：ready | semi | manual。
"""

from __future__ import annotations

from typing import Any

from execution_runtime.compiler.nl_assert import objective_text_signals
from src.services.automation_lexicon import (
    get_conditional_obs,
    get_subjective,
    get_vague_action,
)

LEVEL_READY = "ready"
LEVEL_SEMI = "semi"
LEVEL_MANUAL = "manual"
VALID_LEVELS = frozenset({LEVEL_READY, LEVEL_SEMI, LEVEL_MANUAL})


def _steps(case: dict[str, Any]) -> list[dict[str, Any]]:
    raw = case.get("steps") or []
    return [s for s in raw if isinstance(s, dict)]


def lint_case(case: dict[str, Any]) -> dict[str, Any]:
    """返回 {level, warnings: [str], scores 可选}。"""
    warnings: list[str] = []
    semi_hits = 0
    manual_hits = 0
    subjective_keys = get_subjective()
    vague_action = get_vague_action()
    conditional_obs = get_conditional_obs()

    steps = _steps(case)
    if not steps:
        warnings.append("无步骤")
        manual_hits += 2

    for s in steps:
        action = str(s.get("action") or "").strip()
        expected = str(s.get("expected") or "").strip()
        if any(k in action for k in vague_action):
            warnings.append(f"模糊 action: {action[:40]}")
            semi_hits += 1
        if any(k in action for k in conditional_obs) or (
            any(k in action for k in ("查看", "观察"))
            and not any(k in action for k in ("验证", "检查", "确认", "断言"))
        ):
            warnings.append(f"观察/条件句: {action[:40]}")
            semi_hits += 1
        if not expected:
            warnings.append(f"空 expected: {action[:40]}")
            semi_hits += 1
        elif any(k in expected for k in subjective_keys):
            visible, absent = objective_text_signals(expected)
            has_quote = "「" in expected or "『" in expected or '"' in expected or "'" in expected
            if visible or absent or has_quote:
                warnings.append(f"主观+客观混写 expected: {expected[:40]}")
                semi_hits += 1
            else:
                warnings.append(f"主观 expected: {expected[:40]}")
                manual_hits += 1

    pre = str(case.get("preconditions") or "")
    if pre and any(k in pre for k in ("需人工", "手动", "已选", "已进入", "已打开", "已登录")):
        if "需人工" in pre or "手动" in pre:
            warnings.append("前置条件需人工准备")
            semi_hits += 1
        elif _setup_covers_entry(case):
            # entry_context / login_state 已由执行 Setup 承接，不因「已进入」降级
            pass
        elif not any(
            any(k in str(s.get("action") or "") for k in ("点击", "选择", "打开", "进入"))
            for s in steps[:3]
        ):
            warnings.append("前置条件未步骤化")
            semi_hits += 1

    tags = case.get("tags") or []
    if isinstance(tags, list) and any(str(t).lower() == "manual" for t in tags):
        manual_hits += 2
        warnings.append("tags 含 manual")

    declared = str(case.get("automation_level") or "").strip().lower()
    if declared in VALID_LEVELS:
        # 声明与规则取更保守者
        inferred = _infer_from_hits(semi_hits, manual_hits)
        level = _more_conservative(declared, inferred)
    else:
        level = _infer_from_hits(semi_hits, manual_hits)

    return {
        "level": level,
        "warnings": warnings,
        "semi_hits": semi_hits,
        "manual_hits": manual_hits,
    }


def _setup_covers_entry(case: dict[str, Any]) -> bool:
    """precondition_spec 已足够驱动登录/入口 Setup 时，文案「已进入」不算人工缺口。"""
    spec = case.get("precondition_spec") or {}
    if not isinstance(spec, dict):
        return False
    entry = str(spec.get("entry_context") or "").strip()
    login = str(spec.get("login_state") or "").strip()
    if entry and entry != "module_default":
        return True
    if login in {"logged_in", "logged_out"} and entry == "module_default":
        return True
    return bool(login in {"logged_in", "logged_out"} and entry)


def _infer_from_hits(semi_hits: int, manual_hits: int) -> str:
    if manual_hits >= 2:
        return LEVEL_MANUAL
    if manual_hits >= 1 and semi_hits >= 1:
        return LEVEL_MANUAL
    if manual_hits >= 1 or semi_hits >= 2:
        return LEVEL_SEMI
    if semi_hits >= 1:
        return LEVEL_SEMI
    return LEVEL_READY


def _more_conservative(a: str, b: str) -> str:
    order = {LEVEL_READY: 0, LEVEL_SEMI: 1, LEVEL_MANUAL: 2}
    return a if order.get(a, 0) >= order.get(b, 0) else b


def resolve_automation_level(case: dict[str, Any]) -> str:
    """落库字段与规则取更保守者；无字段则纯推断。"""
    return lint_case(case)["level"]
