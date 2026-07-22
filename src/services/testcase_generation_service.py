"""独立用例生成服务 — 从需求分析 UI 测试点生成测试用例。

与 Project / Pipeline 解耦：任务落盘 storage/testcase_generations/，
用例写入 test_cases 表（project_id=NULL）。

智能体调用硬约束（见 docs/agent_runtime.md）：
- 只通过 agent_runtime.run(AgentTask(role="testcase.generator")) 调用
- 禁止直接 subprocess / SDK / 新建独立 Agent 类
- primary/fallbacks 完全由 config/settings.yaml → agent_runtime.roles 驱动
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from src.agent_runtime import AgentTask, agent_runtime
from src.agent_runtime.cli_shared import (
    dynamic_timeout,
    estimate_tokens,
    extract_json,
    recover_json_from_workdir,
)
from src.core.database import async_session_factory
from src.core.models.models import TestCase
from src.core.config import settings
from src.llm.prompts.skill_loader import load_skill
from src.services.testcase_automation_lint import lint_case
from src.services.testcase_compile_advisor import (
    advise_compile_case,
    advise_prepared_cases,
)
from src.services.testcase_contract_compiler import prepare_executable_case
from src.services.testcase_module_catalog import module_catalog
from src.services.testcase_coverage import (
    build_fr_summaries_for_batch,
    build_slim_skill_instructions,
    compress_test_point,
    pack_tp_batches_by_tokens,
    validate_ui_case_coverage,
)
from src.utils.analysis_logger import GenerationLogger, TCG_STORAGE_BASE, RA_STORAGE_BASE
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

STORAGE_BASE = TCG_STORAGE_BASE
RA_BASE = RA_STORAGE_BASE
ROLE_GENERATOR = "testcase.generator"
SKILL_NAME = "ui-testcase-from-testpoint"


@dataclass
class GenerationTask:
    generation_id: str
    analysis_id: str = ""
    platform_type: str = ""
    custom_prompt: str = ""
    selected_tp_ids: list[str] = field(default_factory=list)
    status: str = "queued"
    current_step: str = "等待开始"
    progress_pct: int = 0
    case_ids: list[str] = field(default_factory=list)
    total_cases: int = 0
    created_at: str = ""
    completed_at: str = ""
    error_message: str = ""


_task_store: dict[str, GenerationTask] = {}
_store_lock = asyncio.Lock()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_generation_id() -> str:
    STORAGE_BASE.mkdir(parents=True, exist_ok=True)
    max_n = 0
    for d in STORAGE_BASE.iterdir():
        if d.is_dir() and re.match(r"^TCG-\d+$", d.name):
            try:
                max_n = max(max_n, int(d.name.split("-")[1]))
            except ValueError:
                pass
    for tid in _task_store:
        m = re.match(r"^TCG-(\d+)$", tid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"TCG-{max_n + 1:04d}"


def _save_task_state(task: GenerationTask) -> None:
    try:
        path = STORAGE_BASE / task.generation_id / "task_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "generation_id": task.generation_id,
            "analysis_id": task.analysis_id,
            "platform_type": task.platform_type,
            "custom_prompt": task.custom_prompt,
            "selected_tp_ids": task.selected_tp_ids,
            "status": task.status,
            "current_step": task.current_step,
            "progress_pct": task.progress_pct,
            "case_ids": task.case_ids,
            "total_cases": task.total_cases,
            "created_at": task.created_at,
            "completed_at": task.completed_at,
            "error_message": task.error_message,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(
            "generation_task_state_save_failed",
            generation_id=task.generation_id,
            error=str(exc),
        )


def _load_ra_analysis_json(analysis_id: str) -> dict | None:
    path = RA_BASE / analysis_id / f"{analysis_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_ra_task_state(analysis_id: str) -> dict:
    path = RA_BASE / analysis_id / "task_state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _filter_ui_test_points(analysis_json: dict) -> list[dict]:
    tps = analysis_json.get("test_points") or []
    result = []
    for tp in tps:
        if not isinstance(tp, dict):
            continue
        tt = str(tp.get("test_type") or "").strip().lower()
        if tt == "ui":
            result.append(tp)
    return result


def _normalize_case(raw: dict, platform_type: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    steps = raw.get("steps")
    if not title or not isinstance(steps, list) or not steps:
        return None
    norm_steps = []
    for i, s in enumerate(steps, start=1):
        if not isinstance(s, dict):
            continue
        action = str(s.get("action") or "").strip()
        expected = str(s.get("expected") or "").strip()
        if not action or not expected:
            continue
        norm_steps.append({
            "step": int(s.get("step") or i),
            "action": action,
            "expected": expected,
        })
    if not norm_steps:
        return None
    declared_module = str(raw.get("module") or "").strip()
    module = module_catalog.resolve(declared_module)
    if not module:
        module = module_catalog.resolve(
            " ".join(
                [
                    title,
                    str(raw.get("description") or ""),
                    str(raw.get("preconditions") or ""),
                ]
            )
        )
    contracts = (
        [item for item in raw.get("step_contracts") if isinstance(item, dict)]
        if isinstance(raw.get("step_contracts"), list)
        else []
    )
    norm_steps, contracts = _strip_module_navigation_prefix(
        module,
        norm_steps,
        contracts,
    )
    if not norm_steps:
        return None
    preconditions, norm_steps = _repair_generated_case_steps(
        str(raw.get("preconditions") or ""),
        norm_steps,
    )
    tp_id = str(raw.get("test_point_id") or "").strip()
    priority = str(raw.get("priority") or "中").strip()
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    level = str(raw.get("automation_level") or "").strip().lower()
    if level not in {"ready", "semi", "manual"}:
        level = ""
    from src.services.precondition_spec import ensure_precondition_spec

    spec = ensure_precondition_spec(
        {
            "precondition_spec": raw.get("precondition_spec"),
            "preconditions": preconditions,
            "module": module,
            "title": title,
        }
    )
    return {
        "title": title[:500],
        "description": str(raw.get("description") or ""),
        "preconditions": preconditions,
        "steps": norm_steps,
        "priority": priority,
        "test_type": "ui",
        "tags": [str(t) for t in tags],
        "platform_type": str(raw.get("platform_type") or platform_type or "general"),
        "test_point_id": tp_id,
        "related_fr": str(raw.get("related_fr") or ""),
        "module": module,
        "automation_level": level,
        "precondition_spec": spec,
        "exec_script": raw.get("exec_script") if isinstance(raw.get("exec_script"), dict) else None,
        "step_contracts": contracts,
    }


def _repair_generated_case_steps(
    preconditions: str,
    steps: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """保守修复生成器常见措辞，不臆造业务目标或测试数据。"""
    conditions = [part.strip() for part in re.split(r"[；\n]", preconditions) if part.strip()]
    repaired: list[dict[str, Any]] = []
    for raw in steps:
        action = str(raw.get("action") or "").strip()
        expected = str(raw.get("expected") or "").strip()

        login = re.match(r"^(?:使用|用)(.+?账号)登录\s*App$", action, re.IGNORECASE)
        if login and len(steps) > 1:
            condition = f"已使用{login.group(1)}登录 App"
            if condition not in conditions:
                conditions.append(condition)
            continue

        enter_tab = re.match(r"^进入\s*(.+?)\s*tab$", action, re.IGNORECASE)
        if enter_tab:
            action = f"点击底部「{enter_tab.group(1).strip()}」tab"

        observe = re.match(r"^(?:观察|查看)\s*(.+)$", action)
        if observe:
            target = observe.group(1).strip()
            action = f"确认{target}" if target.endswith("可见") else f"确认{target}可见"

        quoted_tap = re.match(r"^点击\s*(「[^」]+」)$", action)
        if quoted_tap:
            action = f"点击{quoted_tap.group(1)}按钮"

        repaired.append(
            {
                "step": len(repaired) + 1,
                "action": action,
                "expected": expected,
            }
        )
    return "；".join(conditions), repaired


def _strip_module_navigation_prefix(
    module_name: str,
    steps: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """移除用例开头误生成的「进入模块」前缀。

    规则：
    1. 若第 1 步已在模块主状态开始，说明业务已在模块内，中途返回不算入口，不剥。
    2. 否则找到首次「模块外 → 主状态」的落地步，剥掉此前（含落地）的导航前缀。
    3. 若剥完后无剩余业务步，保留原步骤，避免 TP 覆盖被误杀。
    """
    definition = module_catalog.get(module_name)
    if definition is None or not definition.page_states or not contracts:
        return steps, contracts
    target_state = definition.page_states[0].id
    limited = contracts[: len(steps)]
    if not limited:
        return steps, contracts
    first_start = str(limited[0].get("start_state") or "").strip()
    if first_start == target_state:
        return steps, contracts

    cutoff = 0
    for index, contract in enumerate(limited):
        transition = str(contract.get("expected_transition") or "")
        destination = (
            transition.rsplit("->", 1)[-1].strip() if "->" in transition else ""
        )
        start_state = str(contract.get("start_state") or "").strip()
        if (
            destination == target_state
            and start_state
            and start_state != target_state
        ):
            cutoff = index + 1
            break
    if not cutoff or cutoff >= len(steps):
        return steps, contracts
    remaining_steps = [dict(item) for item in steps[cutoff:]]
    remaining_contracts = [dict(item) for item in contracts[cutoff:]]
    if not remaining_steps:
        return steps, contracts
    for index, step in enumerate(remaining_steps, start=1):
        step["step"] = index
    for index, contract in enumerate(remaining_contracts, start=1):
        contract["step"] = index
    return remaining_steps, remaining_contracts


def _extract_cases_from_output(raw_output: str, workdir: Path) -> tuple[list[dict], str]:
    """返回 (cases, extract_method)。"""
    result = extract_json(raw_output)
    data = result.data if result.success else None
    method = result.extract_method if result.success else ""

    if data is None:
        recovered = recover_json_from_workdir(str(workdir), raw_output=raw_output)
        if recovered.success:
            data = recovered.data
            method = recovered.extract_method or "workdir_recover"

    cases: list[dict] = []
    if isinstance(data, list):
        cases = [c for c in data if isinstance(c, dict)]
    elif isinstance(data, dict):
        for key in ("test_cases", "cases", "data"):
            if isinstance(data.get(key), list):
                cases = [c for c in data[key] if isinstance(c, dict)]
                break
        if not cases and data.get("title") and data.get("steps"):
            cases = [data]

    return cases, method or "unknown"


class TestCaseGenerationService:
    """用例生成编排服务。"""

    def __init__(self) -> None:
        self._store_lock = _store_lock
        restored = self._scan_storage()
        if restored:
            _task_store.update(restored)
            logger.info("generation_tasks_restored", count=len(restored))

    def _scan_storage(self) -> dict[str, GenerationTask]:
        restored: dict[str, GenerationTask] = {}
        if not STORAGE_BASE.exists():
            return restored
        for d in STORAGE_BASE.iterdir():
            if not d.is_dir() or not d.name.startswith("TCG-"):
                continue
            state_file = d / "task_state.json"
            if not state_file.exists():
                continue
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                task = GenerationTask(
                    generation_id=data.get("generation_id", d.name),
                    analysis_id=data.get("analysis_id", ""),
                    platform_type=data.get("platform_type", ""),
                    custom_prompt=data.get("custom_prompt", ""),
                    selected_tp_ids=data.get("selected_tp_ids") or [],
                    status=data.get("status", "failed"),
                    current_step=data.get("current_step", ""),
                    progress_pct=data.get("progress_pct", 0),
                    case_ids=data.get("case_ids") or [],
                    total_cases=data.get("total_cases", 0),
                    created_at=data.get("created_at", ""),
                    completed_at=data.get("completed_at", ""),
                    error_message=data.get("error_message", ""),
                )
                # 中断的 processing 标记为 failed，避免假运行
                if task.status in ("queued", "processing"):
                    task.status = "failed"
                    task.current_step = "服务重启，任务中断"
                    task.error_message = task.error_message or "服务重启导致生成中断，请重新创建任务"
                    _save_task_state(task)
                restored[task.generation_id] = task
            except Exception as exc:
                logger.warning(
                    "generation_task_restore_failed",
                    path=str(d),
                    error=str(exc),
                )
        return restored

    # ---- 来源 RA ----

    async def list_source_analyses(self) -> list[dict]:
        """列出含 UI 测试点的需求分析任务。"""
        items: list[dict] = []
        if not RA_BASE.exists():
            return items
        for d in sorted(RA_BASE.iterdir(), reverse=True):
            if not d.is_dir() or not d.name.startswith("RA-"):
                continue
            analysis = _load_ra_analysis_json(d.name)
            if not analysis:
                continue
            ui_tps = _filter_ui_test_points(analysis)
            if not ui_tps:
                continue
            state = _load_ra_task_state(d.name)
            items.append({
                "analysis_id": d.name,
                "filename": state.get("filename", ""),
                "status": state.get("status", "unknown"),
                "platform_type": state.get("platform_type", ""),
                "ui_tp_count": len(ui_tps),
                "tp_count": len(analysis.get("test_points") or []),
                "created_at": state.get("created_at", ""),
                "completed_at": state.get("completed_at", ""),
            })
        return items

    async def get_ui_test_points(self, analysis_id: str) -> dict:
        analysis = _load_ra_analysis_json(analysis_id)
        if not analysis:
            return {"success": False, "error": f"未找到分析结果: {analysis_id}"}
        ui_tps = _filter_ui_test_points(analysis)
        fr_map = {
            fr.get("id"): fr
            for fr in (analysis.get("functional_requirements") or [])
            if isinstance(fr, dict) and fr.get("id")
        }
        enriched = []
        for tp in ui_tps:
            item = dict(tp)
            rid = tp.get("related_fr")
            if rid and rid in fr_map:
                fr = fr_map[rid]
                item["fr_summary"] = {
                    "id": fr.get("id"),
                    "module": fr.get("module"),
                    "description": fr.get("description"),
                    "priority": fr.get("priority"),
                }
            enriched.append(item)
        return {
            "success": True,
            "analysis_id": analysis_id,
            "ui_test_points": enriched,
            "count": len(enriched),
        }

    # ---- 任务 CRUD ----

    async def start_generation(
        self,
        analysis_id: str,
        test_point_ids: list[str],
        platform_type: str = "",
        custom_prompt: str = "",
    ) -> str:
        analysis = _load_ra_analysis_json(analysis_id)
        if not analysis:
            raise ValueError(f"未找到分析结果: {analysis_id}")

        ui_tps = _filter_ui_test_points(analysis)
        ui_map = {str(tp.get("id")): tp for tp in ui_tps if tp.get("id")}
        selected_ids = [str(x).strip() for x in test_point_ids if str(x).strip()]
        if not selected_ids:
            raise ValueError("请至少选择一个 UI 测试点")
        missing = [x for x in selected_ids if x not in ui_map]
        if missing:
            raise ValueError(f"以下测试点不是 UI 类型或不存在: {', '.join(missing[:10])}")

        ra_state = _load_ra_task_state(analysis_id)
        platform = platform_type or ra_state.get("platform_type") or "general"

        async with self._store_lock:
            generation_id = _next_generation_id()
            task = GenerationTask(
                generation_id=generation_id,
                analysis_id=analysis_id,
                platform_type=platform,
                custom_prompt=custom_prompt or "",
                selected_tp_ids=selected_ids,
                status="queued",
                current_step="任务已创建",
                progress_pct=5,
                created_at=_utcnow(),
            )
            _task_store[generation_id] = task
            _save_task_state(task)

        glog = GenerationLogger(generation_id)
        glog.log(
            "task_created",
            analysis_id=analysis_id,
            tp_ids=selected_ids,
            tp_count=len(selected_ids),
            platform_type=platform,
            custom_prompt=(custom_prompt or "")[:200],
        )

        selected_tps = [ui_map[i] for i in selected_ids]
        glog.save_json("selected_test_points.json", selected_tps)

        asyncio.create_task(
            self._run_pipeline(
                generation_id,
                analysis,
                selected_tps,
                platform,
                custom_prompt or "",
            )
        )
        logger.info(
            "generation_task_started",
            generation_id=generation_id,
            analysis_id=analysis_id,
            tp_count=len(selected_ids),
        )
        return generation_id

    async def get_task(self, generation_id: str) -> GenerationTask | None:
        async with self._store_lock:
            return _task_store.get(generation_id)

    async def list_tasks(self, status: str = "", page: int = 1, size: int = 50) -> tuple[list[GenerationTask], int]:
        async with self._store_lock:
            tasks = list(_task_store.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        tasks.sort(key=lambda t: t.created_at or "", reverse=True)
        total = len(tasks)
        start = max(0, (page - 1) * size)
        return tasks[start : start + size], total

    async def get_task_cases(self, generation_id: str) -> list[dict]:
        async with async_session_factory() as db:
            result = await db.execute(
                select(TestCase).where(TestCase.generation_id == generation_id)
            )
            cases = result.scalars().all()
        return [_serialize_db_case(c) for c in cases]

    # ---- 评审 ----

    async def update_case(
        self,
        generation_id: str,
        case_id: str,
        updates: dict[str, Any],
    ) -> dict:
        async with async_session_factory() as db:
            tc = await db.get(TestCase, case_id)
            if not tc or tc.generation_id != generation_id:
                return {"success": False, "error": "用例不存在或不属于该生成任务"}
            for key in (
                "title",
                "description",
                "preconditions",
                "priority",
                "platform_type",
                "module",
            ):
                if key in updates and updates[key] is not None:
                    setattr(tc, key, updates[key])
            if "steps" in updates and isinstance(updates["steps"], list):
                tc.steps = updates["steps"]
            if "tags" in updates and isinstance(updates["tags"], list):
                tc.tags = updates["tags"]
            prepared = prepare_executable_case(
                {
                    "case_id": str(tc.id),
                    "title": tc.title,
                    "description": tc.description or "",
                    "preconditions": tc.preconditions or "",
                    "steps": tc.steps or [],
                    "tags": tc.tags or [],
                    "test_point_id": tc.test_point_id or "",
                    "module": tc.module or "",
                    "automation_level": tc.automation_level or "",
                }
            )
            prepared = await advise_compile_case(
                prepared,
                workdir=str(STORAGE_BASE / generation_id),
                task_id=generation_id,
                stage_name="testcase_compile_advisor",
            )
            tc.module = prepared["module"] or None
            tc.exec_script = prepared["exec_script"]
            tc.compile_status = prepared["compile_status"]
            tc.compile_errors = prepared["compile_errors"]
            tc.execution_mode = prepared["execution_mode"]
            tc.step_contracts = prepared["step_contracts"]
            await db.commit()
            await db.refresh(tc)
            data = _serialize_db_case(tc)

        glog = GenerationLogger(generation_id)
        glog.log(
            "case_edited",
            case_id=case_id,
            test_point_id=data.get("test_point_id"),
            fields=list(updates.keys()),
            compile_status=data.get("compile_status"),
        )
        return {"success": True, "data": data}

    async def recompile_case(self, generation_id: str, case_id: str) -> dict:
        """基于最新 NL 字段重新生成步骤合同和确定性 DSL。"""
        async with async_session_factory() as db:
            tc = await db.get(TestCase, case_id)
            if not tc or tc.generation_id != generation_id:
                return {"success": False, "error": "用例不存在或不属于该生成任务"}
            prepared = prepare_executable_case(
                {
                    "case_id": str(tc.id),
                    "title": tc.title,
                    "description": tc.description or "",
                    "preconditions": tc.preconditions or "",
                    "steps": tc.steps or [],
                    "tags": tc.tags or [],
                    "test_point_id": tc.test_point_id or "",
                    "module": tc.module or "",
                    "automation_level": tc.automation_level or "",
                }
            )
            prepared = await advise_compile_case(
                prepared,
                workdir=str(STORAGE_BASE / generation_id),
                task_id=generation_id,
                stage_name="testcase_compile_advisor",
            )
            tc.module = prepared["module"] or None
            tc.exec_script = prepared["exec_script"]
            tc.compile_status = prepared["compile_status"]
            tc.compile_errors = prepared["compile_errors"]
            tc.execution_mode = prepared["execution_mode"]
            tc.step_contracts = prepared["step_contracts"]
            await db.commit()
            await db.refresh(tc)
            data = _serialize_db_case(tc)
        GenerationLogger(generation_id).log(
            "case_recompiled",
            case_id=case_id,
            compile_status=data["compile_status"],
            error_count=len(data["compile_errors"] or []),
        )
        return {"success": True, "data": data}

    async def approve_case(self, generation_id: str, case_id: str, comment: str = "") -> dict:
        return await self._review_case(
            generation_id, case_id, "approved", comment=comment
        )

    async def reject_case(
        self,
        generation_id: str,
        case_id: str,
        comment: str = "",
        reject_reason: str = "",
    ) -> dict:
        return await self._review_case(
            generation_id,
            case_id,
            "rejected",
            comment=comment,
            reject_reason=reject_reason,
        )

    async def _review_case(
        self,
        generation_id: str,
        case_id: str,
        status: str,
        comment: str = "",
        reject_reason: str = "",
    ) -> dict:
        async with async_session_factory() as db:
            tc = await db.get(TestCase, case_id)
            if not tc or tc.generation_id != generation_id:
                return {"success": False, "error": "用例不存在或不属于该生成任务"}
            tc.status = status
            tc.review_comment = comment or None
            tc.reject_reason = reject_reason or None
            tc.reviewed_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(tc)
            data = _serialize_db_case(tc)

        glog = GenerationLogger(generation_id)
        step = "case_approved" if status == "approved" else "case_rejected"
        glog.log(
            step,
            case_id=case_id,
            test_point_id=data.get("test_point_id"),
            comment=(comment or "")[:300],
            reject_reason=reject_reason or None,
        )

        await self._maybe_complete_task(generation_id)
        return {"success": True, "data": data}

    async def _maybe_complete_task(self, generation_id: str) -> None:
        async with async_session_factory() as db:
            result = await db.execute(
                select(TestCase).where(TestCase.generation_id == generation_id)
            )
            cases = list(result.scalars().all())
        if not cases:
            return
        pending = [c for c in cases if c.status == "pending_review"]
        if pending:
            return
        approved = sum(1 for c in cases if c.status == "approved")
        rejected = sum(1 for c in cases if c.status == "rejected")

        async with self._store_lock:
            task = _task_store.get(generation_id)
            if not task:
                return
            if task.status == "completed":
                return
            task.status = "completed"
            task.current_step = "评审完成"
            task.progress_pct = 100
            task.completed_at = _utcnow()
            _save_task_state(task)

        glog = GenerationLogger(generation_id)
        glog.log(
            "task_completed",
            approved=approved,
            rejected=rejected,
            total=len(cases),
        )

    # ---- 流水线 ----

    async def _run_pipeline(
        self,
        generation_id: str,
        analysis_json: dict,
        selected_tps: list[dict],
        platform_type: str,
        custom_prompt: str,
    ) -> None:
        glog = GenerationLogger(generation_id)

        async def _set_progress(step: str, pct: int) -> None:
            async with self._store_lock:
                task = _task_store.get(generation_id)
                if task:
                    task.status = "processing"
                    task.current_step = step
                    task.progress_pct = pct
                    _save_task_state(task)

        try:
            await _set_progress("加载测试点与 Skill...", 10)
            glog.log(
                "source_loaded",
                ui_tp_count=len(selected_tps),
                selected_count=len(selected_tps),
                analysis_id=(_task_store.get(generation_id).analysis_id
                             if _task_store.get(generation_id) else ""),
            )

            skill = load_skill(SKILL_NAME)
            full_skill_body = skill.body if skill else ""
            full_skill_body = full_skill_body.replace(
                "{platform_type}", platform_type or "通用"
            ).replace("{custom_prompt}", custom_prompt or "无")
            # 完整 Skill 仅落盘审计；每批注入精简指令以省 token
            glog.log(
                "skill_load",
                skill_name=SKILL_NAME,
                body_length=len(full_skill_body),
                inject_mode="slim",
            )
            glog.save_snapshot("SKILL_used.md", full_skill_body)

            slim_instructions = build_slim_skill_instructions(
                platform_type=platform_type,
                custom_prompt=custom_prompt,
            )
            glog.save_snapshot("SKILL_slim_injected.md", slim_instructions)

            fr_map = {
                fr.get("id"): fr
                for fr in (analysis_json.get("functional_requirements") or [])
                if isinstance(fr, dict) and fr.get("id")
            }

            cfg = settings.testcase_generation
            max_tps = int(cfg.max_tps_per_batch)
            target_tokens = int(cfg.target_input_tokens)
            max_concurrency = max(1, int(cfg.max_concurrency))
            fixed_overhead = int(cfg.fixed_overhead_tokens)

            batches = pack_tp_batches_by_tokens(
                selected_tps,
                fr_map,
                max_tps_per_batch=max_tps,
                target_input_tokens=target_tokens,
                fixed_overhead_tokens=fixed_overhead,
            )
            glog.log(
                "batch_plan",
                packing_mode="token_budget",
                batch_count=len(batches),
                max_tps_per_batch=max_tps,
                target_input_tokens=target_tokens,
                max_concurrency=max_concurrency,
                tp_ids_per_batch=[[tp.get("id") for tp in b] for b in batches],
                batch_sizes=[len(b) for b in batches],
            )

            all_cases: list[dict] = []
            completed_batches = 0
            total_batches = max(len(batches), 1)
            progress_lock = asyncio.Lock()
            log_lock = asyncio.Lock()

            async def _run_one(bi: int, batch: list[dict]) -> list[dict]:
                nonlocal completed_batches
                cases = await self._generate_batch(
                    generation_id=generation_id,
                    glog=glog,
                    slim_instructions=slim_instructions,
                    batch=batch,
                    batch_index=bi,
                    fr_map=fr_map,
                    platform_type=platform_type,
                    custom_prompt=custom_prompt,
                    log_lock=log_lock,
                )
                async with progress_lock:
                    completed_batches += 1
                    pct = 15 + int(70 * completed_batches / total_batches)
                    await _set_progress(
                        f"生成用例批次 {completed_batches}/{len(batches)}（并发中）...",
                        pct,
                    )
                return cases

            if batches:
                sem = asyncio.Semaphore(max_concurrency)

                async def _guarded(bi: int, batch: list[dict]) -> tuple[int, list[dict]]:
                    async with sem:
                        return bi, await _run_one(bi, batch)

                results = await asyncio.gather(
                    *[_guarded(i, b) for i, b in enumerate(batches, start=1)],
                    return_exceptions=True,
                )
                ordered: list[tuple[int, list[dict]]] = []
                errors: list[str] = []
                for item in results:
                    if isinstance(item, Exception):
                        errors.append(str(item)[:300])
                        continue
                    ordered.append(item)
                if errors and not ordered:
                    raise RuntimeError(
                        "全部批次失败: " + "; ".join(errors[:3])
                    )
                if errors:
                    glog.log(
                        "batch_partial_errors",
                        error_count=len(errors),
                        errors=errors[:5],
                    )
                ordered.sort(key=lambda x: x[0])
                for _, cases in ordered:
                    all_cases.extend(cases)

            # 覆盖校验
            selected_ids = [str(tp.get("id")) for tp in selected_tps if tp.get("id")]
            report = validate_ui_case_coverage(selected_ids, all_cases)
            glog.log(
                "coverage_check",
                ok=report.ok,
                missing_tp_ids=report.missing_tp_ids,
                case_count=report.case_count,
                summary=report.summary,
            )
            if not report.ok:
                missing_set = set(report.missing_tp_ids)
                retry_tps = [
                    tp for tp in selected_tps if str(tp.get("id")) in missing_set
                ]
                if retry_tps:
                    glog.log(
                        "self_heal_start",
                        failure_category="output_quality",
                        step_name="coverage_check",
                        missing_count=len(retry_tps),
                    )
                    heal_batches = pack_tp_batches_by_tokens(
                        retry_tps,
                        fr_map,
                        max_tps_per_batch=max_tps,
                        target_input_tokens=target_tokens,
                        fixed_overhead_tokens=fixed_overhead,
                    )
                    glog.log(
                        "self_heal_batch_plan",
                        batch_count=len(heal_batches),
                        tp_ids_per_batch=[
                            [tp.get("id") for tp in b] for b in heal_batches
                        ],
                    )
                    heal_prompt = (
                        (custom_prompt or "")
                        + "\n请务必为本批每个测试点生成至少 1 条 UI 用例。"
                    ).strip()
                    for bi, batch in enumerate(heal_batches, start=1):
                        healed = await self._generate_batch(
                            generation_id=generation_id,
                            glog=glog,
                            slim_instructions=build_slim_skill_instructions(
                                platform_type=platform_type,
                                custom_prompt=heal_prompt,
                            ),
                            batch=batch,
                            batch_index=1000 + bi,
                            fr_map=fr_map,
                            platform_type=platform_type,
                            custom_prompt=heal_prompt,
                            log_lock=log_lock,
                        )
                        all_cases.extend(healed)
                    report = validate_ui_case_coverage(selected_ids, all_cases)
                    glog.log(
                        "coverage_check",
                        ok=report.ok,
                        missing_tp_ids=report.missing_tp_ids,
                        case_count=report.case_count,
                        summary=report.summary,
                        after_heal=True,
                    )
                    glog.log(
                        "self_heal_complete" if report.ok else "self_heal_exhausted",
                        outcome="success" if report.ok else "failed",
                        missing_tp_ids=report.missing_tp_ids,
                    )
                if not report.ok:
                    raise RuntimeError(
                        f"用例覆盖不足: 缺失测试点 {', '.join(report.missing_tp_ids[:20])}"
                    )

            # 可执行性自愈：定点加固 expected → Agent 改写 → 单 TP 重生兜底
            await _set_progress("可执行性自愈中...", 85)
            from src.services.testcase_exec_heal import heal_cases_for_executability

            async def _regen_one(tp_id: str, seed: dict):
                tp = next(
                    (t for t in selected_tps if str(t.get("id")) == tp_id),
                    None,
                )
                if not tp:
                    return []
                heal_prompt = (
                    (custom_prompt or "")
                    + "\n请仅针对该测试点生成可执行 UI 用例；"
                    "expected 必须用「」包裹关键可见文案；负向写不出现「xxx」。"
                ).strip()
                return await self._generate_batch(
                    generation_id=generation_id,
                    glog=glog,
                    slim_instructions=build_slim_skill_instructions(
                        platform_type=platform_type,
                        custom_prompt=heal_prompt,
                    ),
                    batch=[tp],
                    batch_index=2000,
                    fr_map=fr_map,
                    platform_type=platform_type,
                    custom_prompt=heal_prompt,
                    log_lock=log_lock,
                )

            all_cases = await heal_cases_for_executability(
                all_cases,
                generation_id=generation_id,
                workdir=str(glog.dir_path),
                log=glog.log,
                regen_fn=_regen_one,
            )

            await _set_progress("写入用例库...", 90)
            case_ids = await self._persist_cases(
                generation_id=generation_id,
                analysis_id=_task_store[generation_id].analysis_id,
                cases=all_cases,
                platform_type=platform_type,
            )
            glog.save_json("cases_snapshot.json", all_cases)
            glog.log(
                "cases_persisted",
                inserted=len(case_ids),
                generation_id=generation_id,
                case_ids=case_ids[:50],
            )

            async with self._store_lock:
                task = _task_store.get(generation_id)
                if task:
                    task.status = "pending_review"
                    task.current_step = "等待人工评审"
                    task.progress_pct = 95
                    task.case_ids = case_ids
                    task.total_cases = len(case_ids)
                    _save_task_state(task)

            glog.log(
                "pipeline_done",
                total_cases=len(case_ids),
                status="pending_review",
            )

        except Exception as exc:
            logger.error(
                "generation_pipeline_error",
                generation_id=generation_id,
                error=str(exc),
            )
            glog.log("pipeline_error", error=str(exc)[:1000])
            async with self._store_lock:
                task = _task_store.get(generation_id)
                if task:
                    task.status = "failed"
                    task.current_step = "生成失败"
                    task.error_message = str(exc)[:1000]
                    _save_task_state(task)

    async def _generate_batch(
        self,
        *,
        generation_id: str,
        glog: GenerationLogger,
        slim_instructions: str,
        batch: list[dict],
        batch_index: int,
        fr_map: dict,
        platform_type: str,
        custom_prompt: str,
        log_lock: asyncio.Lock | None = None,
    ) -> list[dict]:
        compressed_tps = [compress_test_point(tp) for tp in batch]
        fr_summaries = build_fr_summaries_for_batch(batch, fr_map)

        prompt = f"""{slim_instructions}

## 本批 UI 测试点

```json
{json.dumps(compressed_tps, ensure_ascii=False, separators=(",", ":"))}
```

## 关联功能需求摘要（可选参考）

```json
{json.dumps(fr_summaries, ensure_ascii=False, separators=(",", ":"))}
```

请严格输出 JSON 数组。
"""
        est = estimate_tokens(prompt)
        timeout = dynamic_timeout(est)

        async def _safe_log(step: str, **kwargs) -> None:
            if log_lock is None:
                glog.log(step, **kwargs)
                return
            async with log_lock:
                glog.log(step, **kwargs)

        async def _safe_snapshot(filename: str, content: str) -> None:
            if log_lock is None:
                glog.save_snapshot(filename, content)
                return
            async with log_lock:
                glog.save_snapshot(filename, content)

        await _safe_snapshot(f"prompt_batch_{batch_index}.txt", prompt)
        await _safe_log(
            "agent_start",
            role=ROLE_GENERATOR,
            batch=batch_index,
            prompt_len=len(prompt),
            estimated_tokens=est,
            dynamic_timeout_s=timeout,
            tp_ids=[tp.get("id") for tp in batch],
            tp_count=len(batch),
            prompt_head=prompt[:400],
            prompt_tail=prompt[-200:],
        )

        result = await agent_runtime.run(
            AgentTask(
                role=ROLE_GENERATOR,
                prompt=prompt,
                workdir=str(glog.dir_path),
                timeout=timeout,
                stage_name="testcase_generation",
                task_id=generation_id,
            )
        )

        raw = result.raw_output or ""
        await _safe_snapshot(f"agent_output_batch_{batch_index}.txt", raw)
        if not result.success:
            await _safe_log(
                "agent_failed",
                role=ROLE_GENERATOR,
                batch=batch_index,
                backend=result.backend or "",
                fallback_from=result.fallback_from or "",
                error=(result.error or "")[:800],
                exit_code=result.exit_code,
            )
            raise RuntimeError(
                f"批次 {batch_index} Agent 失败: {result.error or 'unknown'}"
            )

        await _safe_log(
            "agent_done",
            role=ROLE_GENERATOR,
            batch=batch_index,
            backend=result.backend or "",
            fallback_from=result.fallback_from or "",
            latency_ms=result.latency_ms,
            output_len=len(raw),
            output_head=raw[:400],
            output_tail=raw[-300:],
        )

        raw_cases, method = _extract_cases_from_output(raw, glog.dir_path)
        normalized = []
        for c in raw_cases:
            nc = _normalize_case(c, platform_type)
            if nc:
                if not nc["test_point_id"] and len(batch) == 1:
                    nc["test_point_id"] = str(batch[0].get("id") or "")
                if not nc.get("related_fr"):
                    for tp in batch:
                        if str(tp.get("id")) == nc["test_point_id"]:
                            nc["related_fr"] = str(tp.get("related_fr") or "")
                            break
                normalized.append(nc)

        await _safe_log(
            "json_parse",
            success=bool(normalized),
            extract_method=method,
            case_count=len(normalized),
            batch=batch_index,
        )
        if not normalized:
            raise RuntimeError(f"批次 {batch_index} 未能解析出有效用例 JSON")
        return normalized

    async def _persist_cases(
        self,
        *,
        generation_id: str,
        analysis_id: str,
        cases: list[dict],
        platform_type: str,
    ) -> list[str]:
        prepared_rows: list[tuple[dict[str, Any], list[Any], str]] = []
        for c in cases:
            tags = list(c.get("tags") or [])
            if c.get("related_fr") and c["related_fr"] not in tags:
                tags.append(c["related_fr"])
            if c.get("test_point_id") and c["test_point_id"] not in tags:
                tags.append(c["test_point_id"])
            prepared = prepare_executable_case(c)
            lint = lint_case({**prepared, "automation_level": ""})
            level = lint["level"]
            hint = str(prepared.get("automation_level_hint") or "")
            if hint == "manual":
                level = "manual"
            elif hint == "semi" and level == "ready":
                level = "semi"
            declared = str(c.get("automation_level") or "").strip().lower()
            if declared == "manual":
                level = "manual"
            if lint["warnings"]:
                tags = list(tags)
                if level == "manual" and "manual" not in tags:
                    tags.append("manual")
            prepared_rows.append((prepared, tags, level))

        need_advice = sum(
            1 for prepared, _, _ in prepared_rows
            if str(prepared.get("compile_status") or "") in {"failed", "agent_required"}
        )
        if need_advice:
            GenerationLogger(generation_id).log(
                "compile_advise_start",
                case_count=need_advice,
                message=f"对 {need_advice} 条未就绪用例调用编译诊断 Agent",
            )
            advised_list = await advise_prepared_cases(
                [row[0] for row in prepared_rows],
                workdir=str(STORAGE_BASE / generation_id),
                task_id=generation_id,
                max_concurrency=int(
                    getattr(settings.testcase_generation, "max_concurrency", 3) or 3
                ),
            )
            prepared_rows = [
                (advised_list[i], prepared_rows[i][1], prepared_rows[i][2])
                for i in range(len(prepared_rows))
            ]
            GenerationLogger(generation_id).log(
                "compile_advise_done",
                case_count=need_advice,
                message="编译诊断完成",
            )

        ids: list[str] = []
        async with async_session_factory() as db:
            for prepared, tags, level in prepared_rows:
                tc = TestCase(
                    project_id=None,
                    pipeline_id=None,
                    title=prepared["title"],
                    description=prepared.get("description") or "",
                    preconditions=prepared.get("preconditions") or "",
                    steps=prepared["steps"],
                    priority=prepared.get("priority") or "中",
                    test_type="ui",
                    tags=tags,
                    platform_type=prepared.get("platform_type") or platform_type or "general",
                    status="pending_review",
                    source="auto",
                    generation_id=generation_id,
                    source_analysis_id=analysis_id,
                    test_point_id=prepared.get("test_point_id") or None,
                    automation_level=level,
                    module=prepared.get("module") or None,
                    exec_script=prepared.get("exec_script"),
                    compile_status=prepared.get("compile_status") or "pending",
                    compile_errors=prepared.get("compile_errors") or [],
                    execution_mode=prepared.get("execution_mode") or "hybrid",
                    step_contracts=prepared.get("step_contracts") or [],
                    precondition_spec=prepared.get("precondition_spec"),
                    automation_block_reason=prepared.get("automation_block_reason") or None,
                    assertion_quality=prepared.get("assertion_quality") or None,
                )
                db.add(tc)
                await db.flush()
                ids.append(tc.id)
            await db.commit()
        return ids


def _serialize_db_case(c: TestCase) -> dict:
    return {
        "id": c.id,
        "project_id": c.project_id,
        "pipeline_id": c.pipeline_id,
        "directory_id": c.directory_id,
        "title": c.title,
        "description": c.description,
        "preconditions": c.preconditions,
        "steps": c.steps,
        "priority": c.priority,
        "test_type": c.test_type,
        "tags": c.tags,
        "platform_type": c.platform_type,
        "status": c.status,
        "review_comment": c.review_comment,
        "reject_reason": c.reject_reason,
        "reviewed_at": c.reviewed_at.isoformat() if c.reviewed_at else None,
        "source": c.source or "auto",
        "generation_id": c.generation_id,
        "source_analysis_id": c.source_analysis_id,
        "test_point_id": c.test_point_id,
        "automation_level": c.automation_level,
        "module": c.module,
        "exec_script": c.exec_script,
        "compile_status": c.compile_status or "pending",
        "compile_errors": c.compile_errors or [],
        "execution_mode": c.execution_mode or "hybrid",
        "step_contracts": c.step_contracts or [],
        "precondition_spec": getattr(c, "precondition_spec", None),
        "automation_block_reason": getattr(c, "automation_block_reason", None),
        "assertion_quality": getattr(c, "assertion_quality", None),
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


testcase_generation_svc = TestCaseGenerationService()
