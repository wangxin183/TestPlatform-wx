"""Defect API endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.models import Defect

router = APIRouter(tags=["defects"])


@router.get("/projects/{project_id}/defects")
async def list_defects(
    project_id: str,
    severity: str = Query(None),
    status: str = Query(None),
    size: int = Query(50),
    db: AsyncSession = Depends(get_db),
):
    query = select(Defect).where(Defect.project_id == project_id)
    if severity:
        query = query.where(Defect.severity == severity)
    if status:
        query = query.where(Defect.status == status)
    query = query.order_by(Defect.created_at.desc()).limit(size)

    result = await db.execute(query)
    items = result.scalars().all()
    return {"success": True, "data": [_serialize(d) for d in items], "error": None}


@router.patch("/defects/{defect_id}")
async def update_defect(defect_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Defect).where(Defect.id == defect_id))
    d = result.scalar_one_or_none()
    if not d:
        return {"success": False, "data": None, "error": "Not found"}
    for key in ("status", "severity", "description"):
        if key in body:
            setattr(d, key, body[key])
    db.add(d)
    await db.commit()
    return {"success": True, "data": _serialize(d), "error": None}


def _serialize(d: Defect) -> dict:
    return {
        "id": d.id,
        "execution_result_id": d.execution_result_id,
        "project_id": d.project_id,
        "execution_id": d.execution_id,
        "title": d.title,
        "description": d.description,
        "severity": d.severity,
        "status": d.status,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }
