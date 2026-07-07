"""Schedule CRUD API endpoints — manage recurring pipeline executions."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.models import Schedule
from src.scheduler.service import add_job, remove_job, load_schedules
from src.utils.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["schedules"])


@router.get("/projects/{project_id}/schedules")
async def list_schedules(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    query = select(Schedule).where(Schedule.project_id == project_id)
    query = query.order_by(Schedule.created_at.desc())
    result = await db.execute(query)
    items = result.scalars().all()
    return {"success": True, "data": [_serialize(s) for s in items], "error": None}


@router.post("/projects/{project_id}/schedules")
async def create_schedule(project_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    name = body.get("name", "").strip()
    cron = body.get("cron_expression", "").strip()
    if not name or not cron:
        return {"success": False, "data": None, "error": "名称和 Cron 表达式不能为空"}

    # Validate cron expression
    try:
        # Cron validation
        _parse_cron(cron)
    except Exception:
        return {"success": False, "data": None, "error": f"Cron 表达式无效: {cron}"}

    s = Schedule(
        project_id=project_id,
        name=name,
        cron_expression=cron,
        document_ids=body.get("document_ids", []),
        platform_type=body.get("platform_type"),
        enabled=body.get("enabled", True),
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)

    if s.enabled:
        add_job(s)

    return {"success": True, "data": _serialize(s), "error": None}


@router.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    s = result.scalar_one_or_none()
    if not s:
        return {"success": False, "data": None, "error": "Schedule not found"}

    if "name" in body:
        s.name = body["name"]
    if "cron_expression" in body:
        try:
            from src.scheduler.service import _parse_cron
            _parse_cron(body["cron_expression"])
        except Exception:
            return {"success": False, "data": None, "error": "Cron 表达式无效"}
        s.cron_expression = body["cron_expression"]
    if "document_ids" in body:
        s.document_ids = body["document_ids"]
    if "platform_type" in body:
        s.platform_type = body["platform_type"]
    if "enabled" in body:
        s.enabled = body["enabled"]

    db.add(s)
    await db.commit()

    # Update APScheduler job
    remove_job(schedule_id)
    if s.enabled:
        add_job(s)

    return {"success": True, "data": _serialize(s), "error": None}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    s = result.scalar_one_or_none()
    if not s:
        return {"success": False, "data": None, "error": "Schedule not found"}

    remove_job(schedule_id)
    await db.delete(s)
    await db.commit()

    return {"success": True, "data": None, "error": None}


@router.post("/schedules/{schedule_id}/run-now")
async def run_schedule_now(schedule_id: str, db: AsyncSession = Depends(get_db)):
    """Trigger a scheduled pipeline immediately."""
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    s = result.scalar_one_or_none()
    if not s:
        return {"success": False, "data": None, "error": "Schedule not found"}

    from src.scheduler.service import _run_scheduled_pipeline
    import asyncio
    asyncio.create_task(_run_scheduled_pipeline(s))

    return {"success": True, "data": {"message": "已触发执行"}, "error": None}


def _serialize(s: Schedule) -> dict:
    return {
        "id": s.id,
        "project_id": s.project_id,
        "name": s.name,
        "cron_expression": s.cron_expression,
        "document_ids": s.document_ids,
        "platform_type": s.platform_type,
        "enabled": s.enabled,
        "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
        "last_run_status": s.last_run_status,
        "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
