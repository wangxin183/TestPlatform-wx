"""独立 App UI 执行运行时桥接服务 — EXE-xxxx 与 execution_runtime 子进程对接。

与 Project / Pipeline 解耦：任务落盘 storage/execution_runs/，
平台负责导出 task.json、spawn runner、轮询进度、回读 summary/defects 并可落库。
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from execution_runtime.config import load_config
from src.core.database import async_session_factory
from src.core.models.models import Defect, Execution, ExecutionResult, TestCase, TestSuite
from src.services.testcase_automation_lint import (
    LEVEL_MANUAL,
    LEVEL_READY,
    LEVEL_SEMI,
    resolve_automation_level,
)
from src.services.testcase_contract_compiler import prepare_executable_case
from src.utils.analysis_logger import EXE_STORAGE_BASE, ExecutionRunLogger
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STORAGE_BASE = EXE_STORAGE_BASE


def _case_execution_metadata(case: TestCase) -> dict[str, Any]:
    payload = {
        "case_id": str(case.id),
        "title": case.title or "",
        "description": case.description or "",
        "preconditions": case.preconditions or "",
        "steps": case.steps or [],
        "tags": case.tags or [],
        "test_point_id": case.test_point_id or "",
        "automation_level": case.automation_level or "",
        "module": case.module or "",
        "precondition_spec": getattr(case, "precondition_spec", None) or {},
    }
    if case.compile_status:
        return {
            **payload,
            "exec_script": case.exec_script,
            "compile_status": case.compile_status,
            "compile_errors": case.compile_errors or [],
            "execution_mode": case.execution_mode or "hybrid",
            "step_contracts": case.step_contracts or [],
            "assertion_quality": getattr(case, "assertion_quality", None) or "",
            "automation_block_reason": getattr(case, "automation_block_reason", None)
            or "",
        }
    return prepare_executable_case(payload)

_EVENT_PROGRESS: dict[str, int] = {
    "task_loaded": 5,
    "precheck_done": 12,
    "cases_accepted": 15,
    "compile_start": 22,
    "compile_done": 38,
    "pytest_start": 48,
    "pytest_done": 88,
    "allure_generated": 95,
    "run_completed": 100,
    "run_aborted": 100,
}


@dataclass
class ExecutionRunTask:
    run_id: str
    case_ids: list[str] = field(default_factory=list)
    status: str = "queued"  # queued/running/completed/failed/aborted
    current_step: str = "等待开始"
    progress_pct: int = 0
    created_at: str = ""
    completed_at: str = ""
    error_message: str = ""
    execution_id: str = ""  # 落库后的 UUID Execution.id
    summary: dict[str, Any] = field(default_factory=dict)


_task_store: dict[str, ExecutionRunTask] = {}
_store_lock = asyncio.Lock()
_running_procs: dict[str, asyncio.subprocess.Process] = {}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_run_id() -> str:
    STORAGE_BASE.mkdir(parents=True, exist_ok=True)
    max_n = 0
    for d in STORAGE_BASE.iterdir():
        if d.is_dir() and re.match(r"^EXE-\d+$", d.name):
            try:
                max_n = max(max_n, int(d.name.split("-")[1]))
            except ValueError:
                pass
    for rid in _task_store:
        m = re.match(r"^EXE-(\d+)$", rid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"EXE-{max_n + 1:04d}"


def _run_dir(run_id: str) -> Path:
    return STORAGE_BASE / run_id


def _save_task_state(task: ExecutionRunTask) -> None:
    path = _run_dir(task.run_id) / "task_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "run_id": task.run_id,
        "case_ids": task.case_ids,
        "status": task.status,
        "current_step": task.current_step,
        "progress_pct": task.progress_pct,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
        "error_message": task.error_message,
        "execution_id": task.execution_id,
        "summary": task.summary,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_task_state(run_id: str) -> ExecutionRunTask | None:
    path = _run_dir(run_id) / "task_state.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return ExecutionRunTask(
        run_id=data.get("run_id") or run_id,
        case_ids=list(data.get("case_ids") or []),
        status=data.get("status") or "unknown",
        current_step=data.get("current_step") or "",
        progress_pct=int(data.get("progress_pct") or 0),
        created_at=data.get("created_at") or "",
        completed_at=data.get("completed_at") or "",
        error_message=data.get("error_message") or "",
        execution_id=data.get("execution_id") or "",
        summary=dict(data.get("summary") or {}),
    )


def _parse_runtime_log(run_id: str) -> tuple[int, str, str]:
    """从 execution_runtime run.log 解析进度与当前步骤。"""
    log_path = _run_dir(run_id) / "run.log"
    if not log_path.exists():
        return 0, "", ""
    progress = 0
    last_event = ""
    error = ""
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = str(ev.get("event") or "")
            if name in _EVENT_PROGRESS:
                progress = max(progress, _EVENT_PROGRESS[name])
            if name:
                last_event = name
            if name == "run_aborted":
                error = str(ev.get("reason") or "运行中止")
    except Exception:
        pass
    step_labels = {
        "task_loaded": "已加载任务",
        "precheck_start": "环境预检中",
        "precheck_done": "环境预检完成",
        "cases_accepted": "用例校验通过",
        "cases_rejected": "部分用例被拒绝",
        "compile_start": "编译 DSL 中",
        "compile_done": "编译完成",
        "compile_failed": "编译失败",
        "pytest_start": "Appium 执行中",
        "pytest_done": "pytest 执行完成",
        "allure_generated": "报告已生成",
        "run_completed": "运行完成",
        "run_aborted": "运行中止",
    }
    from src.services.narrative_log import narrate

    # 优先用自然语言句，避免前端直接展示英文 event
    last_message = ""
    try:
        for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = str(ev.get("event") or "")
            if not name:
                continue
            last_message = str(ev.get("message") or "") or narrate(
                name, **{k: v for k, v in ev.items() if k != "event"}
            )
            break
    except Exception:
        last_message = ""
    return progress, last_message or step_labels.get(last_event, last_event), error


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


async def _fetch_cases(case_ids: list[str]) -> list[dict[str, Any]]:
    async with async_session_factory() as session:
        q = select(TestCase).where(TestCase.id.in_(case_ids))
        rows = (await session.execute(q)).scalars().all()
        by_id = {str(tc.id): tc for tc in rows}
        out: list[dict[str, Any]] = []
        for cid in case_ids:
            tc = by_id.get(cid)
            if not tc:
                continue
            if str(tc.status or "").lower() != "approved":
                raise ValueError(f"用例 {cid} 状态为 {tc.status}，仅 approved 可执行")
            steps = tc.steps
            if isinstance(steps, str):
                steps = json.loads(steps)
            out.append(
                {
                    "case_id": str(tc.id),
                    "title": tc.title or "",
                    "status": tc.status,
                    "preconditions": tc.preconditions or "",
                    "platform_type": tc.platform_type or "",
                    "test_point_id": tc.test_point_id or "",
                    "steps": steps or [],
                    "module": tc.module or "",
                    "exec_script": tc.exec_script,
                    "compile_status": tc.compile_status or "pending",
                    "compile_errors": tc.compile_errors or [],
                    "execution_mode": tc.execution_mode or "hybrid",
                    "step_contracts": tc.step_contracts or [],
                    "precondition_spec": getattr(tc, "precondition_spec", None) or {},
                    "automation_level": tc.automation_level or "",
                    "assertion_quality": getattr(tc, "assertion_quality", None) or "",
                }
            )
        return out


def _build_task_json(run_id: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    cfg = load_config()
    task: dict[str, Any] = {
        "run_id": run_id,
        "app": {
            "platform": cfg.target_app.platform,
            "bundle_id": cfg.target_app.bundle_id,
        },
        "device": {
            "udid": cfg.device.udid,
            "device_name": cfg.device.device_name,
            "platform_version": cfg.device.platform_version,
            "appium_url": cfg.device.appium_url,
            "automation_name": cfg.device.automation_name,
        },
        "cases": _group_cases_by_module(cases),
    }
    if cfg.target_app.app_activity:
        task["app"]["app_activity"] = cfg.target_app.app_activity
    return task


def _group_cases_by_module(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按前置指纹 + 模块稳定分组。"""
    from src.services.precondition_spec import (
        ensure_precondition_spec,
        precondition_fingerprint,
    )

    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for case in cases:
        spec = ensure_precondition_spec(case)
        case["precondition_spec"] = spec
        key = precondition_fingerprint(spec, str(case.get("module") or ""))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(case)
    return [case for key in order for case in groups[key]]


def _parse_dt(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


async def _import_defects_to_db(task: ExecutionRunTask) -> str:
    """将 runtime 产物落库 Execution + ExecutionResult + Defect，返回 execution_id。"""
    run_dir = _run_dir(task.run_id)
    summary = _read_json(run_dir / "summary.json") or task.summary
    if not summary:
        return ""

    cfg = load_config()
    platform = cfg.target_app.platform or "android"
    total = int(summary.get("total") or 0)
    passed = int(summary.get("passed") or 0)
    failed = int(summary.get("failed") or 0)
    broken = int(summary.get("broken") or 0)
    fail_count = failed + broken

    async with async_session_factory() as session:
        suite = TestSuite(
            name=f"Runtime {task.run_id}",
            description=f"execution_runtime 独立执行 {task.run_id}",
            test_case_ids=task.case_ids,
            project_id=None,
        )
        session.add(suite)
        await session.flush()

        exec_status = "completed" if fail_count == 0 and total > 0 else "failed"
        if total == 0:
            exec_status = "failed"
        execution = Execution(
            test_suite_id=suite.id,
            project_id=None,
            executor_type=platform,
            status=exec_status,
            total_cases=total,
            passed_cases=passed,
            failed_cases=fail_count,
            error_cases=0,
            started_at=_parse_dt(task.created_at),
            completed_at=_parse_dt(task.completed_at),
        )
        session.add(execution)
        await session.flush()

        results_dir = run_dir / "results"
        case_results: dict[str, dict] = {}
        if results_dir.exists():
            for f in results_dir.glob("*.json"):
                data = _read_json(f)
                if isinstance(data, dict):
                    case_results[str(data.get("case_id") or f.stem)] = data

        defects_raw = _read_json(run_dir / "defects.json") or []
        if not isinstance(defects_raw, list):
            defects_raw = []
        defects_by_case = {
            str(d.get("case_id")): d for d in defects_raw if isinstance(d, dict)
        }

        for cid in task.case_ids:
            cr = case_results.get(cid, {})
            outcome = str(cr.get("outcome") or "error")
            status_map = {"passed": "passed", "failed": "failed", "broken": "error"}
            er = ExecutionResult(
                execution_id=execution.id,
                test_case_id=cid,
                status=status_map.get(outcome, "error"),
                duration_ms=float(cr.get("duration_ms") or 0),
                error_message=cr.get("error") or "",
                step_results=cr.get("steps") or [],
                screenshot_path=_first_screenshot(cr),
            )
            session.add(er)
            await session.flush()

            defect_data = defects_by_case.get(cid)
            if defect_data:
                session.add(
                    Defect(
                        execution_result_id=er.id,
                        execution_id=execution.id,
                        project_id=None,
                        title=defect_data.get("title") or f"[{cid}] 执行失败",
                        description=defect_data.get("actual") or "",
                        severity=defect_data.get("severity") or "medium",
                        reproduction_steps=defect_data.get("reproduction_steps") or [],
                        evidence_paths=defect_data.get("evidence_paths") or [],
                        status="open",
                    )
                )

        await session.commit()
        return str(execution.id)


def _first_screenshot(case_result: dict) -> str | None:
    for step in case_result.get("steps") or []:
        for key in ("screenshot_after", "screenshot_before"):
            p = step.get(key)
            if p:
                return str(p)
    return None


async def _run_pipeline(task: ExecutionRunTask) -> None:
    blog = ExecutionRunLogger(task.run_id)
    run_dir = _run_dir(task.run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        task.status = "running"
        task.current_step = "导出 task.json"
        task.progress_pct = 2
        _save_task_state(task)
        blog.log("export_start", case_count=len(task.case_ids))

        cases = await _fetch_cases(task.case_ids)
        if not cases:
            raise ValueError("无有效 approved 用例可执行")

        task_json = _build_task_json(task.run_id, cases)
        task_path = run_dir / "task.json"
        task_path.write_text(
            json.dumps(task_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        blog.log("export_done", path=str(task_path))

        task.current_step = "启动 execution_runtime 子进程"
        task.progress_pct = 4
        _save_task_state(task)

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "execution_runtime.runner",
            "--task",
            str(task_path),
            "--out",
            str(run_dir),
            cwd=str(REPO_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        _running_procs[task.run_id] = proc
        blog.log("subprocess_start", pid=proc.pid)

        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            blog.log("runner_stdout", line=line.decode("utf-8", errors="replace").strip()[:500])
            prog, step, _ = _parse_runtime_log(task.run_id)
            if prog:
                task.progress_pct = max(task.progress_pct, prog)
            if step:
                task.current_step = step
            _save_task_state(task)

        rc = await proc.wait()
        _running_procs.pop(task.run_id, None)
        blog.log("subprocess_done", returncode=rc)

        prog, step, abort_err = _parse_runtime_log(task.run_id)
        task.progress_pct = max(task.progress_pct, prog)
        task.current_step = step or task.current_step
        task.summary = _read_json(run_dir / "summary.json") or {}

        if rc != 0:
            task.status = "failed"
            task.error_message = abort_err or f"runner 退出码 {rc}"
        else:
            total = int((task.summary or {}).get("total") or 0)
            passed = int((task.summary or {}).get("passed") or 0)
            if total > 0 and passed == total:
                task.status = "completed"
            elif total > 0:
                task.status = "completed"
                task.error_message = "部分用例未通过"
            else:
                task.status = "failed"
                task.error_message = "无执行结果"

        task.completed_at = _utcnow()
        task.progress_pct = 100
        _save_task_state(task)

        try:
            task.execution_id = await _import_defects_to_db(task)
            _save_task_state(task)
            blog.log("db_import_done", execution_id=task.execution_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("execution_run_db_import_failed", run_id=task.run_id, error=str(exc))
            blog.log("db_import_failed", error=str(exc)[:300])

    except Exception as exc:  # noqa: BLE001
        logger.error("execution_run_failed", run_id=task.run_id, error=str(exc))
        task.status = "failed"
        task.error_message = str(exc)
        task.completed_at = _utcnow()
        task.progress_pct = 100
        _save_task_state(task)
        blog.log("run_failed", error=str(exc)[:500])
    finally:
        async with _store_lock:
            _task_store[task.run_id] = task


class ExecutionRuntimeService:
    async def get_runtime_config(self) -> dict[str, Any]:
        cfg = load_config()
        return {
            "target_app": {
                "name": cfg.target_app.name,
                "platform": cfg.target_app.platform,
                "bundle_id": cfg.target_app.bundle_id,
                "app_activity": cfg.target_app.app_activity,
            },
            "device": {
                "udid": cfg.device.udid,
                "device_name": cfg.device.device_name,
                "platform_version": cfg.device.platform_version,
                "automation_name": cfg.device.automation_name,
                "appium_url": cfg.device.appium_url,
            },
        }

    async def list_approved_cases(
        self,
        *,
        test_type: str = "ui",
        limit: int = 200,
        include_semi: bool = False,
        include_manual: bool = False,
    ) -> list[dict[str, Any]]:
        """列出可执行用例。默认仅 automation_level=ready（半硬门禁）。

        UI 用例不区分平台，实际运行平台由 execution_runtime 全局配置决定。
        旧用例无字段时按 lint 即时推断。
        """
        allowed = {LEVEL_READY}
        if include_semi:
            allowed.add(LEVEL_SEMI)
        if include_manual:
            allowed.add(LEVEL_MANUAL)

        async with async_session_factory() as session:
            q = select(TestCase).where(TestCase.status == "approved")
            if test_type:
                q = q.where(TestCase.test_type == test_type)
            # 多取一些再按 level 过滤，避免漏掉 ready
            q = q.order_by(TestCase.created_at.desc()).limit(max(limit * 3, 200))
            rows = (await session.execute(q)).scalars().all()
            items: list[dict[str, Any]] = []
            for c in rows:
                metadata = _case_execution_metadata(c)
                level = resolve_automation_level(metadata)
                if level not in allowed:
                    continue
                if metadata["compile_status"] not in {"ok", "agent_required"}:
                    continue
                items.append(
                    {
                        "case_id": str(c.id),
                        "title": c.title or "",
                        "platform_type": c.platform_type or "",
                        "test_type": c.test_type or "",
                        "status": c.status,
                        "generation_id": c.generation_id or "",
                        "test_point_id": c.test_point_id or "",
                        "priority": c.priority or "",
                        "automation_level": level,
                        "module": metadata["module"],
                        "compile_status": metadata["compile_status"],
                        "execution_mode": metadata["execution_mode"],
                    }
                )
                if len(items) >= limit:
                    break
            return items

    async def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        STORAGE_BASE.mkdir(parents=True, exist_ok=True)
        dirs = sorted(
            [d for d in STORAGE_BASE.iterdir() if d.is_dir() and d.name.startswith("EXE-")],
            key=lambda p: p.name,
            reverse=True,
        )
        for d in dirs[:limit]:
            task = _load_task_state(d.name) or _task_store.get(d.name)
            if task:
                items.append(self._serialize_task(task))
            else:
                summary = _read_json(d / "summary.json") or {}
                items.append(
                    {
                        "run_id": d.name,
                        "status": "completed" if summary else "unknown",
                        "case_count": len(summary.get("cases") or []),
                        "passed": summary.get("passed"),
                        "failed": (summary.get("failed") or 0) + (summary.get("broken") or 0),
                    }
                )
        return items

    async def get_task(self, run_id: str) -> ExecutionRunTask | None:
        async with _store_lock:
            if run_id in _task_store:
                return _task_store[run_id]
        return _load_task_state(run_id)

    async def start_run(
        self,
        case_ids: list[str],
        *,
        include_semi: bool = False,
        include_manual: bool = False,
    ) -> str:
        if not case_ids:
            raise ValueError("请至少选择一条用例")
        allowed = {LEVEL_READY}
        if include_semi:
            allowed.add(LEVEL_SEMI)
        if include_manual:
            allowed.add(LEVEL_MANUAL)

        async with async_session_factory() as session:
            q = select(TestCase).where(TestCase.id.in_(case_ids))
            rows = (await session.execute(q)).scalars().all()
            by_id = {str(c.id): c for c in rows}
            accepted: list[str] = []
            rejected: list[str] = []
            for cid in case_ids:
                c = by_id.get(cid)
                if not c:
                    rejected.append(f"{cid}(不存在)")
                    continue
                metadata = _case_execution_metadata(c)
                level = resolve_automation_level(metadata)
                if level not in allowed:
                    rejected.append(f"{(c.title or cid)[:24]}({level})")
                    continue
                if metadata["compile_status"] not in {"ok", "agent_required"}:
                    rejected.append(
                        f"{(c.title or cid)[:24]}({metadata['compile_status']})"
                    )
                    continue
                accepted.append(cid)

        if not accepted:
            raise ValueError(
                "没有可执行用例：默认仅允许 ready。"
                + (" 已拒绝: " + ", ".join(rejected) if rejected else "")
                + " 可勾选「包含半自动」后重试。"
            )
        if rejected:
            logger.info(
                "execution_run_cases_filtered",
                accepted=len(accepted),
                rejected=rejected,
            )

        run_id = _next_run_id()
        task = ExecutionRunTask(
            run_id=run_id,
            case_ids=list(accepted),
            status="queued",
            current_step="排队中",
            created_at=_utcnow(),
        )
        async with _store_lock:
            _task_store[run_id] = task
        _save_task_state(task)
        ExecutionRunLogger(run_id).log(
            "task_created",
            case_ids=accepted,
            rejected=rejected,
            include_semi=include_semi,
        )
        asyncio.create_task(_run_pipeline(task))
        return run_id

    async def get_status(self, run_id: str) -> dict[str, Any]:
        task = await self.get_task(run_id)
        if not task:
            return {"run_id": run_id, "status": "unknown"}
        if task.status == "running":
            prog, step, err = _parse_runtime_log(run_id)
            if prog:
                task.progress_pct = max(task.progress_pct, prog)
            if step:
                task.current_step = step
            if err:
                task.error_message = err
        summary = _read_json(_run_dir(run_id) / "summary.json")
        if summary:
            task.summary = summary
        data = self._serialize_task(task, include_runtime=True)
        # 轮询时也返回自然语言日志，便于前端日志 Tab 实时刷新
        run_dir = _run_dir(run_id)
        platform_logs = ExecutionRunLogger(run_id).read_logs()
        data["logs"] = platform_logs
        data["narrative_logs"] = _collect_narrative_logs(run_dir, platform_logs)
        return data

    async def get_detail(self, run_id: str) -> dict[str, Any]:
        task = await self.get_task(run_id)
        if not task:
            raise ValueError(f"任务 {run_id} 不存在")
        run_dir = _run_dir(run_id)
        data = self._serialize_task(task, include_runtime=True)
        data["env_check"] = _read_json(run_dir / "env_check.json")
        data["defects"] = _read_json(run_dir / "defects.json") or []
        data["logs"] = ExecutionRunLogger(run_id).read_logs()
        data["runtime_logs"] = _read_runtime_logs(run_dir / "run.log")
        data["narrative_logs"] = _collect_narrative_logs(run_dir, data["logs"])
        data["allure_url"] = f"/execution-runs/{run_id}/allure/index.html"

        case_results: list[dict] = []
        results_dir = run_dir / "results"
        if results_dir.exists():
            for f in sorted(results_dir.glob("*.json")):
                cr = _read_json(f)
                if isinstance(cr, dict):
                    case_results.append(cr)
        data["case_results"] = case_results
        return data

    def _serialize_task(self, task: ExecutionRunTask, *, include_runtime: bool = False) -> dict:
        summary = task.summary or _read_json(_run_dir(task.run_id) / "summary.json") or {}
        out = {
            "run_id": task.run_id,
            "case_ids": task.case_ids,
            "case_count": len(task.case_ids),
            "status": task.status,
            "current_step": task.current_step,
            "progress_pct": task.progress_pct,
            "created_at": task.created_at,
            "completed_at": task.completed_at,
            "error_message": task.error_message,
            "execution_id": task.execution_id,
            "summary": {
                "total": summary.get("total"),
                "passed": summary.get("passed"),
                "failed": summary.get("failed"),
                "broken": summary.get("broken"),
                "defects": summary.get("defects"),
            },
        }
        if include_runtime:
            out["allure_url"] = f"/execution-runs/{task.run_id}/allure/index.html"
        return out


def _read_runtime_logs(path: Path) -> list[dict]:
    if not path.exists():
        return []
    logs: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            logs.append(json.loads(line))
        except json.JSONDecodeError:
            logs.append({"raw": line})
    return logs[-200:]


def _collect_narrative_logs(run_dir: Path, platform_logs: list[dict]) -> list[dict]:
    """合并 run/heal/agent/module/平台日志，统一补全中文 message，按时间排序。"""
    from src.services.narrative_log import enrich_log_entry

    merged: list[dict] = []
    for path_name, source in (
        ("run.log", "runtime"),
        ("heal_ledger.jsonl", "heal"),
        ("agent_tool_ledger.jsonl", "agent"),
        ("module_session.jsonl", "session"),
    ):
        path = run_dir / path_name
        for item in _read_runtime_logs(path):
            row = enrich_log_entry(item)
            row["source"] = source
            if "ts" not in row and row.get("timestamp"):
                row["ts"] = row["timestamp"]
            merged.append(row)
    for item in platform_logs or []:
        row = enrich_log_entry(item)
        row["source"] = "platform"
        if "ts" not in row and row.get("timestamp"):
            row["ts"] = row["timestamp"]
        merged.append(row)
    merged.sort(key=lambda x: str(x.get("ts") or ""))
    return merged[-500:]


execution_runtime_svc = ExecutionRuntimeService()
