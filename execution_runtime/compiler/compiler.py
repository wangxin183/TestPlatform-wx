"""编译层：把 NL 用例（TestCase.steps）编译成可执行 DSL（TestScript）。

硬约束（对齐平台规范）：只通过 agent_runtime.run(AgentTask(role="execution.compiler"))
调用 LLM，禁止直接 subprocess / SDK。编译一次、落盘缓存、执行多次。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.agent_runtime import AgentTask, agent_runtime
from src.agent_runtime.cli_shared import (
    dynamic_timeout,
    estimate_tokens,
    extract_json,
    recover_json_from_workdir,
)

from execution_runtime.config import RuntimeConfig
from execution_runtime.dsl.models import ExecScript

from execution_runtime.compiler.local_compiler import compile_case_local
from execution_runtime.tools.action_catalog import tool_schemas_for_prompt

ROLE_COMPILER = "execution.compiler"
_PROMPT_PATH = Path(__file__).parent / "prompts" / "compile.txt"


class CompileError(RuntimeError):
    """编译失败。"""


def _load_prompt_template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_prompt(case: dict[str, Any], cfg: RuntimeConfig) -> str:
    steps = case.get("steps") or []
    platform = (cfg.target_app.platform or "ios").lower()
    automation = cfg.device.automation_name or (
        "UiAutomator2" if platform == "android" else "XCUITest"
    )
    tmpl = _load_prompt_template()
    return (
        tmpl.replace("{app_name}", cfg.target_app.name)
        .replace("{bundle_id}", cfg.target_app.bundle_id)
        .replace("{platform}", platform)
        .replace("{automation_name}", automation)
        .replace("{title}", str(case.get("title") or ""))
        .replace("{preconditions}", str(case.get("preconditions") or "无"))
        .replace("{case_id}", str(case.get("case_id") or ""))
        .replace("{module}", str(case.get("module") or ""))
        .replace(
            "{step_contracts_json}",
            json.dumps(case.get("step_contracts") or [], ensure_ascii=False, indent=2),
        )
        .replace(
            "{action_tools_json}",
            json.dumps(tool_schemas_for_prompt(), ensure_ascii=False, indent=2),
        )
        .replace(
            "{steps_json}",
            json.dumps(steps, ensure_ascii=False, indent=2),
        )
    )


def _script_has_platform_mismatch(data: dict[str, Any], cfg: RuntimeConfig) -> bool:
    """agent 误输出 iOS 定位时，Android 执行会失败，降级本地编译。"""
    platform = (cfg.target_app.platform or "ios").lower()
    if platform != "android":
        return False
    blob = json.dumps(data, ensure_ascii=False)
    ios_markers = ("XCUIElement", "IOS_PREDICATE", "XCUIElementType", "**/")
    if any(m in blob for m in ios_markers):
        return True
    ios_loc_types = {"name", "predicate", "class_chain"}
    for step in data.get("steps") or []:
        for key in ("locator", "until"):
            loc = step.get(key)
            if isinstance(loc, dict) and loc.get("type") in ios_loc_types:
                return True
    return False


def _extract_script_dict(raw_output: str, workdir: Path) -> dict[str, Any] | None:
    result = extract_json(raw_output)
    data = result.data if result.success else None
    if data is None:
        recovered = recover_json_from_workdir(str(workdir), raw_output=raw_output)
        data = recovered.data if recovered.success else None
    if isinstance(data, list) and data:
        data = data[0]
    return data if isinstance(data, dict) else None


async def compile_case(
    case: dict[str, Any],
    cfg: RuntimeConfig,
    *,
    workdir: Path,
    run_id: str = "",
    allow_local_fallback: bool = True,
) -> ExecScript:
    """编译单条用例为 ExecScript；失败抛 CompileError。

    产物副作用：调用方负责把返回的 ExecScript 落盘到 compiled/<case_id>.json。
    agent 全部失败且 allow_local_fallback=True 时，降级为本地规则编译。
    """
    case_id = str(case.get("case_id") or "")
    prompt = _build_prompt(case, cfg)
    est = estimate_tokens(prompt)
    timeout = dynamic_timeout(est)

    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / f"compile_prompt_{case_id}.txt").write_text(prompt, encoding="utf-8")

    result = await agent_runtime.run(
        AgentTask(
            role=ROLE_COMPILER,
            prompt=prompt,
            workdir=str(workdir),
            timeout=timeout,
            stage_name="execution_compile",
            task_id=run_id or case_id,
        )
    )

    raw = result.raw_output or ""
    (workdir / f"compile_output_{case_id}.txt").write_text(raw, encoding="utf-8")

    if not result.success:
        if allow_local_fallback:
            script = compile_case_local(case, cfg)
            (workdir / f"compile_local_fallback_{case_id}.json").write_text(
                json.dumps(script.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return script
        raise CompileError(
            f"用例 {case_id} 编译失败（agent）: {result.error or 'unknown'}"
        )

    data = _extract_script_dict(raw, workdir)
    if data is None:
        raise CompileError(f"用例 {case_id} 编译输出无法解析为 JSON")

    # 补齐追溯字段
    data.setdefault("case_id", case_id)
    data.setdefault("title", str(case.get("title") or ""))
    data.setdefault("preconditions", str(case.get("preconditions") or ""))
    data.setdefault("test_point_id", str(case.get("test_point_id") or ""))
    data.setdefault("module", str(case.get("module") or ""))
    data.setdefault("execution_mode", str(case.get("execution_mode") or "hybrid"))
    data.setdefault("step_contracts", list(case.get("step_contracts") or []))

    try:
        script = ExecScript.from_dict(data)
    except Exception as exc:  # noqa: BLE001
        if allow_local_fallback:
            script = compile_case_local(case, cfg)
            (workdir / f"compile_local_fallback_{case_id}.json").write_text(
                json.dumps(script.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return script
        raise CompileError(f"用例 {case_id} 编译结果不符合 DSL: {exc}") from exc

    if allow_local_fallback and _script_has_platform_mismatch(data, cfg):
        script = compile_case_local(case, cfg)
        (workdir / f"compile_local_fallback_{case_id}.json").write_text(
            json.dumps(script.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return script

    return script
