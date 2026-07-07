"""Notification dispatcher — sends alerts via webhook/email/IM channels."""

from __future__ import annotations

import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone

import httpx

from src.core.database import async_session_factory
from src.core.models.models import NotificationConfig, Pipeline
from src.core.config import settings
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


async def dispatch_notifications(
    project_id: str,
    event: str,
    pipeline_id: str = "",
    payload: dict | None = None,
):
    """Send notifications to all enabled configs matching event type."""
    async with async_session_factory() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(NotificationConfig).where(
                NotificationConfig.project_id == project_id,
                NotificationConfig.enabled == True,
            )
        )
        configs = result.scalars().all()

        for cfg in configs:
            events = cfg.events_json or []
            if event not in events:
                continue

            await _send_notification(cfg, event, pipeline_id, payload or {})


async def _send_notification(
    cfg: NotificationConfig,
    event: str,
    pipeline_id: str,
    payload: dict,
):
    """Send a single notification through the configured channel."""
    message = _build_message(event, pipeline_id, payload)

    try:
        if cfg.channel == "webhook" and cfg.webhook_url:
            await _send_webhook(cfg.webhook_url, message)
        elif cfg.channel == "email" and cfg.email_to:
            await _send_email(cfg.email_to, message)
        elif cfg.channel in ("feishu", "dingtalk", "wecom") and cfg.webhook_url:
            await _send_im(cfg.channel, cfg.webhook_url, message)
        else:
            logger.warning("notification_unsupported_channel", channel=cfg.channel)
            return

        logger.info("notification_sent", channel=cfg.channel, event=event, name=cfg.name)
    except Exception as exc:
        logger.error("notification_failed", channel=cfg.channel, error=str(exc))


def _build_message(event: str, pipeline_id: str, payload: dict) -> dict:
    """Build a structured notification message."""
    title_map = {
        "pipeline_completed": "✅ 流水线执行完成",
        "pipeline_failed": "❌ 流水线执行失败",
        "pipeline_started": "🚀 流水线已启动",
        "defect_critical": "🔴 发现严重缺陷",
        "review_ready": "👀 用例待评审",
    }
    return {
        "event": event,
        "title": title_map.get(event, event),
        "pipeline_id": pipeline_id,
        "time": datetime.now(timezone.utc).isoformat(),
        "detail": payload,
    }


async def _send_webhook(url: str, message: dict):
    """Send JSON payload to a generic webhook."""
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, json=message)


async def _send_email(to: str, message: dict):
    """Send an email notification."""
    smtp_host = getattr(settings, "smtp_host", "") or "localhost"
    smtp_port = int(getattr(settings, "smtp_port", 25) or 25)
    smtp_user = getattr(settings, "smtp_user", "") or ""
    smtp_pass = getattr(settings, "smtp_pass", "") or ""
    from_addr = getattr(settings, "smtp_from", "noreply@testplatform.local") or "noreply@testplatform.local"

    body = f"{message['title']}\n\n时间: {message['time']}\n流水线: {message['pipeline_id']}\n\n{json.dumps(message['detail'], ensure_ascii=False, indent=2)}"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = message["title"]
    msg["From"] = from_addr
    msg["To"] = to

    if smtp_host == "localhost":
        logger.info("email_skipped_no_smtp")
        return

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        if smtp_user:
            server.starttls()
            server.login(smtp_user, smtp_pass)
        server.sendmail(from_addr, [to], msg.as_string())


async def _send_im(channel: str, webhook_url: str, message: dict):
    """Send notification to IM platforms (Feishu/DingTalk/WeCom)."""
    if channel == "feishu":
        body = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"content": message["title"], "tag": "plain_text"}},
                "elements": [
                    {"tag": "div", "text": {"content": f"流水线: {message['pipeline_id']}\n时间: {message['time']}", "tag": "lark_md"}}
                ]
            }
        }
    elif channel == "dingtalk":
        body = {
            "msgtype": "markdown",
            "markdown": {
                "title": message["title"],
                "text": f"### {message['title']}\n- 流水线: {message['pipeline_id']}\n- 时间: {message['time']}"
            }
        }
    elif channel == "wecom":
        body = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"## {message['title']}\n> 流水线: {message['pipeline_id']}\n> 时间: {message['time']}"
            }
        }
    else:
        body = message

    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(webhook_url, json=body)
