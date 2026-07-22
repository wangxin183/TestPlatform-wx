"""TCG 可执行性自愈：定点加固 expected/合同，失败再 Agent 改写，再失败可整案重生。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from src.agent_runtime import AgentTask, agent_runtime
from src.agent_runtime.cli_shared import extract_json
from src.services.heal_loop import HealLedger
from src.services.testcase_contract_compiler import prepare_executable_case
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

ROLE_PATCH = "testcase.generator"
LogFn = Callable[..., None]
RegenFn = Callable[[str, dict[str, Any]], Awaitable[list[dict[str, Any]]]]


def _is_weak_compile(prepared: dict[str, Any]) -> bool:
    status = str(prepared.get("compile_status") or "")
    if status == "failed":
        return True
    hint = str(prepared.get("automation_level_hint") or "")
    if hint == "manual" and status != "ok":
        return True
    return False


def _merge_trace_fields(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    out = dict(target)
    for key in (
        "test_point_id",
        "related_fr",
        "module",
        "platform_type",
        "precondition_spec",
        "priority",
        "tags",
    ):
        if not out.get(key) and source.get(key):
            out[key] = source[key]
    return out


async def _agent_patch_expected(
    case: dict[str, Any],
    *,
    compile_errors: list[Any],
    workdir: str,
    generation_id: str,
) -> dict[str, Any] | None:
    prompt = (
        "你是 UI 用例可执行性修复器。只改写 steps 里每步的 expected（必要时微调 "
        "step_contracts.postconditions），使断言可判定：可见文案必须用「」包裹；"
        "负向写「不出现「xxx」」。禁止改 title/test_point_id/module/action 语义，"
        "禁止编造业务需求。只输出一个用例 JSON 对象。\n\n"
        f"compile_errors={json.dumps(compile_errors, ensure_ascii=False)[:2000]}\n\n"
        f"case={json.dumps(case, ensure_ascii=False)[:6000]}"
    )
    result = await agent_runtime.run(
        AgentTask(
            role=ROLE_PATCH,
            prompt=prompt,
            workdir=workdir,
            timeout=180,
            stage_name="testcase_exec_heal",
            task_id=generation_id,
        )
    )
    if not result.success:
        return None
    extracted = extract_json(result.raw_output or "")
    data = extracted.data if extracted.success else None
    if isinstance(data, list) and data:
        data = data[0]
    return data if isinstance(data, dict) else None


async def heal_cases_for_executability(
    cases: list[dict[str, Any]],
    *,
    generation_id: str,
    workdir: str,
    log: Optional[LogFn] = None,
    regen_fn: Optional[RegenFn] = None,
) -> list[dict[str, Any]]:
    """对编译失败/断言过弱的用例做定点自愈，返回替换后的列表。"""

    def _log(step: str, **kw: Any) -> None:
        if log:
            log(step, **kw)

    out = list(cases)
    weak_indexes = [
        idx
        for idx, case in enumerate(out)
        if _is_weak_compile(prepare_executable_case(case))
    ]
    if not weak_indexes:
        return out

    _log(
        "self_heal_start",
        failure_category="executability",
        step_name="compile_quality",
        failed_compile_count=len(weak_indexes),
    )
    ledger = HealLedger(Path(workdir) / "heal_ledger.jsonl")
    healed_count = 0

    for idx in weak_indexes:
        original = out[idx]
        title = str(original.get("title") or "")[:60]
        patched = copy.deepcopy(original)
        prepared = prepare_executable_case(patched)

        try:
            agent_case = await _agent_patch_expected(
                patched,
                compile_errors=list(prepared.get("compile_errors") or []),
                workdir=workdir,
                generation_id=generation_id,
            )
            if agent_case:
                agent_case = _merge_trace_fields(agent_case, original)
                check = prepare_executable_case(agent_case)
                _log(
                    "exec_heal_agent_patch",
                    title=title,
                    compile_status=check.get("compile_status"),
                )
                ledger.append(
                    "exec_heal_agent_patch",
                    title=title,
                    compile_status=check.get("compile_status"),
                )
                if not _is_weak_compile(check):
                    out[idx] = agent_case
                    healed_count += 1
                    continue
                patched = agent_case
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "testcase_exec_heal_agent_failed",
                title=title,
                error=str(exc)[:200],
            )

        tp_id = str(original.get("test_point_id") or "").strip()
        if regen_fn and tp_id:
            try:
                regen_cases = await regen_fn(tp_id, original)
                _log(
                    "exec_heal_regen",
                    test_point_id=tp_id,
                    case_count=len(regen_cases or []),
                    title=title,
                )
                ledger.append(
                    "exec_heal_regen",
                    test_point_id=tp_id,
                    case_count=len(regen_cases or []),
                    title=title,
                )
                if regen_cases:
                    best = None
                    for cand in regen_cases:
                        merged = _merge_trace_fields(cand, original)
                        if not _is_weak_compile(prepare_executable_case(merged)):
                            best = merged
                            break
                    out[idx] = best or _merge_trace_fields(regen_cases[0], original)
                    healed_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "testcase_exec_heal_regen_failed",
                    tp_id=tp_id,
                    error=str(exc)[:200],
                )

    _log(
        "self_heal_complete" if healed_count else "self_heal_exhausted",
        outcome="success" if healed_count == len(weak_indexes) else "partial",
        healed_count=healed_count,
        targeted=len(weak_indexes),
    )
    return out
