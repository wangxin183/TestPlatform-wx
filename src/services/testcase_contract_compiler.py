"""把评审友好的 NL 用例准备为步骤合同与确定性 DSL。

该模块不调用 LLM：可确定的步骤使用本地编译器生成 DSL；目标或定位
存在歧义时标记 agent_required；缺少安全判定条件时标记 failed。
"""

from __future__ import annotations

import re
from typing import Any

from execution_runtime.compiler.local_compiler import compile_case_local
from execution_runtime.compiler.nl_assert import (
    objective_text_signals,
    quoted_values,
)
from execution_runtime.config import RuntimeConfig, load_config
from src.services.automation_lexicon import (
    get_action_verbs,
    get_ambiguous,
    get_subjective,
)
from src.services.precondition_spec import (
    ensure_precondition_spec,
    humanize_precondition_spec,
    validate_precondition_spec,
)
from src.services.testcase_module_catalog import ACNModuleCatalog, module_catalog

_PAGE_TRANSITION_RE = re.compile(
    r"(?:跳转至|跳转到|进入|打开|返回至|返回到)[「『]?([^，。；」』]+?页)[」』]?"
)
_STRONG_POST = (
    "text_visible:",
    "text_absent:",
    "page_content_changed",
    "page_indicator_changed",
    "page_changed",
    "ocr_text_visible:",
)


def _quoted(text: str) -> list[str]:
    return quoted_values(text)


def _action_kind(action: str) -> str:
    for kind, words in get_action_verbs().items():
        if any(word in action for word in words):
            return kind
    return ""


def _target_description(action: str) -> str:
    values = _quoted(action)
    if values:
        return values[0]
    text = action
    for words in get_action_verbs().values():
        for word in words:
            text = text.replace(word, "")
    return text.strip(" ，。")


def _objective_postconditions(action: str, expected: str) -> list[str]:
    posts: list[str] = []
    visible, absent = objective_text_signals(expected)
    for value in visible:
        posts.append(f"text_visible:{value}")
    for value in absent:
        posts.append(f"text_absent:{value}")
    if re.search(r"\d+\s*/\s*\d+", expected):
        posts.append("page_indicator_changed")
    kind = _action_kind(action)
    if kind == "swipe" and not any(word in expected for word in get_subjective()):
        if "page_content_changed" not in posts:
            posts.append("page_content_changed")
    if not posts and any(
        word in expected for word in ("出现", "显示", "可见", "进入", "跳转")
    ):
        # 「不出现」等负向已由 absent 覆盖；仅剩正向可见语义时才加弱断言
        if not absent and not re.search(
            r"不(?:出现|展示|显示|可见)", expected or ""
        ):
            posts.append("expected_state_visible")
    return posts


def _destination_state(
    current_state: str,
    action: str,
    expected: str,
    *,
    module_name: str,
    module_state: str,
) -> str:
    matches = _PAGE_TRANSITION_RE.findall(f"{action}；{expected}")
    if not matches:
        return current_state
    page_name = matches[-1].strip()
    if module_name and module_name in page_name:
        return module_state
    return f"external:{page_name}"


def _has_l1(contract: dict[str, Any]) -> bool:
    transition = str(contract.get("expected_transition") or "")
    start = ""
    dest = ""
    if "->" in transition:
        start, dest = [p.strip() for p in transition.rsplit("->", 1)]
    if dest and dest != start:
        return True
    # 断言步停留在已命名页面状态，视为结构锚点验收
    start_state = str(contract.get("start_state") or start or "")
    if str(contract.get("action_kind") or "") == "assert" and start_state:
        return True
    return False


def _has_l2(postconditions: list[str]) -> bool:
    for p in postconditions:
        if p.startswith("text_visible:") or p.startswith("text_absent:"):
            return True
        if p.startswith("ocr_text_visible:"):
            return True
        if p in {
            "page_content_changed",
            "page_indicator_changed",
            "page_changed",
        }:
            return True
    return False


_ACTION_ONLY_KINDS = frozenset({"tap", "wait", "swipe", "input", "scroll"})


def score_assertion_quality(contracts: list[dict[str, Any]]) -> str:
    """strong | adequate | weak | none

    纯操作步（tap/wait/swipe/input 等且无 postconditions）不参与打分，
    避免中间点击/等待把已有 L1/L2 断言的用例拖成 none。
    """
    if not contracts:
        return "none"
    qualities: list[str] = []
    for c in contracts:
        posts = [str(p) for p in (c.get("postconditions") or [])]
        kind = str(c.get("action_kind") or "").strip().lower()
        if not posts and kind in _ACTION_ONLY_KINDS:
            continue
        l1 = _has_l1(c)
        l2 = _has_l2(posts)
        only_weak = posts and all(p == "expected_state_visible" for p in posts)
        if l1 and l2:
            qualities.append("strong")
        elif l1 or l2:
            qualities.append("adequate")
        elif only_weak or posts:
            qualities.append("weak")
        else:
            qualities.append("none")
    if not qualities:
        return "none"
    order = {"none": 0, "weak": 1, "adequate": 2, "strong": 3}
    # 用例整体取最弱「可评」步骤
    return min(qualities, key=lambda q: order[q])


def build_step_contracts(
    case: dict[str, Any],
    catalog: ACNModuleCatalog = module_catalog,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回 (contracts, errors)。errors 同时包含阻断和需 Agent 的原因。"""
    module_name = catalog.resolve(str(case.get("module") or ""))
    module = catalog.get(module_name)
    if module is None:
        return [], [
            {
                "code": "MODULE_REQUIRED",
                "message": "用例必须映射到 ACN 一级模块",
                "severity": "blocking",
            }
        ]

    state_id = module.page_states[0].id if module.page_states else f"{module.id}_main"
    current_state = state_id
    contracts: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, raw in enumerate(case.get("steps") or [], start=1):
        if not isinstance(raw, dict):
            continue
        step_no = int(raw.get("step") or index)
        action = str(raw.get("action") or "").strip()
        expected = str(raw.get("expected") or "").strip()
        kind = _action_kind(action)
        target_description = _target_description(action)
        postconditions = _objective_postconditions(action, expected)
        destination_state = _destination_state(
            current_state,
            action,
            expected,
            module_name=module.name,
            module_state=state_id,
        )
        if destination_state != current_state and "page_changed" not in postconditions:
            postconditions.append("page_changed")

        draft_contract = {
            "step": step_no,
            "start_state": current_state,
            "intent": action,
            "action_kind": kind or "agent_decide",
            "target": {"description": target_description},
            "expected_transition": f"{current_state} -> {destination_state}",
            "postconditions": postconditions,
            "forbidden_states": ["login_dialog", "payment_dialog"],
        }

        if not action:
            errors.append(
                {
                    "code": "ACTION_REQUIRED",
                    "message": "步骤缺少操作描述",
                    "step": step_no,
                    "severity": "blocking",
                }
            )
        if not expected or (
            any(word in expected for word in get_subjective()) and not postconditions
        ):
            errors.append(
                {
                    "code": "UNVERIFIABLE_EXPECTED",
                    "message": f"预期结果无法客观判定: {expected or '空'}",
                    "step": step_no,
                    "severity": "manual",
                }
            )
        elif postconditions == ["expected_state_visible"] and not _has_l1(
            draft_contract
        ):
            errors.append(
                {
                    "code": "WEAK_ASSERTION",
                    "message": f"仅有弱断言 expected_state_visible: {expected[:40]}",
                    "step": step_no,
                    "severity": "manual",
                }
            )
        if not kind:
            errors.append(
                {
                    "code": "UNSUPPORTED_ACTION",
                    "message": f"无法映射到标准动作: {action}",
                    "step": step_no,
                    "severity": "agent",
                }
            )
        if any(word in action for word in get_ambiguous()):
            errors.append(
                {
                    "code": "AMBIGUOUS_TARGET",
                    "message": f"操作目标需运行时结合页面确定: {target_description}",
                    "step": step_no,
                    "severity": "agent",
                }
            )

        contracts.append(draft_contract)
        current_state = destination_state
    if not contracts:
        errors.append(
            {
                "code": "STEPS_REQUIRED",
                "message": "用例至少需要一个有效步骤",
                "severity": "blocking",
            }
        )
    return contracts, errors


def repair_step_contract_states(
    case: dict[str, Any],
    contracts: list[dict[str, Any]],
    catalog: ACNModuleCatalog = module_catalog,
) -> list[dict[str, Any]]:
    """按 NL 步骤重算状态链，兼容已落库的旧版同状态合同。"""
    module_name = catalog.resolve(str(case.get("module") or ""))
    module = catalog.get(module_name)
    if module is None:
        return [dict(item) for item in contracts]
    module_state = (
        module.page_states[0].id if module.page_states else f"{module.id}_main"
    )
    current_state = module_state
    repaired: list[dict[str, Any]] = []
    steps = [item for item in case.get("steps") or [] if isinstance(item, dict)]
    for index, contract in enumerate(contracts):
        item = dict(contract)
        raw = steps[index] if index < len(steps) else {}
        action = str(raw.get("action") or item.get("intent") or "")
        expected = str(raw.get("expected") or "")
        destination = _destination_state(
            current_state,
            action,
            expected,
            module_name=module.name,
            module_state=module_state,
        )
        postconditions = _objective_postconditions(action, expected)
        if destination != current_state and "page_changed" not in postconditions:
            postconditions.append("page_changed")
        item["start_state"] = current_state
        item["expected_transition"] = f"{current_state} -> {destination}"
        item["postconditions"] = postconditions
        repaired.append(item)
        current_state = destination
    return repaired


def prepare_executable_case(
    case: dict[str, Any],
    cfg: RuntimeConfig | None = None,
    catalog: ACNModuleCatalog = module_catalog,
) -> dict[str, Any]:
    """生成双轨执行字段，不修改传入字典。"""
    prepared = dict(case)
    module_name = catalog.resolve(str(prepared.get("module") or ""))
    prepared["module"] = module_name

    spec = ensure_precondition_spec(prepared)
    prepared["precondition_spec"] = spec
    if not str(prepared.get("preconditions") or "").strip():
        prepared["preconditions"] = humanize_precondition_spec(spec)

    contracts, errors = build_step_contracts(prepared, catalog)
    prepared["step_contracts"] = contracts
    errors.extend(validate_precondition_spec(spec))

    quality = score_assertion_quality(contracts)
    prepared["assertion_quality"] = quality
    if quality == "none" and contracts:
        errors.append(
            {
                "code": "ASSERTION_QUALITY_LOW",
                "message": f"断言质量不足: {quality}（至少需要 L1 锚点或 L2 文案）",
                "severity": "manual",
            }
        )
    elif quality == "weak":
        prepared["automation_level_hint"] = "semi"

    blocking = [e for e in errors if e.get("severity", "blocking") == "blocking"]
    manual_errors = [e for e in errors if e.get("severity") == "manual"]
    agent_errors = [e for e in errors if e.get("severity") == "agent"]

    block_reason = ""
    for e in blocking + manual_errors + agent_errors:
        block_reason = f"{e.get('code')}: {e.get('message')}"
        break
    prepared["automation_block_reason"] = block_reason

    if blocking or manual_errors:
        status = "failed"
        mode = "agent"
        prepared.update(
            {
                "exec_script": None,
                "compile_status": status,
                "compile_errors": errors,
                "execution_mode": mode,
                "automation_level_hint": "manual" if manual_errors else "",
            }
        )
        return prepared

    runtime_cfg = cfg or load_config()
    script = compile_case_local(prepared, runtime_cfg).to_dict()
    script["module"] = module_name
    script["step_contracts"] = contracts
    script["precondition_spec"] = spec
    if agent_errors:
        status = "agent_required"
        mode = "agent"
    else:
        status = "ok"
        mode = "hybrid"
    prepared.update(
        {
            "exec_script": script,
            "compile_status": status,
            "compile_errors": errors,
            "execution_mode": mode,
        }
    )
    return prepared
