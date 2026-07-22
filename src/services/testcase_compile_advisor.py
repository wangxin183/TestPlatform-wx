"""用例编译诊断顾问：failed / agent_required 时经 agent_runtime 即时生成建议。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from src.agent_runtime import AgentTask, agent_runtime
from src.agent_runtime.cli_shared import extract_json
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

ROLE = "testcase.compile_advisor"
SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / ".agents"
    / "skills"
    / "testcase-compile-advisor"
    / "SKILL.md"
)
ADVISE_STATUSES = frozenset({"failed", "agent_required"})
DEFAULT_TIMEOUT_S = 90
DEFAULT_CONCURRENCY = 3

_FALLBACK_SUGGESTION = (
    "诊断 Agent 暂不可用。请根据错误码与步骤检查 action/expected："
    "expected 优先用「」包裹可见文案；去掉「任一/某个」等模糊目标后重新编译。"
)
_FALLBACK_NEED = (
    "补充可判定的操作目标或「」可见文案；Agent 恢复后可点「重新编译」获取智能建议。"
)


def needs_compile_advice(prepared: dict[str, Any]) -> bool:
    return str(prepared.get("compile_status") or "") in ADVISE_STATUSES


def fallback_compile_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agent 不可用时的最小兜底（非错误码文案表）。"""
    out: list[dict[str, Any]] = []
    for raw in errors or []:
        item = dict(raw)
        item["reason"] = str(item.get("reason") or item.get("message") or "编译未通过")
        if not str(item.get("suggestion") or "").strip():
            item["suggestion"] = _FALLBACK_SUGGESTION
        if not str(item.get("need") or "").strip():
            item["need"] = _FALLBACK_NEED
        # 兼容旧前端字段
        if not str(item.get("message") or "").strip():
            item["message"] = item["reason"]
        out.append(item)
    if not out:
        out.append(
            {
                "code": "COMPILE_ADVICE_UNAVAILABLE",
                "reason": "编译未通过且无规则明细",
                "message": "编译未通过且无规则明细",
                "suggestion": _FALLBACK_SUGGESTION,
                "need": _FALLBACK_NEED,
                "severity": "manual",
            }
        )
    return out


def _load_skill() -> str:
    try:
        return SKILL_PATH.read_text(encoding="utf-8")
    except OSError:
        return (
            "你是用例编译诊断顾问。输出 JSON 数组，每项含 step/code/reason/suggestion/need。"
        )


def _build_prompt(prepared: dict[str, Any]) -> str:
    skill = _load_skill()
    payload = {
        "compile_status": prepared.get("compile_status"),
        "assertion_quality": prepared.get("assertion_quality"),
        "automation_block_reason": prepared.get("automation_block_reason"),
        "rule_errors": prepared.get("compile_errors") or [],
        "case": {
            "title": prepared.get("title"),
            "module": prepared.get("module"),
            "preconditions": prepared.get("preconditions"),
            "test_point_id": prepared.get("test_point_id"),
            "steps": prepared.get("steps") or [],
        },
    }
    return (
        f"{skill}\n\n## 本用例输入\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False)[:10000]}\n```\n"
    )


def _normalize_advice_items(
    raw: Any,
    *,
    rule_errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("errors") or raw.get("advice") or raw.get("items") or [raw]
    if not isinstance(raw, list):
        return fallback_compile_errors(rule_errors)

    items: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        reason = str(
            entry.get("reason") or entry.get("message") or ""
        ).strip()
        suggestion = str(entry.get("suggestion") or "").strip()
        need = str(entry.get("need") or "").strip()
        if not reason and not suggestion and not need:
            continue
        step = entry.get("step")
        try:
            step_n = int(step) if step is not None and str(step).strip() else None
        except (TypeError, ValueError):
            step_n = None
        item: dict[str, Any] = {
            "code": str(entry.get("code") or "COMPILE_ADVICE").strip() or "COMPILE_ADVICE",
            "reason": reason or "编译未通过",
            "message": reason or "编译未通过",
            "suggestion": suggestion or _FALLBACK_SUGGESTION,
            "need": need or _FALLBACK_NEED,
            "severity": str(entry.get("severity") or "manual"),
        }
        if step_n is not None:
            item["step"] = step_n
        items.append(item)

    if not items:
        return fallback_compile_errors(rule_errors)
    return items


async def advise_compile_case(
    prepared: dict[str, Any],
    *,
    workdir: str = "",
    task_id: str = "",
    stage_name: str = "testcase_compile_advisor",
    timeout: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """若状态为 failed/agent_required，调用 Agent 填充诊断；否则原样返回。"""
    out = dict(prepared)
    if not needs_compile_advice(out):
        return out

    rule_errors = [
        dict(e) for e in (out.get("compile_errors") or []) if isinstance(e, dict)
    ]
    prompt = _build_prompt(out)
    try:
        result = await agent_runtime.run(
            AgentTask(
                role=ROLE,
                prompt=prompt,
                workdir=workdir or None,
                timeout=timeout,
                stage_name=stage_name,
                task_id=task_id,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("compile_advisor_failed", error=str(exc), task_id=task_id)
        out["compile_errors"] = fallback_compile_errors(rule_errors)
        return out

    if not result.success:
        logger.warning(
            "compile_advisor_unsuccessful",
            error=result.error,
            backend=result.backend,
            task_id=task_id,
        )
        out["compile_errors"] = fallback_compile_errors(rule_errors)
        return out

    extracted = extract_json(result.raw_output or "")
    data = extracted.data if extracted.success else None
    out["compile_errors"] = _normalize_advice_items(data, rule_errors=rule_errors)
    out["compile_advice_backend"] = result.backend
    return out


async def advise_prepared_cases(
    prepared_list: list[dict[str, Any]],
    *,
    workdir: str = "",
    task_id: str = "",
    max_concurrency: int = DEFAULT_CONCURRENCY,
) -> list[dict[str, Any]]:
    """对多条已规则编译的用例按需并发诊断（每条问题用例一次 Agent）。"""
    if not prepared_list:
        return []
    sem = asyncio.Semaphore(max(1, int(max_concurrency or DEFAULT_CONCURRENCY)))

    async def _one(item: dict[str, Any]) -> dict[str, Any]:
        if not needs_compile_advice(item):
            return item
        async with sem:
            return await advise_compile_case(
                item,
                workdir=workdir,
                task_id=task_id,
            )

    return list(await asyncio.gather(*[_one(p) for p in prepared_list]))
