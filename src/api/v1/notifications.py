"""Notification config CRUD API endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.models import NotificationConfig

router = APIRouter(tags=["notifications"])


@router.get("/projects/{project_id}/notifications")
async def list_notifications(project_id: str, db: AsyncSession = Depends(get_db)):
    query = select(NotificationConfig).where(NotificationConfig.project_id == project_id)
    query = query.order_by(NotificationConfig.created_at.desc())
    result = await db.execute(query)
    items = result.scalars().all()
    return {"success": True, "data": [_serialize(n) for n in items], "error": None}


@router.post("/projects/{project_id}/notifications")
async def create_notification(project_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    name = body.get("name", "").strip()
    channel = body.get("channel", "").strip()
    if not name or not channel:
        return {"success": False, "data": None, "error": "名称和通知渠道不能为空"}

    valid_channels = {"webhook", "email", "feishu", "dingtalk", "wecom"}
    if channel not in valid_channels:
        return {"success": False, "data": None, "error": f"无效的通知渠道: {channel}, 可选: {valid_channels}"}

    nc = NotificationConfig(
        project_id=project_id,
        name=name,
        channel=channel,
        webhook_url=body.get("webhook_url", ""),
        email_to=body.get("email_to", ""),
        events_json=body.get("events_json", []),
        enabled=body.get("enabled", True),
    )
    db.add(nc)
    await db.commit()
    await db.refresh(nc)
    return {"success": True, "data": _serialize(nc), "error": None}


@router.put("/notifications/{nc_id}")
async def update_notification(nc_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(NotificationConfig).where(NotificationConfig.id == nc_id))
    nc = result.scalar_one_or_none()
    if not nc:
        return {"success": False, "data": None, "error": "Notification config not found"}

    for field in ("name", "channel", "webhook_url", "email_to", "events_json", "enabled"):
        if field in body:
            setattr(nc, field, body[field])

    db.add(nc)
    await db.commit()
    return {"success": True, "data": _serialize(nc), "error": None}


@router.delete("/notifications/{nc_id}")
async def delete_notification(nc_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(NotificationConfig).where(NotificationConfig.id == nc_id))
    nc = result.scalar_one_or_none()
    if not nc:
        return {"success": False, "data": None, "error": "Notification config not found"}
    await db.delete(nc)
    await db.commit()
    return {"success": True, "data": None, "error": None}


def _serialize(nc: NotificationConfig) -> dict:
    return {
        "id": nc.id,
        "project_id": nc.project_id,
        "name": nc.name,
        "channel": nc.channel,
        "webhook_url": nc.webhook_url,
        "email_to": nc.email_to,
        "events_json": nc.events_json,
        "enabled": nc.enabled,
        "created_at": nc.created_at.isoformat() if nc.created_at else None,
    }
