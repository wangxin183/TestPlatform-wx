"""动态参数化执行：每条已编译用例 = 一个 pytest test。

由 runner 通过 subprocess 调起：
  EXEC_RUNTIME_CONTEXT=<run_dir>/context.json \
    pytest execution_runtime/pytest_exec/test_execution.py \
    --alluredir=<run_dir>/allure-results

每条用例执行后：写 results/<case_id>.json；失败自动记缺陷；assert 让 allure 标红。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from execution_runtime.case_runner import run_case
from execution_runtime.defect import append_defect, build_defect
from execution_runtime.dsl.models import ExecScript
from execution_runtime.heal import heal_run
from execution_runtime.models.result import StepOutcome
from execution_runtime.pytest_exec.context import ExecContext
from execution_runtime.session.module_session import prepare_module_session
from execution_runtime.setup import (
    PreconditionSetupError,
    run_entry_setup,
    run_login_setup,
)
from execution_runtime.tools.gateway import ToolGateway
from src.services.heal_loop import HealScope
from src.services.precondition_spec import (
    ensure_precondition_spec,
    entry_fingerprint,
    login_fingerprint,
    precondition_fingerprint,
)

try:
    import allure  # type: ignore
except Exception:  # noqa: BLE001
    allure = None


def _load_scripts() -> list[ExecScript]:
    ctx = ExecContext.load()
    scripts: list[ExecScript] = []
    for f in ctx.script_files:
        if not f.exists():
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        scripts.append(ExecScript.from_dict(data))
    return scripts


_SCRIPTS = _load_scripts()


def _idfn(script: ExecScript) -> str:
    return f"{script.case_id}:{(script.title or '')[:20]}"


def _raise_if_case_failed(result) -> None:
    if result.outcome in (StepOutcome.FAILED, StepOutcome.BROKEN):
        raise RuntimeError(result.error or f"case {result.outcome.value}")


@pytest.mark.parametrize("script", _SCRIPTS, ids=[_idfn(s) for s in _SCRIPTS])
def test_case(
    script: ExecScript,
    driver,
    runtime_config,
    exec_context: ExecContext,
    module_session,
    precondition_setup_state,
):
    run_dir = exec_context.run_dir
    if allure is not None:
        allure.dynamic.title(script.title or script.case_id)
        if script.test_point_id:
            allure.dynamic.tag(script.test_point_id)

    spec = ensure_precondition_spec(
        {
            "precondition_spec": script.precondition_spec,
            "module": script.module,
            "title": script.title,
            "preconditions": script.preconditions,
        }
    )
    login_fp = login_fingerprint(spec)
    entry_fp = entry_fingerprint(spec, script.module)
    fp = precondition_fingerprint(spec, script.module)
    need_login = precondition_setup_state.get("login_fp") != login_fp
    need_entry = precondition_setup_state.get("entry_fp") != entry_fp
    gateway = ToolGateway(driver, runtime_config)

    async def _setup_attempt():
        warnings: list[str] = []
        payload = {
            "precondition_spec": spec,
            "module": script.module,
            "title": script.title,
            "preconditions": script.preconditions,
        }
        if need_login:
            setup_login = run_login_setup(
                gateway, payload, module=script.module
            )
            precondition_setup_state["login_fp"] = login_fp
            warnings.extend(setup_login.warnings)
        if need_entry:
            setup_entry = run_entry_setup(
                gateway, payload, module=script.module
            )
            precondition_setup_state["entry_fp"] = entry_fp
            warnings.extend(setup_entry.warnings)
        precondition_setup_state["fingerprint"] = fp
        precondition_setup_state["warnings"] = warnings
        return warnings

    if need_login or need_entry:
        try:
            try:
                asyncio.run(_setup_attempt())
            except PreconditionSetupError as exc:
                asyncio.run(
                    heal_run(
                        scope=HealScope.SETUP,
                        cfg=runtime_config,
                        gateway=gateway,
                        run_dir=run_dir,
                        attempt_fn=_setup_attempt,
                        case_id=script.case_id,
                        module=script.module,
                        initial_error=exc,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"PRECONDITION_SETUP_FAILED: {exc}", pytrace=False)

    async def _session_attempt():
        return prepare_module_session(
            driver,
            runtime_config,
            script,
            module_session,
            run_dir,
        )

    try:
        try:
            effective_script = prepare_module_session(
                driver,
                runtime_config,
                script,
                module_session,
                run_dir,
            )
        except Exception as nav_exc:  # noqa: BLE001
            effective_script = asyncio.run(
                heal_run(
                    scope=HealScope.SETUP,
                    cfg=runtime_config,
                    gateway=gateway,
                    run_dir=run_dir,
                    attempt_fn=_session_attempt,
                    case_id=script.case_id,
                    module=script.module,
                    initial_error=nav_exc,
                )
            )
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"MODULE_SESSION_FAILED: {exc}", pytrace=False)

    result = run_case(driver, effective_script, runtime_config, run_dir)

    if (
        result.outcome in (StepOutcome.FAILED, StepOutcome.BROKEN)
        and runtime_config.run.self_heal_enabled
    ):
        async def _case_attempt():
            sess = prepare_module_session(
                driver,
                runtime_config,
                script,
                module_session,
                run_dir,
            )
            case_result = run_case(driver, sess, runtime_config, run_dir)
            _raise_if_case_failed(case_result)
            return case_result

        try:
            result = asyncio.run(
                heal_run(
                    scope=HealScope.CASE,
                    cfg=runtime_config,
                    gateway=gateway,
                    run_dir=run_dir,
                    attempt_fn=_case_attempt,
                    case_id=script.case_id,
                    module=script.module,
                    initial_error=RuntimeError(result.error or "case failed"),
                )
            )
        except Exception:
            # 自愈耗尽：保留首轮 result 记缺陷
            pass

    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / f"{script.case_id}.json").write_text(
        json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if result.outcome in (StepOutcome.FAILED, StepOutcome.BROKEN):
        defect = build_defect(result)
        append_defect(run_dir, defect)
        pytest.fail(
            f"用例 {script.case_id} 执行失败({result.outcome.value}): {result.error}",
            pytrace=False,
        )
