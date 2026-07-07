"""Lightweight asyncio-based cron scheduler for recurring pipeline execution.

Uses a simple polling loop with cron expression matching since APScheduler
is not available in the environment.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import async_session_factory
from src.core.models.models import Document, Pipeline, Schedule
from src.pipeline.context import PipelineContext
from src.pipeline.orchestrator import run_pipeline
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# Global state
_scheduler_task: asyncio.Task | None = None
_jobs: dict[str, dict] = {}  # schedule_id -> {schedule, cron_parts, next_run}
_running = False
_poll_interval_seconds = 30  # check every 30 seconds


def _parse_cron(expr: str) -> dict:
    """Parse a standard 5-field cron expression into sets of allowed values.

    Supports: *, */N, N, N-M, N,M,O
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Cron expression must have 5 fields, got {len(parts)}: {expr}")

    def _parse_field(field: str, min_val: int, max_val: int) -> set[int]:
        if field == "*":
            return set(range(min_val, max_val + 1))
        values: set[int] = set()
        for part in field.split(","):
            if "/" in part:
                base, step = part.split("/")
                base_set = _parse_field(base, min_val, max_val)
                step_int = int(step)
                start = min(base_set) if base_set else min_val
                for v in range(start, max_val + 1, step_int):
                    if v <= max_val:
                        values.add(v)
            elif "-" in part:
                lo, hi = part.split("-")
                values.update(range(int(lo), int(hi) + 1))
            else:
                values.add(int(part))
        return values

    return {
        "minute": _parse_field(parts[0], 0, 59),
        "hour": _parse_field(parts[1], 0, 23),
        "day": _parse_field(parts[2], 1, 31),
        "month": _parse_field(parts[3], 1, 12),
        "weekday": _parse_field(parts[4], 0, 6),
    }


def _cron_matches(parsed: dict, dt: datetime) -> bool:
    """Check if a datetime matches the parsed cron expression."""
    return (
        dt.minute in parsed["minute"]
        and dt.hour in parsed["hour"]
        and dt.day in parsed["day"]
        and dt.month in parsed["month"]
        and (dt.weekday() in parsed["weekday"])
    )


async def load_schedules():
    """Load all enabled schedules from DB."""
    global _jobs
    _jobs.clear()

    async with async_session_factory() as session:
        result = await session.execute(
            select(Schedule).where(Schedule.enabled == True)
        )
        schedules = result.scalars().all()

        for s in schedules:
            try:
                parsed = _parse_cron(s.cron_expression)
                _jobs[s.id] = {
                    "schedule": s,
                    "cron_parts": parsed,
                    "last_run": s.last_run_at,
                }
                logger.info("schedule_loaded", name=s.name, cron=s.cron_expression, id=s.id)
            except Exception as exc:
                logger.error("schedule_parse_failed", name=s.name, error=str(exc))

    logger.info("schedules_loaded", count=len(_jobs))


def add_job(schedule: Schedule):
    """Add or update a single job."""
    parsed = _parse_cron(schedule.cron_expression)
    _jobs[schedule.id] = {
        "schedule": schedule,
        "cron_parts": parsed,
        "last_run": schedule.last_run_at,
    }
    logger.info("schedule_added", name=schedule.name, id=schedule.id)


def remove_job(schedule_id: str):
    """Remove a job."""
    _jobs.pop(schedule_id, None)
    logger.info("schedule_removed", id=schedule_id)


async def _run_scheduled_pipeline(schedule: Schedule):
    """Create and run a pipeline for a schedule."""
    async with async_session_factory() as session:
        try:
            doc_ids = schedule.document_ids or []
            if doc_ids:
                result = await session.execute(
                    select(Document).where(
                        Document.id.in_(doc_ids),
                        Document.project_id == schedule.project_id,
                    )
                )
                valid_docs = result.scalars().all()
                doc_ids = [d.id for d in valid_docs]

            if not doc_ids:
                logger.warning("schedule_no_docs", schedule_name=schedule.name)
                schedule.last_run_status = "failed"
                schedule.last_run_at = datetime.now(timezone.utc)
                session.add(schedule)
                await session.commit()
                return

            context = PipelineContext(
                pipeline_id="",
                project_id=schedule.project_id,
                document_ids=doc_ids,
            )

            p = Pipeline(
                project_id=schedule.project_id,
                current_stage="pending",
                status="pending",
                context_snapshot=context.to_json(),
            )
            session.add(p)
            await session.commit()
            await session.refresh(p)

            context.pipeline_id = p.id
            p.context_snapshot = context.to_json()
            session.add(p)

            schedule.last_run_at = datetime.now(timezone.utc)
            schedule.last_run_status = "running"
            session.add(schedule)
            await session.commit()

            result = await run_pipeline(p.id)

            schedule.last_run_status = result.get("status", "unknown")
            session.add(schedule)
            await session.commit()

        except Exception as exc:
            logger.error("schedule_run_failed", schedule_name=schedule.name, error=str(exc))
            schedule.last_run_status = "failed"
            schedule.last_run_at = datetime.now(timezone.utc)
            session.add(schedule)
            await session.commit()


async def _scheduler_loop():
    """Main polling loop — checks every poll_interval_seconds for due schedules."""
    global _running
    _running = True
    logger.info("scheduler_loop_started", interval=_poll_interval_seconds)

    while _running:
        try:
            now = datetime.now(timezone.utc)
            for job_id, job in list(_jobs.items()):
                schedule = job["schedule"]
                cron_parts = job["cron_parts"]

                # Reload schedule state periodically
                async with async_session_factory() as session:
                    result = await session.execute(
                        select(Schedule).where(Schedule.id == schedule.id)
                    )
                    db_schedule = result.scalar_one_or_none()
                    if not db_schedule or not db_schedule.enabled:
                        _jobs.pop(job_id, None)
                        continue
                    schedule = db_schedule
                    job["schedule"] = schedule

                if not schedule.enabled:
                    continue

                last_run = job.get("last_run")
                # Determine the minute window to check
                check_window = now.replace(second=0, microsecond=0)

                # Skip if already run in this minute
                if last_run and last_run.replace(second=0, microsecond=0) >= check_window:
                    continue

                if _cron_matches(cron_parts, now):
                    logger.info("schedule_triggered", name=schedule.name, cron=schedule.cron_expression)
                    job["last_run"] = now
                    asyncio.create_task(_run_scheduled_pipeline(schedule))

        except Exception as exc:
            logger.error("scheduler_loop_error", error=str(exc))

        await asyncio.sleep(_poll_interval_seconds)


def start_scheduler():
    """Start the scheduler loop. Call during app startup."""
    global _scheduler_task, _running
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_scheduler_loop())
        logger.info("scheduler_started")


def stop_scheduler():
    """Stop the scheduler loop. Call during app shutdown."""
    global _running
    _running = False
    logger.info("scheduler_stopped")


def get_scheduler():
    """Compatibility function. Returns a dummy object."""
    return None
