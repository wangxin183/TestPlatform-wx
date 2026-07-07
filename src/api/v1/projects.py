"""Project CRUD API endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.models import Project
from src.core.schemas.api import CreateProjectRequest, UpdateProjectRequest

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("")
async def create_project(body: CreateProjectRequest, db: AsyncSession = Depends(get_db)):
    p = Project(
        name=body.name,
        description=body.description,
        platform_type=body.platform_type,
        platform_config=body.platform_config,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return {"success": True, "data": _serialize(p), "error": None}


@router.get("")
async def list_projects(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    platform_type: str = Query(None),
    status: str = Query("active"),
    db: AsyncSession = Depends(get_db),
):
    query = select(Project)
    if platform_type:
        query = query.where(Project.platform_type == platform_type)
    if status:
        query = query.where(Project.status == status)
    query = query.offset((page - 1) * size).limit(size)

    result = await db.execute(query)
    items = result.scalars().all()
    return {
        "success": True,
        "data": [_serialize(p) for p in items],
        "error": None,
        "meta": {"page": page, "size": size},
    }


@router.get("/{project_id}")
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    p = result.scalar_one_or_none()
    if not p:
        return {"success": False, "data": None, "error": "Project not found"}
    return {"success": True, "data": _serialize(p), "error": None}


@router.put("/{project_id}")
async def update_project(project_id: str, body: UpdateProjectRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    p = result.scalar_one_or_none()
    if not p:
        return {"success": False, "data": None, "error": "Project not found"}
    for key, val in body.model_dump(exclude_unset=True).items():
        setattr(p, key, val)
    db.add(p)
    await db.commit()
    return {"success": True, "data": _serialize(p), "error": None}


@router.delete("/{project_id}")
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    p = result.scalar_one_or_none()
    if not p:
        return {"success": False, "data": None, "error": "Project not found"}
    await db.delete(p)
    await db.commit()
    return {"success": True, "data": None, "error": None}


def _serialize(p: Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "platform_type": p.platform_type,
        "platform_config": p.platform_config,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }
