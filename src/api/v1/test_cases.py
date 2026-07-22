"""Test Case API endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.models import TestCase
from src.services.testcase_contract_compiler import prepare_executable_case

router = APIRouter(tags=["test_cases"])


@router.get("/projects/{project_id}/test-cases")
async def list_test_cases(
    project_id: str,
    pipeline_id: str = Query(None),
    status: str = Query(None),
    priority: str = Query(None),
    search: str = Query(None),
    size: int = Query(100),
    db: AsyncSession = Depends(get_db),
):
    query = select(TestCase).where(TestCase.project_id == project_id)
    if pipeline_id:
        query = query.where(TestCase.pipeline_id == pipeline_id)
    if status:
        query = query.where(TestCase.status == status)
    if priority:
        query = query.where(TestCase.priority == priority)
    if search:
        query = query.where(TestCase.title.contains(search))
    query = query.order_by(TestCase.created_at.desc()).limit(size)

    result = await db.execute(query)
    items = result.scalars().all()
    return {"success": True, "data": [_serialize(tc) for tc in items], "error": None}


@router.get("/test-cases/{case_id}")
async def get_test_case(case_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TestCase).where(TestCase.id == case_id))
    tc = result.scalar_one_or_none()
    if not tc:
        return {"success": False, "data": None, "error": "Not found"}
    return {"success": True, "data": _serialize(tc), "error": None}


@router.post("/test-cases/{case_id}/approve")
async def approve_test_case(case_id: str, body: dict = {}, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TestCase).where(TestCase.id == case_id))
    tc = result.scalar_one_or_none()
    if not tc:
        return {"success": False, "data": None, "error": "Not found"}
    tc.status = "approved"
    tc.review_comment = body.get("comment")
    db.add(tc)
    await db.commit()
    return {"success": True, "data": _serialize(tc), "error": None}


@router.post("/test-cases/{case_id}/reject")
async def reject_test_case(case_id: str, body: dict = {}, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TestCase).where(TestCase.id == case_id))
    tc = result.scalar_one_or_none()
    if not tc:
        return {"success": False, "data": None, "error": "Not found"}
    tc.status = "rejected"
    tc.review_comment = body.get("comment")
    tc.reject_reason = body.get("reason")
    db.add(tc)
    await db.commit()
    return {"success": True, "data": _serialize(tc), "error": None}


@router.put("/test-cases/{case_id}")
async def update_test_case(case_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    """Update editable fields of a test case (title, description, preconditions, steps, notes)."""
    result = await db.execute(select(TestCase).where(TestCase.id == case_id))
    tc = result.scalar_one_or_none()
    if not tc:
        return {"success": False, "data": None, "error": "Not found"}

    for field in (
        "title", "description", "preconditions", "steps", "notes",
        "priority", "test_type", "tags", "module",
    ):
        if field in body:
            setattr(tc, field, body[field])
    prepared = prepare_executable_case(
        {
            "case_id": str(tc.id),
            "title": tc.title,
            "description": tc.description or "",
            "preconditions": tc.preconditions or "",
            "steps": tc.steps or [],
            "tags": tc.tags or [],
            "module": tc.module or "",
            "test_point_id": tc.test_point_id or "",
        }
    )
    tc.module = prepared.get("module") or None
    tc.exec_script = prepared.get("exec_script")
    tc.compile_status = prepared.get("compile_status")
    tc.compile_errors = prepared.get("compile_errors") or []
    tc.execution_mode = prepared.get("execution_mode")
    tc.step_contracts = prepared.get("step_contracts") or []
    db.add(tc)
    await db.commit()
    return {"success": True, "data": _serialize(tc), "error": None}


@router.post("/test-cases/batch")
async def batch_action(body: dict, db: AsyncSession = Depends(get_db)):
    ids = body.get("ids", [])
    action = body.get("action")
    if action not in ("approve", "reject"):
        return {"success": False, "data": None, "error": "Invalid action"}

    new_status = "approved" if action == "approve" else "rejected"
    for case_id in ids:
        result = await db.execute(select(TestCase).where(TestCase.id == case_id))
        tc = result.scalar_one_or_none()
        if tc:
            tc.status = new_status
            db.add(tc)
    await db.commit()
    return {"success": True, "data": {"updated": len(ids)}, "error": None}

@router.post("/test-cases/batch-by-filter")
async def batch_by_filter(body: dict, db: AsyncSession = Depends(get_db)):
    """Batch approve/reject test cases by filter criteria (priority, test_type, etc.)."""
    filters = body.get("filters", {})
    action = body.get("action")
    pipeline_id = body.get("pipeline_id")
    if action not in ("approve", "reject"):
        return {"success": False, "data": None, "error": "Invalid action"}

    query = select(TestCase)
    if pipeline_id:
        query = query.where(TestCase.pipeline_id == pipeline_id)
    if filters.get("priority"):
        priorities = [p.strip() for p in filters["priority"].split(",")]
        query = query.where(TestCase.priority.in_(priorities))
    if filters.get("test_type"):
        types = [t.strip() for t in filters["test_type"].split(",")]
        query = query.where(TestCase.test_type.in_(types))
    # Only affect pending_review cases
    query = query.where(TestCase.status == "pending_review")

    result = await db.execute(query)
    cases = result.scalars().all()

    new_status = "approved" if action == "approve" else "rejected"
    updated = 0
    for tc in cases:
        tc.status = new_status
        db.add(tc)
        updated += 1
    await db.commit()
    return {"success": True, "data": {"updated": updated, "action": action}, "error": None}

def _serialize(tc: TestCase) -> dict:
    return {
        "id": tc.id,
        "project_id": tc.project_id,
        "pipeline_id": tc.pipeline_id,
        "title": tc.title,
        "description": tc.description,
        "preconditions": tc.preconditions,
        "steps": tc.steps,
        "priority": tc.priority,
        "test_type": tc.test_type,
        "tags": tc.tags,
        "platform_type": tc.platform_type,
        "status": tc.status,
        "review_comment": tc.review_comment,
        "notes": getattr(tc, "notes", None),
        "reject_reason": getattr(tc, "reject_reason", None),
        "ai_score": getattr(tc, "ai_score", None),
        "ai_flags": getattr(tc, "ai_flags", None),
        "automation_level": getattr(tc, "automation_level", None),
        "module": getattr(tc, "module", None),
        "exec_script": getattr(tc, "exec_script", None),
        "compile_status": getattr(tc, "compile_status", None) or "pending",
        "compile_errors": getattr(tc, "compile_errors", None) or [],
        "execution_mode": getattr(tc, "execution_mode", None) or "hybrid",
        "step_contracts": getattr(tc, "step_contracts", None) or [],
        "created_at": tc.created_at.isoformat() if tc.created_at else None,
    }
