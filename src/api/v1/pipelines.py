"""Pipeline API endpoints."""

import asyncio

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.models import Document, Pipeline, PipelineStageLog, TestCase
from src.core.schemas.api import CreatePipelineRequest
from src.pipeline.orchestrator import run_pipeline, run_stage, STAGE_MAP
from src.pipeline.context import PipelineContext
from src.utils.logging_config import get_logger
from src.ws.manager import ws_manager

logger = get_logger(__name__)
router = APIRouter(tags=["pipelines"])


def _run_background(pipeline_id: str) -> None:
    """Create a background task with error logging for pipeline execution."""
    async def _run():
        try:
            await run_pipeline(pipeline_id)
        except Exception as exc:
            logger.error("background_pipeline_failed", pipeline_id=pipeline_id, error=str(exc))
            await ws_manager.broadcast(pipeline_id, {"type": "error", "message": str(exc)})
    asyncio.create_task(_run())


@router.post("/projects/{project_id}/pipelines")
async def create_pipeline(project_id: str, body: CreatePipelineRequest, db: AsyncSession = Depends(get_db)):
    custom_prompt = body.custom_prompt
    document_ids = body.document_ids

    if not document_ids:
        return {"success": False, "data": None, "error": "请选择至少一个文档"}

    # Validate documents exist and belong to project
    result = await db.execute(
        select(Document).where(
            Document.id.in_(document_ids),
            Document.project_id == project_id,
        )
    )
    valid_docs = result.scalars().all()
    if len(valid_docs) != len(document_ids):
        return {"success": False, "data": None, "error": "部分文档不存在或不属于该项目"}

    # Validate LLM API key is configured
    import os
    from src.core.config import settings
    provider_config = settings.llm_providers_config.get("providers", {}).get(
        settings.llm_providers_config.get("router", {}).get("default_provider", "deepseek"), {}
    )
    api_key_env = provider_config.get("api_key_env", "DEEPSEEK_API_KEY")
    if not os.environ.get(api_key_env):
        return {"success": False, "data": None, "error": f"LLM API Key 未配置，请设置环境变量 {api_key_env}"}

    # Store custom_prompt + document_ids in context snapshot
    from src.pipeline.context import PipelineContext
    context = PipelineContext(
        pipeline_id="",
        project_id=project_id,
        document_ids=document_ids,
    )
    context.custom_prompt = custom_prompt

    p = Pipeline(
        project_id=project_id,
        current_stage="pending",
        status="pending",
        context_snapshot=context.to_json(),
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)

    # Update context with correct pipeline_id
    context.pipeline_id = p.id
    p.context_snapshot = context.to_json()
    db.add(p)
    await db.commit()

    _run_background(p.id)

    return {"success": True, "data": _serialize(p), "error": None}


@router.get("/projects/{project_id}/pipelines")
async def list_pipelines(
    project_id: str,
    status: str = Query(None),
    size: int = Query(20),
    db: AsyncSession = Depends(get_db),
):
    query = select(Pipeline).where(Pipeline.project_id == project_id)
    if status:
        query = query.where(Pipeline.status == status)
    query = query.order_by(Pipeline.created_at.desc()).limit(size)

    result = await db.execute(query)
    items = result.scalars().all()
    return {"success": True, "data": [_serialize(p) for p in items], "error": None}


@router.get("/pipelines")
async def list_all_pipelines(size: int = Query(20), db: AsyncSession = Depends(get_db)):
    query = select(Pipeline).order_by(Pipeline.created_at.desc()).limit(size)
    result = await db.execute(query)
    items = result.scalars().all()
    return {"success": True, "data": [_serialize(p) for p in items], "error": None}


@router.get("/pipelines/{pipeline_id}")
async def get_pipeline(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Pipeline).where(Pipeline.id == pipeline_id))
    p = result.scalar_one_or_none()
    if not p:
        return {"success": False, "data": None, "error": "Not found"}

    duration = await _calc_pipeline_duration(pipeline_id, db)
    data = _serialize(p)
    data["duration_seconds"] = duration
    return {"success": True, "data": data, "error": None}


@router.get("/pipelines/{pipeline_id}/stages")
async def get_pipeline_stages(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PipelineStageLog)
        .where(PipelineStageLog.pipeline_id == pipeline_id)
        .order_by(PipelineStageLog.created_at)
    )
    logs = result.scalars().all()
    return {"success": True, "data": [_serialize_log(l) for l in logs], "error": None}


@router.post("/pipelines/{pipeline_id}/pause")
async def pause_pipeline(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Pipeline).where(Pipeline.id == pipeline_id))
    p = result.scalar_one_or_none()
    if not p:
        return {"success": False, "data": None, "error": "Not found"}
    p.status = "paused"
    db.add(p)
    await db.commit()
    return {"success": True, "data": _serialize(p), "error": None}


@router.post("/pipelines/{pipeline_id}/resume")
async def resume_pipeline(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Pipeline).where(Pipeline.id == pipeline_id))
    p = result.scalar_one_or_none()
    if not p:
        return {"success": False, "data": None, "error": "Not found"}

    # Review stage: check if all test cases are approved before advancing
    if p.current_stage == "review":
        from src.core.models.models import TestCase
        result = await db.execute(
            select(TestCase).where(
                TestCase.pipeline_id == pipeline_id,
                TestCase.status != "rejected",
            )
        )
        all_cases = result.scalars().all()
        not_approved = [tc for tc in all_cases if tc.status != "approved"]
        if not_approved:
            return {
                "success": False,
                "data": {
                    "stage": "review",
                    "not_approved_count": len(not_approved),
                    "total": len(all_cases),
                },
                "error": f"当前在用例评审阶段，还有 {len(not_approved)} 条用例未评审通过。请在评审面板中逐一评审，全部通过后再进入下一阶段。",
            }

        # All approved — advance to execution
        from src.pipeline.orchestrator import continue_pipeline
        result = await continue_pipeline(pipeline_id, approved=True)
        return {"success": True, "data": result, "error": None}

    p.status = "running"
    db.add(p)
    await db.commit()

    _run_background(p.id)

    return {"success": True, "data": _serialize(p), "error": None}


@router.post("/pipelines/{pipeline_id}/cancel")
async def cancel_pipeline(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Pipeline).where(Pipeline.id == pipeline_id))
    p = result.scalar_one_or_none()
    if not p:
        return {"success": False, "data": None, "error": "Not found"}
    p.status = "cancelled"
    db.add(p)
    await db.commit()
    return {"success": True, "data": _serialize(p), "error": None}


@router.post("/pipelines/{pipeline_id}/advance")
async def advance_from_review(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    """Advance pipeline from review stage to execution.

    Only allowed if ALL test cases for this pipeline are approved.
    """
    from src.pipeline.orchestrator import continue_pipeline

    # Load pipeline
    result = await db.execute(select(Pipeline).where(Pipeline.id == pipeline_id))
    p = result.scalar_one_or_none()
    if not p:
        return {"success": False, "data": None, "error": "流水线不存在"}
    if p.current_stage != "review":
        return {"success": False, "data": None, "error": "流水线不在评审阶段"}

    # Check all test cases are approved
    from src.core.models.models import TestCase
    result = await db.execute(
        select(TestCase).where(
            TestCase.pipeline_id == pipeline_id,
            TestCase.status != "rejected",
        )
    )
    all_cases = result.scalars().all()

    not_approved = [tc for tc in all_cases if tc.status != "approved"]
    if not_approved:
        return {
            "success": False,
            "data": {"not_approved_count": len(not_approved)},
            "error": f"还有 {len(not_approved)} 条用例未评审通过，全部通过后才能进入下一阶段",
        }

    # Re-check after DB session complete — also verify in orchestrator
    result = await continue_pipeline(pipeline_id, approved=True)
    return {"success": True, "data": result, "error": None}


@router.post("/pipelines/{pipeline_id}/reject-all")
async def reject_all_and_regenerate(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    """Reject all test cases and loop back to generation stage with feedback."""
    from src.pipeline.orchestrator import continue_pipeline

    # Load pipeline
    result = await db.execute(select(Pipeline).where(Pipeline.id == pipeline_id))
    p = result.scalar_one_or_none()
    if not p:
        return {"success": False, "data": None, "error": "流水线不存在"}
    if p.current_stage != "review":
        return {"success": False, "data": None, "error": "流水线不在评审阶段"}

    # Reject all test cases
    from src.core.models.models import TestCase
    from sqlalchemy import update
    await db.execute(
        update(TestCase)
        .where(TestCase.pipeline_id == pipeline_id)
        .values(status="rejected")
    )
    await db.commit()

    result = await continue_pipeline(pipeline_id, approved=False, feedback="全部用例评审不通过，需重新生成")
    return {"success": True, "data": result, "error": None}


# ── Standalone stage execution ──

@router.post("/stages/{stage_name}/run")
async def run_stage_standalone(stage_name: str, body: dict, db: AsyncSession = Depends(get_db)):
    """Run a single stage independently. No pipeline required.

    Body: {
        "project_id": "...",
        "context": {"raw_texts": {...}, ...}
    }
    """
    project_id = body.get("project_id")
    if not project_id:
        return {"success": False, "data": None, "error": "project_id is required"}

    # Build context from request body
    context = PipelineContext(pipeline_id="", project_id=project_id)
    ctx_overrides = body.get("context", {})
    for key, value in ctx_overrides.items():
        if hasattr(context, key):
            setattr(context, key, value)

    try:
        output = await run_stage(stage_name, context, db, project_id)
    except ValueError as e:
        return {"success": False, "data": None, "error": str(e)}

    # Save stage log
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    stage_log = PipelineStageLog(
        pipeline_id="",
        stage_name=stage_name,
        status=output.status,
        input_summary={"mode": "standalone", "project_id": project_id},
        output_data=output.data,
        error_message=output.error,
        started_at=now,
        completed_at=now,
    )
    db.add(stage_log)
    await db.commit()

    return {
        "success": output.is_success,
        "data": {
            "stage_name": stage_name,
            "status": output.status,
            "output": output.data,
            "error": output.error,
            "context": {k: v for k, v in context.to_dict().items() if v is not None},
        },
        "error": None,
    }


# ── Pipeline retry from failed stage ──

@router.post("/pipelines/{pipeline_id}/retry")
async def retry_pipeline(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    """Retry a failed pipeline from the failed stage, running through to completion.

    Only works when pipeline.status == 'failed'.
    """
    result = await db.execute(select(Pipeline).where(Pipeline.id == pipeline_id))
    p = result.scalar_one_or_none()
    if not p:
        return {"success": False, "data": None, "error": "Pipeline not found"}
    if p.status != "failed":
        return {"success": False, "data": None, "error": "Pipeline is not in failed state"}
    if not p.current_stage or p.current_stage == "failed":
        return {"success": False, "data": None, "error": "Cannot determine which stage failed"}

    retry_from = p.current_stage
    p.status = "running"
    db.add(p)
    await db.commit()

    _run_background(p.id)

    return {"success": True, "data": {"retry_from": retry_from}, "error": None}


# ── Available stages with contracts ──

@router.get("/stages")
async def list_available_stages():
    """List all available stages with their input/output contracts."""
    stages = []
    for name, cls in STAGE_MAP.items():
        stages.append({
            "name": name,
            "required_context": cls.required_context_fields(),
            "produced_context": cls.produced_context_fields(),
        })
    return {"success": True, "data": stages, "error": None}



@router.get("/pipelines/{pipeline_id}/test-cases")
async def get_pipeline_test_cases(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    """Get all test cases generated for a pipeline."""
    result = await db.execute(
        select(TestCase).where(TestCase.pipeline_id == pipeline_id)
        .order_by(TestCase.created_at)
    )
    cases = result.scalars().all()
    return {"success": True, "data": [_serialize_tc(c) for c in cases], "error": None}

async def _calc_pipeline_duration(pipeline_id: str, db: AsyncSession) -> float:
    """Sum completed stage durations (completed_at - started_at) for a pipeline."""
    result = await db.execute(
        select(PipelineStageLog)
        .where(PipelineStageLog.pipeline_id == pipeline_id)
    )
    logs = result.scalars().all()
    total = 0.0
    for log in logs:
        if log.started_at and log.completed_at:
            total += (log.completed_at - log.started_at).total_seconds()
    return total


@router.get("/pipelines/{pipeline_id}/dag")
async def get_pipeline_dag(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    """Return DAG structure for pipeline visualization."""
    # Fetch pipeline
    result = await db.execute(select(Pipeline).where(Pipeline.id == pipeline_id))
    p = result.scalar_one_or_none()
    if not p:
        return {"success": False, "data": None, "error": "Pipeline not found"}

    # Fetch stage logs
    result = await db.execute(
        select(PipelineStageLog)
        .where(PipelineStageLog.pipeline_id == pipeline_id)
        .order_by(PipelineStageLog.created_at)
    )
    logs = result.scalars().all()

    # Define the DAG structure based on FSM
    stage_order = [
        ("ingestion", "文档摄入"),
        ("parsing", "文档解析"),
        ("analysis", "需求分析"),
        ("generation", "用例生成"),
        ("review", "人工评审"),
        ("execution", "测试执行"),
        ("reporting", "报告生成"),
        ("regression", "回归筛选"),
    ]

    log_map = {l.stage_name: l for l in logs}
    nodes = []
    for name, label in stage_order:
        log = log_map.get(name)
        status = "pending"
        duration = None
        if log:
            status = log.status
            if log.started_at and log.completed_at:
                duration = round((log.completed_at - log.started_at).total_seconds(), 1)
        nodes.append({
            "id": name,
            "label": label,
            "status": status,
            "duration_seconds": duration,
        })

    edges = []
    for i in range(len(nodes) - 1):
        edges.append({"from": nodes[i]["id"], "to": nodes[i+1]["id"]})
    # Add review reject loop-back
    edges.append({"from": "review", "to": "generation", "type": "reject"})

    # Progress — running stages count as 50%
    completed = sum(1 for n in nodes if n["status"] == "completed")
    running_n = sum(1 for n in nodes if n["status"] == "running")
    failed_n = sum(1 for n in nodes if n["status"] == "failed")
    total = len(nodes)
    progress = round((completed + running_n * 0.5) / total * 100, 1) if total > 0 else 0

    return {
        "success": True,
        "data": {
            "nodes": nodes,
            "edges": edges,
            "progress": progress,
            "completed_count": completed,
            "running_count": running_n,
            "failed_count": failed_n,
            "total_count": total,
            "pipeline_status": p.status,
            "current_stage": p.current_stage,
        },
        "error": None,
    }

@router.get("/pipelines/{pipeline_id}/review-stats")
async def get_review_stats(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    """Get review statistics for a pipeline."""
    result = await db.execute(
        select(TestCase).where(TestCase.pipeline_id == pipeline_id)
    )
    cases = result.scalars().all()
    if not cases:
        return {"success": True, "data": {
            "total": 0, "approved": 0, "rejected": 0,
            "by_type": {}, "reject_reasons": {},
            "high_risk_count": 0, "high_risk_approved": 0,
            "avg_ai_score": 0,
        }, "error": None}

    total = len(cases)
    approved = sum(1 for c in cases if c.status == "approved")
    rejected = sum(1 for c in cases if c.status == "rejected")

    by_type = {}
    for c in cases:
        tt = c.test_type or "unknown"
        if tt not in by_type:
            by_type[tt] = {"total": 0, "approved": 0}
        by_type[tt]["total"] += 1
        if c.status == "approved":
            by_type[tt]["approved"] += 1

    reject_reasons = {}
    for c in cases:
        if c.status == "rejected" and getattr(c, "reject_reason", None):
            rr = c.reject_reason
            reject_reasons[rr] = reject_reasons.get(rr, 0) + 1

    high_risk = sum(1 for c in cases if getattr(c, "ai_score", None) is not None and c.ai_score < 40)
    high_risk_approved = sum(1 for c in cases if getattr(c, "ai_score", None) is not None and c.ai_score < 40 and c.status == "approved")

    scores = [c.ai_score for c in cases if getattr(c, "ai_score", None) is not None]
    avg_ai_score = round(sum(scores) / len(scores), 1) if scores else 0

    return {"success": True, "data": {
        "total": total, "approved": approved, "rejected": rejected,
        "by_type": by_type, "reject_reasons": reject_reasons,
        "high_risk_count": high_risk, "high_risk_approved": high_risk_approved,
        "avg_ai_score": avg_ai_score,
    }, "error": None}


@router.post("/pipelines/{pipeline_id}/retry-rejected")
async def retry_rejected_cases(pipeline_id: str, db: AsyncSession = Depends(get_db)):
    """Re-generate only rejected test cases."""
    # Find rejected cases
    result = await db.execute(
        select(TestCase).where(
            TestCase.pipeline_id == pipeline_id,
            TestCase.status == "rejected",
        )
    )
    rejected = result.scalars().all()
    if not rejected:
        return {"success": False, "data": None, "error": "No rejected cases to retry"}

    # Collect reject reasons for feedback
    reasons = []
    for tc in rejected:
        if getattr(tc, "reject_reason", None):
            reasons.append(f"用例「{tc.title}」: {tc.reject_reason}")
        # Mark old cases as deprecated, store id for regenerated_from
        tc.status = "deprecated"
        db.add(tc)

    await db.commit()

    # Update pipeline context with retry feedback
    result = await db.execute(select(Pipeline).where(Pipeline.id == pipeline_id))
    p = result.scalar_one_or_none()
    if p and p.context_snapshot:
        context = PipelineContext.from_json(p.context_snapshot)
        feedback = "驳回原因汇总:\n" + "\n".join(reasons[:10])
        context.review_feedback = feedback
        context.generated_test_cases = None  # clear old cases for regeneration
        p.context_snapshot = context.to_json()
        p.current_stage = "generation"
        p.status = "running"
        db.add(p)
        await db.commit()

        # Kick off pipeline from generation stage
        _run_background(pipeline_id)

        return {"success": True, "data": {"retry_count": len(rejected), "feedback": feedback}, "error": None}

    return {"success": False, "data": None, "error": "Pipeline not found"}


def _serialize(p: Pipeline) -> dict:
    return {
        "id": p.id,
        "project_id": p.project_id,
        "current_stage": p.current_stage,
        "status": p.status,
        "celery_task_id": p.celery_task_id,
        "started_at": p.started_at.isoformat() if p.started_at else None,
        "completed_at": p.completed_at.isoformat() if p.completed_at else None,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _serialize_log(l: PipelineStageLog) -> dict:
    return {
        "id": l.id,
        "pipeline_id": l.pipeline_id,
        "stage_name": l.stage_name,
        "status": l.status,
        "output_data": l.output_data,
        "error_message": l.error_message,
        "started_at": l.started_at.isoformat() if l.started_at else None,
        "completed_at": l.completed_at.isoformat() if l.completed_at else None,
    }


def _serialize_tc(tc) -> dict:
    return {
        "id": tc.id,
        "pipeline_id": tc.pipeline_id,
        "project_id": tc.project_id,
        "title": tc.title,
        "description": tc.description,
        "preconditions": tc.preconditions,
        "steps": tc.steps,
        "priority": tc.priority,
        "test_type": tc.test_type,
        "tags": tc.tags,
        "platform_type": tc.platform_type,
        "status": tc.status,
        "review_comment": getattr(tc, "review_comment", None),
        "reject_reason": getattr(tc, "reject_reason", None),
        "ai_score": getattr(tc, "ai_score", None),
        "ai_flags": getattr(tc, "ai_flags", None),
        "created_at": tc.created_at.isoformat() if tc.created_at else None,
    }
