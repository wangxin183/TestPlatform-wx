"""独立 App UI 执行运行时 API — EXE-xxxx 与 execution_runtime 桥接。"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from src.services.execution_runtime_service import execution_runtime_svc
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/execution-runs", tags=["execution-runs"])


class StartRunBody(BaseModel):
    case_ids: list[str] = Field(..., min_length=1)
    include_semi: bool = False
    include_manual: bool = False


@router.get("/config")
async def get_runtime_config():
    data = await execution_runtime_svc.get_runtime_config()
    return {"success": True, "data": data, "error": None}


@router.get("/cases")
async def list_runnable_cases(
    test_type: str = Query("ui"),
    limit: int = Query(200, ge=1, le=500),
    include_semi: bool = Query(False),
    include_manual: bool = Query(False),
):
    items = await execution_runtime_svc.list_approved_cases(
        test_type=test_type,
        limit=limit,
        include_semi=include_semi,
        include_manual=include_manual,
    )
    return {"success": True, "data": items, "error": None}


@router.post("")
async def create_run(body: StartRunBody):
    try:
        run_id = await execution_runtime_svc.start_run(
            body.case_ids,
            include_semi=body.include_semi,
            include_manual=body.include_manual,
        )
        return {
            "success": True,
            "data": {"run_id": run_id, **(await execution_runtime_svc.get_status(run_id))},
            "error": None,
        }
    except ValueError as exc:
        return {"success": False, "data": None, "error": str(exc)}
    except Exception as exc:
        logger.error("create_execution_run_error", error=str(exc))
        return {"success": False, "data": None, "error": f"创建执行任务失败: {exc}"}


@router.get("")
async def list_runs(size: int = Query(50, ge=1, le=200)):
    items = await execution_runtime_svc.list_runs(limit=size)
    return {"success": True, "data": items, "error": None}


@router.get("/{run_id}")
async def get_run_detail(run_id: str):
    try:
        data = await execution_runtime_svc.get_detail(run_id)
        return {"success": True, "data": data, "error": None}
    except ValueError as exc:
        return {"success": False, "data": None, "error": str(exc)}


@router.get("/{run_id}/status")
async def get_run_status(run_id: str):
    data = await execution_runtime_svc.get_status(run_id)
    if data.get("status") == "unknown":
        return {"success": False, "data": None, "error": f"任务 {run_id} 不存在"}
    return {"success": True, "data": data, "error": None}


@router.get("/{run_id}/summary")
async def get_run_summary(run_id: str):
    try:
        detail = await execution_runtime_svc.get_detail(run_id)
        return {
            "success": True,
            "data": {
                "run_id": run_id,
                "summary": detail.get("summary"),
                "case_results": detail.get("case_results"),
                "execution_id": detail.get("execution_id"),
            },
            "error": None,
        }
    except ValueError as exc:
        return {"success": False, "data": None, "error": str(exc)}


@router.get("/{run_id}/defects")
async def get_run_defects(run_id: str):
    try:
        detail = await execution_runtime_svc.get_detail(run_id)
        return {"success": True, "data": detail.get("defects") or [], "error": None}
    except ValueError as exc:
        return {"success": False, "data": None, "error": str(exc)}
