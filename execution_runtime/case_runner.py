"""单条用例执行编排：executor + recorder + 缺陷 + allure 附件。

被 pytest 测试调用；返回 CaseResult。执行阶段不调 LLM（自愈为 P1）。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

from execution_runtime.config import RuntimeConfig
from execution_runtime.dsl.models import Step, ExecScript
from execution_runtime.engine.executor import StepExecError, StepExecutor
from execution_runtime.models.result import CaseResult, StepOutcome, StepRecord
from execution_runtime.recorder import StepRecorder
from execution_runtime.agent_tool_runner import AgentToolRunError, AgentToolRunner
from execution_runtime.tools.gateway import ToolGateway
from execution_runtime.tools.observation import PageObserver, PageStateMatcher
from src.services.testcase_module_catalog import module_catalog

TRANSITION_WAIT_SECONDS = 8.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _allure():
    try:
        import allure  # type: ignore

        return allure
    except Exception:
        return None


def _attach_file(path: str, name: str, mime: str) -> None:
    allure = _allure()
    if allure is None or not path:
        return
    p = Path(path)
    if not p.exists():
        return
    try:
        allure.attach.file(str(p), name=name, attachment_type=mime)
    except Exception:
        pass


def _locator_desc(step: Step) -> tuple[str, str]:
    if step.locator is not None:
        return step.locator.type, step.locator.value
    return "", ""


def _wait_for_expected_transition(
    driver,
    module_name: str,
    contract: dict,
    before,
) -> bool:
    transition = str(contract.get("expected_transition") or "")
    if "->" not in transition:
        return True
    start_state, destination = [part.strip() for part in transition.rsplit("->", 1)]
    if not destination or destination == start_state:
        return True
    definition = module_catalog.get(module_name)
    target_state = next(
        (
            state
            for state in (definition.page_states if definition else [])
            if state.id == destination
        ),
        None,
    )
    deadline = time.monotonic() + TRANSITION_WAIT_SECONDS
    while True:
        after = PageObserver(driver).observe()
        if target_state is not None:
            if PageStateMatcher().match(target_state, after).matched:
                return True
        elif (
            after.package != before.package
            or after.activity != before.activity
            or after.source_hash != before.source_hash
        ):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def run_case(
    driver,
    script: ExecScript,
    cfg: RuntimeConfig,
    run_dir: Path,
) -> CaseResult:
    """执行一条 ExecScript，逐步留痕，返回 CaseResult。"""
    allure = _allure()
    executor = StepExecutor(driver, cfg)
    recorder = StepRecorder(
        driver,
        run_dir,
        script.case_id,
        redact_keys=cfg.redact_keys,
        screenshot_each_step=cfg.run.screenshot_each_step,
        dump_source_each_step=cfg.run.dump_source_each_step,
    )

    result = CaseResult(
        case_id=script.case_id,
        title=script.title,
        test_point_id=script.test_point_id,
        started_at=_now(),
    )
    case_start = time.time()
    failed = False
    agent_runner = (
        AgentToolRunner(ToolGateway(driver, cfg), run_dir=run_dir)
        if script.execution_mode in {"hybrid", "agent"}
        else None
    )

    for idx, step in enumerate(script.steps, start=1):
        ltype, lvalue = _locator_desc(step)
        rec = StepRecord(
            step_no=idx,
            action=step.action,
            description=step.description,
            expected=step.expected,
            locator_type=ltype,
            locator_value=lvalue,
            started_at=_now(),
        )
        step_start = time.time()
        shot_before = recorder.capture_screenshot(idx, "before")
        rec.screenshot_before = shot_before

        step_ctx = (
            allure.step(f"步骤{idx}: {step.description or step.action}")
            if allure else _NullCtx()
        )
        try:
            with step_ctx:
                contract = (
                    script.step_contracts[idx - 1]
                    if idx - 1 < len(script.step_contracts)
                    else None
                )
                if script.execution_mode == "agent" and not contract:
                    raise AgentToolRunError(
                        f"Agent 模式步骤 {idx} 缺少 step_contract"
                    )
                if script.execution_mode == "agent" and contract and agent_runner:
                    agent_result = asyncio.run(
                        agent_runner.run_step(
                            contract,
                            module=script.module,
                            case_id=script.case_id,
                        )
                    )
                    rec.matched_by = f"agent:{agent_result.last_tool}"
                    rec.healed = True
                    rec.heal_note = agent_result.message
                    result.healed_count += 1
                else:
                    try:
                        before = PageObserver(driver).observe() if contract else None
                        matched = executor.execute(step)
                        if (
                            contract
                            and before is not None
                            and not _wait_for_expected_transition(
                                driver,
                                script.module,
                                contract,
                                before,
                            )
                        ):
                            raise StepExecError(
                                "操作已执行，但未观察到 expected_transition",
                                kind="broken",
                            )
                        rec.matched_by = matched
                    except StepExecError:
                        if not (agent_runner and contract):
                            raise
                        agent_result = asyncio.run(
                            agent_runner.run_step(
                                contract,
                                module=script.module,
                                case_id=script.case_id,
                            )
                        )
                        rec.matched_by = f"agent:{agent_result.last_tool}"
                        rec.healed = True
                        rec.heal_note = agent_result.message
                        result.healed_count += 1
                rec.outcome = StepOutcome.PASSED
        except StepExecError as exc:
            rec.outcome = (
                StepOutcome.FAILED if exc.kind == "failed" else StepOutcome.BROKEN
            )
            rec.error = str(exc)
            failed = True
        except AgentToolRunError as exc:
            rec.outcome = StepOutcome.BROKEN
            rec.error = f"Agent 工具执行失败: {exc}"
            failed = True
        except Exception as exc:  # noqa: BLE001
            rec.outcome = StepOutcome.BROKEN
            rec.error = f"未预期异常: {exc}"
            failed = True

        rec.screenshot_after = recorder.capture_screenshot(idx, "after")
        rec.page_source = recorder.capture_source(idx)
        rec.duration_ms = int((time.time() - step_start) * 1000)
        rec.ended_at = _now()
        recorder.write_step(rec.as_dict())

        # allure 附件
        _attach_file(rec.screenshot_before, f"step{idx}-before", _png())
        _attach_file(rec.screenshot_after, f"step{idx}-after", _png())
        _attach_file(rec.page_source, f"step{idx}-source.xml", _xml())

        result.steps.append(rec)
        if failed:
            result.outcome = rec.outcome
            result.error = rec.error
            break

    if not failed:
        result.outcome = StepOutcome.PASSED
    result.ended_at = _now()
    result.duration_ms = int((time.time() - case_start) * 1000)
    return result


def _png():
    try:
        import allure

        return allure.attachment_type.PNG
    except Exception:
        return None


def _xml():
    try:
        import allure

        return allure.attachment_type.XML
    except Exception:
        return None


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
