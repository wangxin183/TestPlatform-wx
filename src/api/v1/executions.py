"""Execution API endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.models import Execution, ExecutionResult

router = APIRouter(tags=["executions"])


@router.get("/executions/{execution_id}")
async def get_execution(execution_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Execution).where(Execution.id == execution_id))
    ex = result.scalar_one_or_none()
    if not ex:
        return {"success": False, "data": None, "error": "Not found"}
    return {"success": True, "data": _serialize(ex), "error": None}


@router.get("/executions/{execution_id}/results")
async def get_execution_results(
    execution_id: str,
    status: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(ExecutionResult).where(ExecutionResult.execution_id == execution_id)
    if status:
        query = query.where(ExecutionResult.status == status)
    result = await db.execute(query)
    items = result.scalars().all()
    return {"success": True, "data": [_serialize_result(r) for r in items], "error": None}


@router.get("/executions/{execution_id}/defects")
async def get_execution_defects(execution_id: str, db: AsyncSession = Depends(get_db)):
    from src.core.models.models import Defect
    result = await db.execute(
        select(Defect).where(Defect.execution_id == execution_id)
    )
    items = result.scalars().all()
    return {"success": True, "data": [_serialize_defect(d) for d in items], "error": None}


def _serialize(ex: Execution) -> dict:
    return {
        "id": ex.id,
        "test_suite_id": ex.test_suite_id,
        "project_id": ex.project_id,
        "pipeline_id": ex.pipeline_id,
        "executor_type": ex.executor_type,
        "status": ex.status,
        "total_cases": ex.total_cases,
        "passed_cases": ex.passed_cases,
        "failed_cases": ex.failed_cases,
        "error_cases": ex.error_cases,
        "started_at": ex.started_at.isoformat() if ex.started_at else None,
        "completed_at": ex.completed_at.isoformat() if ex.completed_at else None,
    }


def _serialize_result(r: ExecutionResult) -> dict:
    return {
        "id": r.id,
        "execution_id": r.execution_id,
        "test_case_id": r.test_case_id,
        "attempt": r.attempt,
        "status": r.status,
        "duration_ms": r.duration_ms,
        "error_message": r.error_message,
        "failure_reason": r.failure_reason,
        "screenshot_path": r.screenshot_path,
        "executed_at": r.executed_at.isoformat() if r.executed_at else None,
    }


def _serialize_defect(d) -> dict:
    return {
        "id": d.id,
        "execution_result_id": d.execution_result_id,
        "project_id": d.project_id,
        "title": d.title,
        "description": d.description,
        "severity": d.severity,
        "status": d.status,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


# ═══════════════════════════════════════════════════════════════
# Standalone execution
# ═══════════════════════════════════════════════════════════════

@router.post("/executions/run")
async def run_execution_standalone(body: dict, db: AsyncSession = Depends(get_db)):
    """Run test execution independently (no pipeline required).

    Body: {
        "project_id": "xxx",              // optional — inferred from cases if omitted
        "test_case_ids": ["id1", "id2"],  // required
        "target_url": "https://...",      // optional
        "api_base_url": "https://...",    // optional
    }
    """
    import asyncio

    test_case_ids = body.get("test_case_ids", [])
    if not test_case_ids:
        return {"success": False, "data": None, "error": "test_case_ids is required"}

    target_url = body.get("target_url", "")
    api_base_url = body.get("api_base_url", "")
    project_id = body.get("project_id", "")

    # ── Fetch test cases ──
    result = await db.execute(
        select(TestCase).where(TestCase.id.in_(test_case_ids))
    )
    test_cases = list(result.scalars().all())

    # Infer project_id from first case if not provided
    if not project_id and test_cases:
        project_id = test_cases[0].project_id or ""

    if not test_cases:
        return {"success": False, "data": None, "error": "没有已审批的用例可执行"}

    # ── Build progress callback → WebSocket broadcast ──
    async def progress_callback(data: dict):
        exec_id = data.get("execution_id", "")
        if exec_id:
            await ws_manager.broadcast_execution_progress(exec_id, data)

    # ── Execute ──
    from src.services.execution_service import ExecutionService
    from src.services.defect_analyzer import DefectAnalyzer

    service = ExecutionService(db, progress_callback=progress_callback)
    summary = await service.execute_cases(
        test_cases=test_cases,
        pipeline_id="",
        project_id=project_id,
        target_url=target_url,
        api_base_url=api_base_url,
    )

    # ── Auto-analyze failures → create defects ──
    analyzer = DefectAnalyzer(db)
    all_defects = []
    for eid in summary.execution_ids:
        defects = await analyzer.analyze_execution(eid)
        all_defects.extend(defects)

    # ── Notify completion via WebSocket ──
    for eid in summary.execution_ids:
        await ws_manager.broadcast_execution_complete(eid, {
            "execution_id": eid,
            "total_cases": summary.total_cases,
            "passed": summary.passed,
            "failed": summary.failed,
            "error": summary.error,
            "generated": summary.generated,
            "defects_created": len(all_defects),
        })

    return {
        "success": True,
        "data": {
            "execution_ids": summary.execution_ids,
            "total_cases": summary.total_cases,
            "passed": summary.passed,
            "failed": summary.failed,
            "error": summary.error,
            "generated": summary.generated,
            "defects_created": len(all_defects),
        },
        "error": None,
    }


@router.get("/executions/{execution_id}/progress")
async def get_execution_progress(execution_id: str, db: AsyncSession = Depends(get_db)):
    """Lightweight polling endpoint — returns current execution progress."""
    result = await db.execute(
        select(Execution).where(Execution.id == execution_id)
    )
    ex = result.scalar_one_or_none()
    if not ex:
        return {"success": False, "data": None, "error": "Execution not found"}

    # Count results by status
    from sqlalchemy import func
    stats_result = await db.execute(
        select(
            ExecutionResult.status,
            func.count(ExecutionResult.id),
        ).where(
            ExecutionResult.execution_id == execution_id,
        ).group_by(ExecutionResult.status)
    )
    stats = dict(stats_result.all())

    return {
        "success": True,
        "data": {
            "execution_id": execution_id,
            "status": ex.status,
            "total_cases": ex.total_cases,
            "completed": sum(stats.values()),
            "by_status": stats,
        },
        "error": None,
    }


@router.get("/executions/{execution_id}/summary")
async def get_execution_summary(execution_id: str, db: AsyncSession = Depends(get_db)):
    """Comprehensive summary — execution + results + defects + generated scripts."""
    # Execution
    result = await db.execute(
        select(Execution).where(Execution.id == execution_id)
    )
    ex = result.scalar_one_or_none()
    if not ex:
        return {"success": False, "data": None, "error": "Execution not found"}

    # Results
    results_result = await db.execute(
        select(ExecutionResult).where(ExecutionResult.execution_id == execution_id)
    )
    all_results = results_result.scalars().all()

    # Defects
    from src.core.models.models import Defect
    defects_result = await db.execute(
        select(Defect).where(Defect.execution_id == execution_id)
    )
    defects = defects_result.scalars().all()

    # Generated scripts
    script_paths = [
        r.generated_script_path
        for r in all_results
        if getattr(r, "generated_script_path", None)
    ]

    # Pass rate
    total = len(all_results)
    passed = sum(1 for r in all_results if r.status == "passed")
    failed = sum(1 for r in all_results if r.status == "failed")
    error = sum(1 for r in all_results if r.status == "error")
    generated = sum(1 for r in all_results if r.status == "generated")

    return {
        "success": True,
        "data": {
            "execution": _serialize(ex),
            "results": [_serialize_result(r) for r in all_results],
            "defects": [_serialize_defect(d) for d in defects],
            "scripts": script_paths,
            "stats": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "error": error,
                "generated": generated,
                "pass_rate": round(passed / max(total - generated, 1) * 100, 1) if total > generated else 0,
            },
        },
        "error": None,
    }


@router.post("/executions/{execution_id}/retry")
async def retry_execution(execution_id: str, body: Optional[dict] = None, db: AsyncSession = Depends(get_db)):
    """Retry failed/error cases in an execution.

    Body (optional): {"case_ids": ["id1", "id2"]}  // retry specific cases
    """
    # Fetch execution
    result = await db.execute(
        select(Execution).where(Execution.id == execution_id)
    )
    ex = result.scalar_one_or_none()
    if not ex:
        return {"success": False, "data": None, "error": "Execution not found"}

    # Determine which cases to retry
    case_ids = (body or {}).get("case_ids", [])
    if case_ids:
        result = await db.execute(
            select(TestCase).where(TestCase.id.in_(case_ids))
        )
        retry_cases = list(result.scalars().all())
    else:
        # Retry all failed/error cases
        failed_results = await db.execute(
            select(ExecutionResult).where(
                ExecutionResult.execution_id == execution_id,
                ExecutionResult.status.in_(["failed", "error"]),
            )
        )
        failed_case_ids = [r.test_case_id for r in failed_results.scalars().all()]
        if not failed_case_ids:
            return {"success": False, "data": None, "error": "没有失败的用例需要重试"}
        result = await db.execute(
            select(TestCase).where(TestCase.id.in_(failed_case_ids))
        )
        retry_cases = list(result.scalars().all())

    if not retry_cases:
        return {"success": False, "data": None, "error": "没有找到需要重试的用例"}

    # Run retry
    from src.services.execution_service import ExecutionService
    service = ExecutionService(db)
    summary = await service.execute_cases(
        test_cases=retry_cases,
        pipeline_id=ex.pipeline_id or "",
        project_id=ex.project_id or "",
    )

    return {
        "success": True,
        "data": {
            "retry_execution_ids": summary.execution_ids,
            "retried_cases": len(retry_cases),
            "passed": summary.passed,
            "failed": summary.failed,
            "error": summary.error,
        },
        "error": None,
    }
