"""Review Submissions API — upload, review, batch, and stats endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.models import ReviewSubmission
from src.utils.case_parser import parse_cases
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/review", tags=["review_submissions"])

MAX_BATCH_SIZE = 50
VALID_FORMATS = {"json", "excel", "markdown", "xmind"}
FORMAT_EXT_MAP = {"excel": "xlsx", "markdown": "md", "xmind": "xmind", "json": "json"}


# ═══════════════════════════════════════════════════════
# Upload
# ═══════════════════════════════════════════════════════

@router.post("/upload")
async def upload_cases(
    file: UploadFile = File(None),
    content: str = Form(None),
    fmt: str = Form("json"),
    db: AsyncSession = Depends(get_db),
):
    """Upload test cases via file upload or text content."""
    fmt = fmt.lower()
    if fmt not in VALID_FORMATS:
        return {"success": False, "data": None, "error": f"不支持的格式: {fmt}，支持: {', '.join(VALID_FORMATS)}"}

    try:
        raw_content: bytes | str
        if file:
            raw_content = await file.read()
            if not raw_content:
                return {"success": False, "data": None, "error": "上传的文件为空"}
        elif content:
            raw_content = content
        else:
            return {"success": False, "data": None, "error": "请上传文件或输入用例内容"}

        cases, error, method = await parse_cases(fmt, raw_content)

        if error and not cases:
            return {"success": False, "data": None, "error": error}

        if not cases:
            return {"success": False, "data": None, "error": "未解析到任何用例"}

        if len(cases) > MAX_BATCH_SIZE:
            return {
                "success": False, "data": None,
                "error": f"单次最多上传 {MAX_BATCH_SIZE} 条用例，当前 {len(cases)} 条",
            }

        # Create batch and submissions
        batch_id = str(uuid.uuid4())
        now = datetime.utcnow()
        submissions = []

        for case in cases:
            sub = ReviewSubmission(
                batch_id=batch_id,
                source_format=fmt,
                title=str(case.get("title", "")),
                description=str(case.get("description", "")),
                preconditions=str(case.get("preconditions", "")),
                steps=_ensure_steps(case.get("steps", [])),
                priority=str(case.get("priority", "中")),
                test_type=str(case.get("test_type", "ui")),
                tags=case.get("tags", []),
                platform_type=str(case.get("platform_type", "")),
                status="pending_review",
                created_at=now,
                updated_at=now,
            )
            db.add(sub)
            submissions.append(sub)

        await db.commit()
        for sub in submissions:
            await db.refresh(sub)

        hint = None
        if error and cases:
            hint = error
        elif method == "ai_fallback":
            hint = "格式识别失败，已通过 AI 自动解析"

        return {
            "success": True,
            "data": {
                "batch_id": batch_id,
                "cases": [_serialize_sub(s) for s in submissions],
                "parse_method": method,
                "ai_hint": hint,
            },
            "error": None,
        }

    except Exception as e:
        logger.error("review_upload_failed", error=str(e))
        return {"success": False, "data": None, "error": f"上传失败: {str(e)}"}


# ═══════════════════════════════════════════════════════
# Batches
# ═══════════════════════════════════════════════════════

@router.get("/batches")
async def list_batches(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List all upload batches with summary counts."""
    result = await db.execute(
        select(ReviewSubmission.batch_id)
        .group_by(ReviewSubmission.batch_id)
        .order_by(func.max(ReviewSubmission.created_at).desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    batch_ids = [row[0] for row in result.all()]

    batches = []
    for bid in batch_ids:
        result2 = await db.execute(
            select(ReviewSubmission).where(ReviewSubmission.batch_id == bid)
        )
        subs = result2.scalars().all()
        if subs:
            total = len(subs)
            approved = sum(1 for s in subs if s.status == "approved")
            rejected = sum(1 for s in subs if s.status == "rejected")
            batches.append({
                "batch_id": bid,
                "source_format": subs[0].source_format,
                "total": total,
                "approved": approved,
                "rejected": rejected,
                "pending": total - approved - rejected,
                "created_at": subs[0].created_at.isoformat() if subs[0].created_at else None,
            })

    return {"success": True, "data": batches, "error": None}


@router.get("/batches/{batch_id}")
async def get_batch(batch_id: str, db: AsyncSession = Depends(get_db)):
    """Get all cases in a batch."""
    result = await db.execute(
        select(ReviewSubmission)
        .where(ReviewSubmission.batch_id == batch_id)
        .order_by(ReviewSubmission.created_at)
    )
    subs = result.scalars().all()
    return {
        "success": True,
        "data": [_serialize_sub(s) for s in subs],
        "error": None,
    }


# ═══════════════════════════════════════════════════════
# Single: Approve / Reject / Edit
# ═══════════════════════════════════════════════════════

@router.post("/submissions/{sub_id}/approve")
async def approve_submission(sub_id: str, body: dict = {}, db: AsyncSession = Depends(get_db)):
    s = await _get_sub(db, sub_id)
    if not s:
        return {"success": False, "data": None, "error": "Not found"}
    s.status = "approved"
    s.review_comment = body.get("comment")
    s.reviewed_at = datetime.utcnow()
    db.add(s)
    await db.commit()
    return {"success": True, "data": _serialize_sub(s), "error": None}


@router.post("/submissions/{sub_id}/reject")
async def reject_submission(sub_id: str, body: dict = {}, db: AsyncSession = Depends(get_db)):
    s = await _get_sub(db, sub_id)
    if not s:
        return {"success": False, "data": None, "error": "Not found"}
    s.status = "rejected"
    s.review_comment = body.get("comment")
    s.reject_reason = body.get("reason")
    s.reviewed_at = datetime.utcnow()
    db.add(s)
    await db.commit()
    return {"success": True, "data": _serialize_sub(s), "error": None}


@router.put("/submissions/{sub_id}")
async def edit_submission(sub_id: str, body: dict, db: AsyncSession = Depends(get_db)):
    s = await _get_sub(db, sub_id)
    if not s:
        return {"success": False, "data": None, "error": "Not found"}

    for field in ("title", "description", "preconditions", "steps", "priority",
                  "test_type", "tags", "platform_type", "notes"):
        if field in body:
            setattr(s, field, body[field])
    s.updated_at = datetime.utcnow()
    db.add(s)
    await db.commit()
    return {"success": True, "data": _serialize_sub(s), "error": None}


# ═══════════════════════════════════════════════════════
# Batch Operations
# ═══════════════════════════════════════════════════════

@router.post("/submissions/batch")
async def batch_action(body: dict, db: AsyncSession = Depends(get_db)):
    ids = body.get("ids", [])
    action = body.get("action")
    if action not in ("approve", "reject"):
        return {"success": False, "data": None, "error": "Invalid action"}

    new_status = "approved" if action == "approve" else "rejected"
    now = datetime.utcnow()
    updated = 0
    for sub_id in ids:
        s = await _get_sub(db, sub_id)
        if s:
            s.status = new_status
            s.reviewed_at = now
            db.add(s)
            updated += 1
    await db.commit()
    return {"success": True, "data": {"updated": updated}, "error": None}


# ═══════════════════════════════════════════════════════
# Stats
# ═══════════════════════════════════════════════════════

@router.get("/submissions/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Aggregate review stats across all upload batches."""
    result = await db.execute(select(ReviewSubmission))
    subs = result.scalars().all()

    if not subs:
        return {"success": True, "data": {
            "total": 0, "approved": 0, "rejected": 0, "pending": 0,
            "by_format": {}, "avg_ai_score": 0,
        }, "error": None}

    total = len(subs)
    approved = sum(1 for s in subs if s.status == "approved")
    rejected = sum(1 for s in subs if s.status == "rejected")

    by_format = {}
    for s in subs:
        fmt = s.source_format or "unknown"
        by_format.setdefault(fmt, {"total": 0, "approved": 0})
        by_format[fmt]["total"] += 1
        if s.status == "approved":
            by_format[fmt]["approved"] += 1

    scores = [s.ai_score for s in subs if s.ai_score is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0

    return {"success": True, "data": {
        "total": total,
        "approved": approved,
        "rejected": rejected,
        "pending": total - approved - rejected,
        "by_format": by_format,
        "avg_ai_score": avg_score,
    }, "error": None}


# ═══════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════

async def _get_sub(db: AsyncSession, sub_id: str) -> ReviewSubmission | None:
    result = await db.execute(
        select(ReviewSubmission).where(ReviewSubmission.id == sub_id)
    )
    return result.scalar_one_or_none()


def _serialize_sub(s: ReviewSubmission) -> dict:
    return {
        "id": s.id,
        "batch_id": s.batch_id,
        "source_format": s.source_format,
        "title": s.title,
        "description": s.description,
        "preconditions": s.preconditions,
        "steps": s.steps,
        "priority": s.priority,
        "test_type": s.test_type,
        "tags": s.tags,
        "platform_type": s.platform_type,
        "status": s.status,
        "review_comment": s.review_comment,
        "reviewed_by": s.reviewed_by,
        "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None,
        "notes": s.notes,
        "reject_reason": s.reject_reason,
        "ai_score": s.ai_score,
        "ai_flags": s.ai_flags,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def _ensure_steps(steps: list) -> list:
    """Ensure steps are valid dicts with step/action/expected keys."""
    if not steps:
        return []
    normalized = []
    for i, s in enumerate(steps):
        if isinstance(s, dict):
            normalized.append({
                "step": s.get("step", i + 1),
                "action": s.get("action", ""),
                "expected": s.get("expected", ""),
            })
        elif isinstance(s, str):
            normalized.append({"step": i + 1, "action": s, "expected": ""})
    return normalized
