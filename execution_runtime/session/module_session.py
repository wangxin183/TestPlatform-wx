"""同模块用例的 Appium 会话复用决策。"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from execution_runtime.config import RuntimeConfig
from execution_runtime.dsl.models import ExecScript, Step
from execution_runtime.engine.executor import StepExecutor
from execution_runtime.engine.executor import StepExecError
from execution_runtime.tools.observation import PageObserver, PageStateMatcher
from execution_runtime.agent_tool_runner import AgentToolRunner, AgentToolRunError
from execution_runtime.tools.gateway import ToolGateway
from src.services.testcase_module_catalog import module_catalog


@dataclass(frozen=True)
class SessionPlan:
    module: str
    previous_module: str
    run_setup: bool
    reuse_session: bool
    module_changed: bool


class ModuleSessionCoordinator:
    def __init__(self) -> None:
        self.current_module = ""

    def plan(self, module: str) -> SessionPlan:
        normalized = str(module or "").strip()
        previous = self.current_module
        same = bool(normalized and normalized == previous)
        changed = bool(previous and normalized != previous)
        plan = SessionPlan(
            module=normalized,
            previous_module=previous,
            run_setup=not same,
            reuse_session=same,
            module_changed=changed,
        )
        self.current_module = normalized
        return plan


def prepare_module_session(
    driver,
    cfg: RuntimeConfig,
    script: ExecScript,
    coordinator: ModuleSessionCoordinator,
    run_dir: Path,
) -> ExecScript:
    """切换模块时运行 setup；同模块复用会话并移除重复启动/退出动作。"""
    plan = coordinator.plan(script.module)
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "case_id": script.case_id,
        "module": script.module,
        "previous_module": plan.previous_module,
        "reuse_session": plan.reuse_session,
        "run_setup": plan.run_setup,
    }
    module = module_catalog.get(script.module)
    state = module.page_states[0] if module and module.page_states else None
    state_matches = True
    if state is not None:
        state_matches = PageStateMatcher().match(
            state, PageObserver(driver).observe()
        ).matched
    run_setup = plan.run_setup or not state_matches

    if plan.reuse_session and state_matches:
        event["event"] = "skip_setup"
    elif run_setup:
        event["event"] = "module_setup_start"
        if plan.reuse_session and not state_matches:
            event["state_drift"] = True
        driver.activate_app(cfg.target_app.bundle_id)
        if state is not None and script.execution_mode in {"hybrid", "agent"}:
            try:
                result = asyncio.run(
                    AgentToolRunner(
                        ToolGateway(driver, cfg),
                        run_dir=run_dir,
                    ).navigate_to_module(
                        script.module,
                        case_id=script.case_id,
                    )
                )
                event["event"] = "module_navigation_succeeded"
                event["agent_calls"] = result.call_count
                event["navigation_message"] = result.message
            except AgentToolRunError as exc:
                event["event"] = "module_navigation_failed"
                event["reason"] = str(exc)
                _write_session_event(run_dir, event)
                raise StepExecError(
                    f"模块 {script.module} 智能入口失败: {exc}",
                    kind="broken",
                ) from exc
        else:
            executor = StepExecutor(driver, cfg)
            setup_error: Exception | None = None
            try:
                for step in script.module_setup:
                    executor.execute(step)
            except Exception:
                # 纯确定性模式仅重启并重试一次。
                try:
                    driver.terminate_app(cfg.target_app.bundle_id)
                    driver.activate_app(cfg.target_app.bundle_id)
                    for step in script.module_setup:
                        executor.execute(step)
                    event["recovered"] = True
                except Exception as exc:  # noqa: BLE001
                    setup_error = exc
            matched = (
                PageStateMatcher().match(state, PageObserver(driver).observe())
                if state is not None
                else None
            )
            if setup_error is not None or (matched is not None and not matched.matched):
                event["event"] = "module_setup_failed"
                event["reason"] = str(
                    setup_error or (matched.reason if matched is not None else "")
                )
                _write_session_event(run_dir, event)
                raise StepExecError(
                    f"模块 {script.module} 确定性入口失败: {event['reason']}",
                    kind="broken",
                )

    _write_session_event(run_dir, event)
    effective = script.model_copy(deep=True)
    if script.module:
        effective.steps = [
            step
            for step in effective.steps
            if step.action not in {"launch_app", "terminate_app"}
        ]
        if not effective.steps:
            effective.steps = [
                Step(
                    action="screenshot",
                    description="模块页面执行留证",
                    expected="",
                )
            ]
    return effective


def _write_session_event(run_dir: Path, event: dict) -> None:
    from src.services.narrative_log import narrate

    payload = dict(event)
    if not payload.get("message"):
        event_name = str(payload.get("event") or "")
        narrate_kwargs = {k: v for k, v in payload.items() if k != "event"}
        payload["message"] = narrate(event_name, **narrate_kwargs)
    path = run_dir / "module_session.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
