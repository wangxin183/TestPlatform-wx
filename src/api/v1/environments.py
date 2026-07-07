"""Environment CRUD API endpoints — manage test environment configurations."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.models import Environment
from src.utils.crypto import decrypt_dict, encrypt_dict

router = APIRouter(tags=["environments"])


@router.get("/projects/{project_id}/environments")
async def list_environments(project_id: str, db: AsyncSession = Depends(get_db)):
    query = select(Environment).where(Environment.project_id == project_id)
    query = query.order_by(Environment.created_at.desc())
    result = await db.execute(query)
    items = result.scalars().all()
    return {"success": True, "data": [_serialize(e) for e in items], "error": None}


@router.post("/projects/{project_id}/environments")
async def create_environment(project_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    name = body.get("name", "").strip()
    if not name:
        return {"success": False, "data": None, "error": "环境名称不能为空"}

    # If set as default, unset existing default
    if body.get("is_default"):
        from sqlalchemy import update
        await db.execute(
            update(Environment)
            .where(Environment.project_id == project_id)
            .values(is_default=False)
        )

    env = Environment(
        project_id=project_id,
        name=name,
        base_url=body.get("base_url", ""),
        web_url=body.get("web_url", ""),
        api_base_url=body.get("api_base_url", ""),
        variables_json=encrypt_dict(body.get("variables_json", {})),
        headers_json=encrypt_dict(body.get("headers_json", {})),
        is_default=body.get("is_default", False),
        description=body.get("description", ""),
    )
    db.add(env)
    await db.commit()
    await db.refresh(env)
    return {"success": True, "data": _serialize(env), "error": None}


@router.put("/environments/{env_id}")
async def update_environment(env_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Environment).where(Environment.id == env_id))
    env = result.scalar_one_or_none()
    if not env:
        return {"success": False, "data": None, "error": "Environment not found"}

    if body.get("is_default") and not env.is_default:
        from sqlalchemy import update
        await db.execute(
            update(Environment)
            .where(Environment.project_id == env.project_id)
            .values(is_default=False)
        )

    encrypted_fields = {"variables_json", "headers_json"}
    for field in ("name", "base_url", "web_url", "api_base_url", "is_default",
                  "description", "variables_json", "headers_json"):
        if field in body:
            val = encrypt_dict(body[field]) if field in encrypted_fields else body[field]
            setattr(env, field, val)

    db.add(env)
    await db.commit()
    return {"success": True, "data": _serialize(env), "error": None}


@router.delete("/environments/{env_id}")
async def delete_environment(env_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Environment).where(Environment.id == env_id))
    env = result.scalar_one_or_none()
    if not env:
        return {"success": False, "data": None, "error": "Environment not found"}
    await db.delete(env)
    await db.commit()
    return {"success": True, "data": None, "error": None}


def _serialize(e: Environment) -> dict:
    return {
        "id": e.id,
        "project_id": e.project_id,
        "name": e.name,
        "base_url": e.base_url,
        "web_url": e.web_url,
        "api_base_url": e.api_base_url,
        "variables_json": decrypt_dict(e.variables_json),
        "headers_json": decrypt_dict(e.headers_json),
        "is_default": e.is_default,
        "description": e.description,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }
