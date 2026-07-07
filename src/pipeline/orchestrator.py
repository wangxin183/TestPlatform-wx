"""Pipeline orchestrator — drives the pipeline through its stages.

Uses the state machine to advance stages. Each stage is executed
via its AbstractStage implementation. Results are stored in the database.

Supports pause/cancel: checks pipeline status before each stage and
stops execution if the user interrupted the pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import async_session_factory
from src.core.models.models import Document, Pipeline, PipelineStageLog, Project
from src.pipeline.context import PipelineContext
from src.pipeline.state_machine import PipelineStateMachine, STATES
from src.pipeline.stages.base import AbstractStage, StageInput
from src.pipeline.stages.ingestion import IngestionStage
from src.pipeline.stages.parsing import ParsingStage
from src.pipeline.stages.analysis import AnalysisStage
from src.pipeline.stages.generation import GenerationStage
from src.pipeline.stages.review import ReviewStage
from src.pipeline.stages.execution import ExecutionStage
from src.pipeline.stages.reporting import ReportingStage
from src.pipeline.stages.regression import RegressionStage
from src.utils.logging_config import get_logger
from src.utils.stage_logger import get_stage_logger
from src.notifications.dispatcher import dispatch_notifications
from src.ws.manager import ws_manager

logger = get_logger(__name__)

# Maps FSM state names to Stage classes
STAGE_MAP: dict[str, type[AbstractStage]] = {
    "ingestion": IngestionStage,
    "parsing": ParsingStage,
    "analysis": AnalysisStage,
    "generation": GenerationStage,
    "review": ReviewStage,
    "execution": ExecutionStage,
    "reporting": ReportingStage,
    "regression": RegressionStage,
}


async def run_stage(
    stage_name: str,
    context: PipelineContext,
    session: AsyncSession,
    project_id: str | None = None,
) -> StageOutput:
    """Run a single stage. Validates context prerequisites before execution.

    Pure stage execution — no FSM advancement, no WebSocket broadcast,
    no DB persistence. Those concerns belong to the caller.
    """
    stage_cls = STAGE_MAP.get(stage_name)
    if not stage_cls:
        raise ValueError(f"Unknown stage: {stage_name}")

    missing = []
    for field in stage_cls.required_context_fields():
        if getattr(context, field) is None:
            missing.append(field)
    if missing:
        raise ValueError(
            f"Stage '{stage_name}' requires: {missing}. "
            f"Provide them via context or run previous stages first."
        )

    # Attempt number (idempotency + tracing)
    attempt = int(context.stage_attempts.get(stage_name) or 0)
    if attempt <= 0:
        result = await session.execute(
            select(func.count(PipelineStageLog.id)).where(
                PipelineStageLog.pipeline_id == context.pipeline_id,
                PipelineStageLog.stage_name == stage_name,
            )
        )
        attempt = int(result.scalar() or 0) + 1
        context.stage_attempts[stage_name] = attempt

    idempotency_key = context.stage_idempotency.get(stage_name)
    if not idempotency_key:
        idempotency_key = f"{context.pipeline_id}:{stage_name}:{attempt}"
        context.stage_idempotency[stage_name] = idempotency_key

    stage = stage_cls(session)
    stage_input = StageInput(
        pipeline_id=context.pipeline_id,
        project_id=project_id or context.project_id,
        context=context,
        stage_attempt=attempt,
        idempotency_key=idempotency_key,
    )
    return await stage.run(stage_input)


async def _check_interrupted(session: AsyncSession, pipeline_id: str) -> str | None:
    """Check if pipeline was paused or cancelled by user.

    Returns 'paused', 'cancelled', or None if the pipeline should continue.
    """
    result = await session.execute(
        select(Pipeline.status).where(Pipeline.id == pipeline_id)
    )
    current_status = result.scalar_one_or_none()
    if current_status in ("paused", "cancelled"):
        return current_status
    return None


async def _update_pipeline_status(
    session: AsyncSession, pipeline: Pipeline,
    fsm: PipelineStateMachine, context: PipelineContext,
) -> None:
    """Persist pipeline state to DB and broadcast via WebSocket."""
    pipeline.context_snapshot = context.to_json()
    pipeline.current_stage = fsm.state
    session.add(pipeline)
    await session.commit()
    await ws_manager.broadcast_stage_change(
        pipeline.id, fsm.state, pipeline.status
    )


async def run_pipeline(pipeline_id: str) -> dict:
    """Entry point: run a pipeline through all stages.

    Returns a summary dict with final status.
    """
    async with async_session_factory() as session:
        try:
            # Load pipeline from DB with eager-loaded relationships
            result = await session.execute(
                select(Pipeline)
                .where(Pipeline.id == pipeline_id)
                .options(
                    selectinload(Pipeline.project).selectinload(Project.documents)
                )
            )
            pipeline = result.scalar_one_or_none()
            if not pipeline:
                return {"status": "error", "message": f"Pipeline {pipeline_id} not found"}

            # Load or create context
            if pipeline.context_snapshot:
                context = PipelineContext.from_json(pipeline.context_snapshot)
                if not context.project_config:
                    context.project_config = pipeline.project.platform_config or {}
            else:
                doc_ids = [d.id for d in (pipeline.project.documents or [])]
                context = PipelineContext(
                    pipeline_id=pipeline_id,
                    project_id=pipeline.project_id,
                    project_config=pipeline.project.platform_config or {},
                    document_ids=doc_ids,
                )

            # Initialize state machine
            fsm = PipelineStateMachine(pipeline_id)

            if pipeline.current_stage in STATES:
                fsm.machine.set_state(pipeline.current_stage)
            elif pipeline.current_stage == "pending":
                pass
            else:
                fsm.machine.set_state("failed")
                pipeline.current_stage = "failed"
                await session.commit()

            pipeline.status = "running"
            pipeline.started_at = pipeline.started_at or datetime.now(timezone.utc)
            await session.commit()

            # Advance from pending to first stage
            if fsm.state == "pending":
                fsm.start()

            # Run through stages until terminal or review (blocking)
            while not fsm.is_terminal and fsm.state != "review":
                # ── Interrupt check: user paused or cancelled? ──
                interrupted = await _check_interrupted(session, pipeline_id)
                if interrupted == "cancelled":
                    logger.info("pipeline_cancelled_by_user", pipeline_id=pipeline_id, stage=fsm.state)
                    pipeline.status = "cancelled"
                    pipeline.context_snapshot = context.to_json()
                    pipeline.current_stage = fsm.state
                    await session.commit()
                    await ws_manager.broadcast_stage_change(pipeline_id, fsm.state, "cancelled")
                    return {"status": "cancelled", "pipeline_id": pipeline_id, "final_stage": fsm.state}
                if interrupted == "paused":
                    logger.info("pipeline_paused_by_user", pipeline_id=pipeline_id, stage=fsm.state)
                    pipeline.status = "paused"
                    pipeline.context_snapshot = context.to_json()
                    pipeline.current_stage = fsm.state
                    pipeline.completed_at = None
                    await session.commit()
                    await ws_manager.broadcast_stage_change(pipeline_id, fsm.state, "paused")
                    return {"status": "paused", "pipeline_id": pipeline_id, "final_stage": fsm.state}

                if fsm.state not in STAGE_MAP:
                    logger.error("unknown_stage", pipeline_id=pipeline_id, stage=fsm.state)
                    break

                stage_name = fsm.state
                slog = get_stage_logger(pipeline_id, stage_name)
                slog.info(f"流水线阶段开始: {stage_name}")
                
                stage_started = datetime.now(timezone.utc)
                output = await run_stage(stage_name, context, session, pipeline.project_id)
                stage_completed = datetime.now(timezone.utc)
                
                slog.info(f"流水线阶段完成: {stage_name}, 状态={output.status}")
                if output.error:
                    slog.error(f"阶段失败: {output.error}")

                # Record stage log
                stage_log = PipelineStageLog(
                    pipeline_id=pipeline_id,
                    stage_name=stage_name,
                    status=output.status,
                    input_summary={
                        "from_state": stage_name,
                        "attempt": context.stage_attempts.get(stage_name),
                        "idempotency_key": context.stage_idempotency.get(stage_name),
                    },
                    output_data=output.data,
                    error_message=output.error,
                    started_at=stage_started,
                    completed_at=stage_completed,
                )
                session.add(stage_log)
                await session.commit()

                # ── Handle stage result ──
                if output.is_success:
                    fsm.set_stage_success(True)
                    fsm.advance()
                else:
                    # Stage failed — mark pipeline as failed
                    fsm.set_stage_success(False)
                    failed_stage = fsm.state
                    fsm.error()
                    pipeline.current_stage = failed_stage
                    pipeline.status = "failed"
                    pipeline.context_snapshot = context.to_json()
                    await session.commit()
                    await ws_manager.broadcast_stage_change(
                        pipeline_id, fsm.state, "failed"
                    )
                    logger.error(
                        "pipeline_stage_failed",
                        pipeline_id=pipeline_id,
                        stage=fsm.state,
                        error=output.error,
                    )
                    return {
                        "status": "failed",
                        "pipeline_id": pipeline_id,
                        "final_stage": fsm.state,
                        "error": output.error,
                    }

                # Save context snapshot for resume
                await _update_pipeline_status(session, pipeline, fsm, context)

                # Stop at REVIEW for human gate
                if fsm.state == "review":
                    # Execute ReviewStage before pausing (it sets test case statuses)
                    review_cls = STAGE_MAP.get("review")
                    if review_cls:
                        review_instance = review_cls(session)
                        review_input = StageInput(
                            pipeline_id=pipeline_id,
                            project_id=pipeline.project_id,
                            context=context,
                            stage_attempt=(context.stage_attempts.get("review") or 0) + 1,
                            idempotency_key=f"{pipeline_id}:review:{(context.stage_attempts.get('review') or 0) + 1}",
                        )
                        review_output = await review_instance.run(review_input)
                        # Record review stage log
                        context.stage_attempts["review"] = review_input.stage_attempt
                        context.stage_idempotency["review"] = review_input.idempotency_key or ""
                        review_log = PipelineStageLog(
                            pipeline_id=pipeline_id,
                            stage_name="review",
                            status=review_output.status,
                            input_summary={
                                "from_state": "review",
                                "attempt": review_input.stage_attempt,
                                "idempotency_key": review_input.idempotency_key,
                            },
                            output_data=review_output.data,
                            error_message=review_output.error,
                            started_at=datetime.now(timezone.utc),
                            completed_at=datetime.now(timezone.utc),
                        )
                        session.add(review_log)
                        await session.commit()

                    pipeline.current_stage = "review"
                    pipeline.status = "paused"
                    await session.commit()
                    await ws_manager.broadcast_stage_change(
                        pipeline_id, "review", "paused"
                    )
                    logger.info("pipeline_waiting_review", pipeline_id=pipeline_id)
                    try:
                        await dispatch_notifications(
                            project_id=pipeline.project_id,
                            event='review_ready',
                            pipeline_id=pipeline_id,
                            payload={'current_stage': 'review'}
                        )
                    except Exception:
                        pass
                    return {
                        "status": "paused",
                        "pipeline_id": pipeline_id,
                        "final_stage": "review",
                    }

            # Final status
            if fsm.is_terminal:
                pipeline.status = fsm.state
                pipeline.completed_at = datetime.now(timezone.utc)
            else:
                pipeline.status = fsm.state

            pipeline.context_snapshot = context.to_json()
            pipeline.current_stage = fsm.state
            await session.commit()
            await ws_manager.broadcast_stage_change(pipeline_id, fsm.state, pipeline.status)

            # Dispatch notification on completion/failure
            try:
                event = 'pipeline_completed' if pipeline.status == 'completed' else 'pipeline_failed'
                await dispatch_notifications(
                    project_id=pipeline.project_id,
                    event=event,
                    pipeline_id=pipeline_id,
                    payload={'current_stage': fsm.state, 'status': pipeline.status}
                )
            except Exception as exc:
                logger.error('notification_dispatch_failed', error=str(exc))

            return {
                "status": pipeline.status,
                "pipeline_id": pipeline_id,
                "final_stage": fsm.state,
            }

        except Exception as exc:
            logger.error("pipeline_error", pipeline_id=pipeline_id, error=str(exc))
            await session.rollback()
            # Try to mark pipeline as failed
            try:
                result = await session.execute(
                    select(Pipeline).where(Pipeline.id == pipeline_id)
                )
                p = result.scalar_one_or_none()
                if p:
                    p.status = "failed"
                    await session.commit()
            except Exception:
                pass
            await ws_manager.broadcast_stage_change(pipeline_id, "failed", "failed")
            return {"status": "failed", "pipeline_id": pipeline_id, "error": str(exc)}


async def continue_pipeline(pipeline_id: str, approved: bool, feedback: str = "") -> dict:
    """Continue a pipeline from the REVIEW stage.

    Called after human review: approved=True advances to EXECUTION,
    approved=False loops back to GENERATION with feedback.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(Pipeline).where(Pipeline.id == pipeline_id)
        )
        pipeline = result.scalar_one_or_none()
        if not pipeline:
            return {"status": "error", "message": "Pipeline not found"}

        context = PipelineContext.from_json(pipeline.context_snapshot)
        fsm = PipelineStateMachine(pipeline_id)
        fsm.state = "review"

        if approved:
            # Verify: ALL test cases must be approved before advancing
            from src.core.models.models import TestCase
            result = await session.execute(
                select(TestCase).where(
                    TestCase.pipeline_id == pipeline_id,
                    TestCase.status != "rejected",
                )
            )
            all_cases = result.scalars().all()
            not_approved = [tc for tc in all_cases if tc.status != "approved"]
            if not_approved:
                return {
                    "status": "blocked",
                    "pipeline_id": pipeline_id,
                    "message": f"还有 {len(not_approved)} 条用例未评审通过，无法进入下一阶段",
                }

            fsm.set_review_approved(True)
            fsm.advance()
            context.review_feedback = None
            # Persist approved test case IDs into pipeline context
            approved_ids = [tc.id for tc in all_cases if tc.status == "approved"]
            context.approved_test_case_ids = approved_ids
        else:
            context.review_feedback = feedback
            fsm.reject()

        pipeline.current_stage = fsm.state
        pipeline.status = "running"
        pipeline.context_snapshot = context.to_json()
        await session.commit()

        return await run_pipeline(pipeline_id)
