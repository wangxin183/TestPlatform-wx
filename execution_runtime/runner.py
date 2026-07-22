"""执行运行时入口。

用法（平台子进程调用 / 本地 CLI 单跑）：
  python -m execution_runtime.runner --task <task.json> --out <run_dir>

流程：解析 task → 自动预检 gate → 校验 approved → 逐条编译 → pytest 执行
      → 生成 allure 报告 → 汇总 summary.json + defects.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 允许从仓库根直接 `python -m execution_runtime.runner`
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except Exception:
    pass

from execution_runtime.compiler import CompileError, compile_case  # noqa: E402
from execution_runtime.config import RuntimeConfig, load_config  # noqa: E402
from execution_runtime.env import format_report, run_precheck  # noqa: E402
from execution_runtime.pytest_exec.context import ENV_CONTEXT, ExecContext  # noqa: E402
from execution_runtime.dsl.models import ExecScript  # noqa: E402
from src.services.testcase_contract_compiler import (  # noqa: E402
    repair_step_contract_states,
)
from src.services.testcase_module_catalog import module_catalog  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunLogger:
    """阶段级 JSONL 运行日志（run.log）。"""

    def __init__(self, run_dir: Path) -> None:
        self.path = run_dir / "run.log"
        run_dir.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **kw: Any) -> None:
        from src.services.narrative_log import narrate

        line = {"ts": _now(), "event": event, **kw}
        if not line.get("message"):
            line["message"] = narrate(event, **kw)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        print(
            f"[{line['ts']}] {line.get('message') or event}",
            flush=True,
        )


def _task_to_overrides(task: dict[str, Any]) -> dict[str, Any]:
    """把 task.json 的 app/device/run 段转成 config 覆盖。"""
    overrides: dict[str, Any] = {}
    app = task.get("app") or {}
    if app:
        overrides["target_app"] = {
            k: v for k, v in {
                "platform": app.get("platform"),
                "bundle_id": app.get("bundle_id"),
                "app_path": app.get("app_path"),
                "app_activity": app.get("app_activity"),
            }.items() if v is not None
        }
    dev = task.get("device") or {}
    if dev:
        overrides["device"] = {k: v for k, v in dev.items() if v is not None}
    run = task.get("run") or {}
    if run:
        overrides["run"] = {k: v for k, v in run.items() if v is not None}
    return overrides


def _validate_cases(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """只放行 status==approved 的用例。返回 (valid, rejected_reasons)。"""
    valid: list[dict[str, Any]] = []
    rejected: list[str] = []
    for c in cases:
        cid = str(c.get("case_id") or c.get("id") or "?")
        status = str(c.get("status") or "").lower()
        if status != "approved":
            rejected.append(f"{cid}: status={status or '空'}（要求 approved）")
            continue
        if not c.get("steps"):
            rejected.append(f"{cid}: 无 steps")
            continue
        # 归一 case_id
        c["case_id"] = cid
        valid.append(c)
    return valid, rejected


def _sort_cases_by_module(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """兼容旧名：按前置指纹 + 模块稳定分组。"""
    return _sort_cases_for_execution(cases)


def _sort_cases_for_execution(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 precondition 指纹 + 模块首次出现顺序稳定分组。"""
    from src.services.precondition_spec import (
        ensure_precondition_spec,
        precondition_fingerprint,
    )

    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for case in cases:
        spec = ensure_precondition_spec(case)
        case["precondition_spec"] = spec
        module = str(case.get("module") or "").strip()
        key = precondition_fingerprint(spec, module)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(case)
    return [case for key in order for case in groups[key]]


def _repair_stored_contracts(
    case: dict[str, Any],
    contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return repair_step_contract_states(case, contracts)


async def _compile_all(
    cases: list[dict[str, Any]],
    cfg: RuntimeConfig,
    run_dir: Path,
    run_id: str,
    rlog: RunLogger,
) -> list[str]:
    """编译全部用例，落盘 compiled/<case_id>.json，返回相对路径列表。"""
    compiled_dir = run_dir / "compiled"
    compiled_dir.mkdir(parents=True, exist_ok=True)
    rel_paths: list[str] = []
    for c in _sort_cases_by_module(cases):
        cid = c["case_id"]
        rlog.log("compile_start", case_id=cid, title=(c.get("title") or "")[:60])
        try:
            stored = c.get("exec_script")
            if isinstance(stored, dict) and c.get("compile_status") in {
                "ok",
                "agent_required",
            }:
                data = dict(stored)
                data["case_id"] = cid
                data["module"] = str(c.get("module") or "")
                data["execution_mode"] = str(c.get("execution_mode") or "hybrid")
                data["step_contracts"] = _repair_stored_contracts(
                    c,
                    list(c.get("step_contracts") or []),
                )
                from src.services.precondition_spec import ensure_precondition_spec

                data["precondition_spec"] = ensure_precondition_spec(c)
                module = module_catalog.get(data["module"])
                data["module_setup"] = list(module.entry_steps) if module else []
                script = ExecScript.from_dict(data)
                source = "stored"
            else:
                script = await compile_case(c, cfg, workdir=compiled_dir, run_id=run_id)
                module = module_catalog.get(str(c.get("module") or ""))
                script.module = module.name if module else ""
                script.execution_mode = str(
                    c.get("execution_mode") or "hybrid"
                )  # type: ignore[assignment]
                script.step_contracts = _repair_stored_contracts(
                    c,
                    list(c.get("step_contracts") or []),
                )
                from src.services.precondition_spec import ensure_precondition_spec

                script.precondition_spec = ensure_precondition_spec(c)
                script.module_setup = (
                    [
                        type(script.steps[0]).model_validate(step)
                        for step in module.entry_steps
                    ]
                    if module and module.entry_steps
                    else []
                )
                source = "compiled"
        except CompileError as exc:
            rlog.log("compile_failed", case_id=cid, error=str(exc)[:500])
            continue
        except Exception as exc:  # noqa: BLE001
            rlog.log("compile_failed", case_id=cid, error=str(exc)[:500])
            continue
        out = compiled_dir / f"{cid}.json"
        out.write_text(
            json.dumps(script.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        rel_paths.append(f"compiled/{cid}.json")
        used_fallback = (compiled_dir / f"compile_local_fallback_{cid}.json").exists()
        rlog.log(
            "compile_done",
            case_id=cid,
            step_count=len(script.steps),
            local_fallback=used_fallback,
            source=source,
            module=script.module,
            execution_mode=script.execution_mode,
        )
    return rel_paths


def _run_pytest(run_dir: Path, context_path: Path, rlog: RunLogger) -> int:
    allure_results = run_dir / "allure-results"
    test_file = Path(__file__).parent / "pytest_exec" / "test_execution.py"
    env = dict(os.environ)
    env[ENV_CONTEXT] = str(context_path)
    cmd = [
        sys.executable, "-m", "pytest",
        str(test_file),
        f"--alluredir={allure_results}",
        "-p", "no:cacheprovider",
        "-v",
    ]
    rlog.log("pytest_start", cmd=" ".join(cmd))
    proc = subprocess.run(cmd, env=env, cwd=str(_REPO_ROOT))
    rlog.log("pytest_done", returncode=proc.returncode)
    return proc.returncode


def _generate_allure(run_dir: Path, rlog: RunLogger) -> str:
    """best-effort 生成 allure 静态报告；CLI 缺失/失败则降级保留 results。"""
    allure_cli = shutil.which("allure")
    results = run_dir / "allure-results"
    report = run_dir / "allure-report"
    if allure_cli is None:
        rlog.log("allure_skip", reason="allure CLI 未安装，保留 allure-results")
        return ""
    try:
        proc = subprocess.run(
            [allure_cli, "generate", str(results), "-o", str(report), "--clean"],
            capture_output=True, text=True, timeout=180,
        )
        if proc.returncode == 0:
            rlog.log("allure_generated", report=str(report))
            return str(report)
        rlog.log("allure_failed", stderr=(proc.stderr or "")[:300])
    except Exception as exc:  # noqa: BLE001
        rlog.log("allure_error", error=str(exc)[:300])
    return ""


def _build_summary(run_dir: Path, run_id: str, report_path: str) -> dict[str, Any]:
    results_dir = run_dir / "results"
    cases: list[dict[str, Any]] = []
    if results_dir.exists():
        for f in sorted(results_dir.glob("*.json")):
            try:
                cases.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    passed = sum(1 for c in cases if c.get("outcome") == "passed")
    failed = sum(1 for c in cases if c.get("outcome") == "failed")
    broken = sum(1 for c in cases if c.get("outcome") == "broken")
    healed = sum(int(c.get("healed_count") or 0) for c in cases)
    defects_file = run_dir / "defects.json"
    defect_count = 0
    if defects_file.exists():
        try:
            defect_count = len(json.loads(defects_file.read_text(encoding="utf-8")))
        except Exception:
            defect_count = 0
    summary = {
        "run_id": run_id,
        "total": len(cases),
        "passed": passed,
        "failed": failed,
        "broken": broken,
        "healed": healed,
        "defects": defect_count,
        "allure_report": report_path,
        "generated_at": _now(),
        "cases": [
            {
                "case_id": c.get("case_id"),
                "title": c.get("title"),
                "outcome": c.get("outcome"),
                "duration_ms": c.get("duration_ms"),
            }
            for c in cases
        ],
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


async def main_async(task_path: Path, out_dir: Path) -> int:
    task = json.loads(task_path.read_text(encoding="utf-8"))
    run_id = str(task.get("run_id") or out_dir.name or "EXE-0000")
    run_dir = out_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    rlog = RunLogger(run_dir)
    rlog.log("task_loaded", run_id=run_id, case_count=len(task.get("cases") or []))

    cfg = load_config(_task_to_overrides(task))
    rlog.log(
        "config_resolved",
        app=cfg.target_app.bundle_id,
        device=cfg.device.device_name,
        udid=cfg.device.udid,
        platform_version=cfg.device.platform_version,
    )

    # 0. 自动预检 gate
    precheck = run_precheck(cfg, auto_repair=True)
    (run_dir / "env_check.json").write_text(
        json.dumps(precheck.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(format_report(precheck), flush=True)
    rlog.log("precheck_done", ok=precheck.ok,
             blocking_failures=[i.key for i in precheck.blocking_failures()])
    if not precheck.ok:
        rlog.log("run_aborted", reason="环境预检未通过")
        return 2

    # 1. 校验 approved
    valid, rejected = _validate_cases(task.get("cases") or [])
    if rejected:
        rlog.log("cases_rejected", count=len(rejected), reasons=rejected[:20])
    if not valid:
        rlog.log("run_aborted", reason="无可执行的 approved 用例")
        return 3
    rlog.log("cases_accepted", count=len(valid))

    # 2. 编译
    scripts = await _compile_all(valid, cfg, run_dir, run_id, rlog)
    if not scripts:
        rlog.log("run_aborted", reason="全部用例编译失败")
        return 4

    # 3. 写 context + pytest 执行
    context_path = run_dir / "context.json"
    ExecContext.write(
        context_path,
        run_id=run_id,
        run_dir=run_dir,
        config_overrides=_task_to_overrides(task),
        scripts=scripts,
    )
    _run_pytest(run_dir, context_path, rlog)

    # 4. allure + summary
    report_path = _generate_allure(run_dir, rlog)
    summary = _build_summary(run_dir, run_id, report_path)
    rlog.log("run_completed", **{k: summary[k] for k in
             ("total", "passed", "failed", "broken", "defects")})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="execution_runtime 执行入口")
    parser.add_argument("--task", required=True, help="task.json 路径")
    parser.add_argument("--out", required=True, help="run_dir 输出目录")
    args = parser.parse_args()
    return asyncio.run(main_async(Path(args.task), Path(args.out)))


if __name__ == "__main__":
    raise SystemExit(main())
