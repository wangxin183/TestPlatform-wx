"""独立用例生成 API — 与项目解耦。

端点：
  GET    /testcase-generations/sources
  GET    /testcase-generations/sources/{ra_id}/test-points
  POST   /testcase-generations
  GET    /testcase-generations
  GET    /testcase-generations/{id}
  GET    /testcase-generations/{id}/status
  PUT    /testcase-generations/{id}/cases/{case_id}
  POST   /testcase-generations/{id}/cases/{case_id}/approve
  POST   /testcase-generations/{id}/cases/{case_id}/reject
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

from src.services.testcase_generation_service import testcase_generation_svc
from src.services.testcase_module_catalog import module_catalog
from src.utils.analysis_logger import GenerationLogger
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/testcase-generations", tags=["testcase-generation"])


class StartGenerationBody(BaseModel):
    analysis_id: str
    test_point_ids: list[str] = Field(default_factory=list)
    platform_type: str = ""
    custom_prompt: str = ""


class UpdateCaseBody(BaseModel):
    title: str | None = None
    description: str | None = None
    preconditions: str | None = None
    steps: list | None = None
    priority: str | None = None
    tags: list | None = None
    platform_type: str | None = None
    module: str | None = None


class RejectCaseBody(BaseModel):
    comment: str = ""
    reject_reason: str = ""


class ApproveCaseBody(BaseModel):
    comment: str = ""


def _serialize_task(task, *, include_logs: bool = False, cases: list | None = None) -> dict:
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
    if cases is not None:
        data["cases"] = cases
        pending = sum(1 for c in cases if c.get("status") == "pending_review")
        approved = sum(1 for c in cases if c.get("status") == "approved")
        rejected = sum(1 for c in cases if c.get("status") == "rejected")
        data["stats"] = {
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
            "total": len(cases),
        }
    if include_logs:
        from src.services.narrative_log import enrich_log_entry

        raw_logs = GenerationLogger(task.generation_id).read_logs()
        data["logs"] = [enrich_log_entry(item) for item in raw_logs]
    return data


@router.get("/modules")
async def list_modules():
    return {
        "success": True,
        "data": [
            {
                "id": module.id,
                "name": module.name,
                "entry_nl": module.entry_nl,
                "has_executable_setup": bool(module.entry_steps),
            }
            for module in module_catalog.modules
        ],
        "error": None,
    }


@router.get("/sources")
async def list_sources():
    items = await testcase_generation_svc.list_source_analyses()
    return {"success": True, "data": items, "error": None}


@router.get("/sources/{ra_id}/test-points")
async def get_source_test_points(ra_id: str):
    result = await testcase_generation_svc.get_ui_test_points(ra_id)
    if not result.get("success"):
        return {"success": False, "data": None, "error": result.get("error")}
    return {"success": True, "data": result, "error": None}


@router.post("")
async def create_generation(body: StartGenerationBody):
    try:
        generation_id = await testcase_generation_svc.start_generation(
            analysis_id=body.analysis_id,
            test_point_ids=body.test_point_ids,
            platform_type=body.platform_type,
            custom_prompt=body.custom_prompt,
        )
        task = await testcase_generation_svc.get_task(generation_id)
        return {
            "success": True,
            "data": _serialize_task(task) if task else {"generation_id": generation_id},
            "error": None,
        }
    except ValueError as exc:
        return {"success": False, "data": None, "error": str(exc)}
    except Exception as exc:
        logger.error("create_generation_error", error=str(exc))
        return {"success": False, "data": None, "error": f"创建生成任务失败: {exc}"}


@router.get("")
async def list_generations(
    status: str = Query(""),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
):
    tasks, total = await testcase_generation_svc.list_tasks(status=status, page=page, size=size)
    return {
        "success": True,
        "data": [_serialize_task(t) for t in tasks],
        "error": None,
        "meta": {"page": page, "size": size, "total": total},
    }


@router.get("/{generation_id}")
async def get_generation(generation_id: str):
    task = await testcase_generation_svc.get_task(generation_id)
    if not task:
        return {"success": False, "data": None, "error": f"任务未找到: {generation_id}"}
    cases = await testcase_generation_svc.get_task_cases(generation_id)
    return {
        "success": True,
        "data": _serialize_task(task, include_logs=True, cases=cases),
        "error": None,
    }


@router.get("/{generation_id}/status")
async def get_generation_status(generation_id: str):
    task = await testcase_generation_svc.get_task(generation_id)
    if not task:
        return {"success": False, "data": None, "error": f"任务未找到: {generation_id}"}
    return {
        "success": True,
        "data": {
            "generation_id": task.generation_id,
            "status": task.status,
            "current_step": task.current_step,
            "progress_pct": task.progress_pct,
            "total_cases": task.total_cases,
            "error_message": task.error_message,
        },
        "error": None,
    }


@router.put("/{generation_id}/cases/{case_id}")
async def update_case(generation_id: str, case_id: str, body: UpdateCaseBody = Body(...)):
    updates = body.model_dump(exclude_unset=True)
    result = await testcase_generation_svc.update_case(generation_id, case_id, updates)
    if not result.get("success"):
        return {"success": False, "data": None, "error": result.get("error")}
    return {"success": True, "data": result.get("data"), "error": None}


@router.post("/{generation_id}/cases/{case_id}/recompile")
async def recompile_case(generation_id: str, case_id: str):
    result = await testcase_generation_svc.recompile_case(generation_id, case_id)
    if not result.get("success"):
        return {"success": False, "data": None, "error": result.get("error")}
    return {"success": True, "data": result.get("data"), "error": None}


@router.post("/{generation_id}/cases/{case_id}/approve")
async def approve_case(
    generation_id: str,
    case_id: str,
    body: ApproveCaseBody = Body(default=ApproveCaseBody()),
):
    result = await testcase_generation_svc.approve_case(
        generation_id, case_id, comment=body.comment
    )
    if not result.get("success"):
        return {"success": False, "data": None, "error": result.get("error")}
    task = await testcase_generation_svc.get_task(generation_id)
    return {
        "success": True,
        "data": {
            "case": result.get("data"),
            "task_status": task.status if task else None,
        },
        "error": None,
    }


@router.post("/{generation_id}/cases/{case_id}/reject")
async def reject_case(
    generation_id: str,
    case_id: str,
    body: RejectCaseBody = Body(default=RejectCaseBody()),
):
    result = await testcase_generation_svc.reject_case(
        generation_id,
        case_id,
        comment=body.comment,
        reject_reason=body.reject_reason,
    )
    if not result.get("success"):
        return {"success": False, "data": None, "error": result.get("error")}
    task = await testcase_generation_svc.get_task(generation_id)
    return {
        "success": True,
        "data": {
            "case": result.get("data"),
            "task_status": task.status if task else None,
        },
        "error": None,
    }
