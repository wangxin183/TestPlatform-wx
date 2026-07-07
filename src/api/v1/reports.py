"""Report API endpoints — list, view, download, and generate reports on demand."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.models import (
    Report, RegressionCase, Execution, ExecutionResult, Defect,
    PipelineStageLog, Pipeline, Project,
)
from src.report.exporter import build_report_data, export_html
from src.utils.file_storage import read as read_file
from src.utils.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["reports"])


@router.get("/projects/{project_id}/reports")
async def list_reports(
    project_id: str,
    size: int = Query(20),
    db: AsyncSession = Depends(get_db),
):
    query = select(Report).where(Report.project_id == project_id)
    query = query.order_by(Report.generated_at.desc()).limit(size)
    result = await db.execute(query)
    items = result.scalars().all()
    return {"success": True, "data": [_serialize(r) for r in items], "error": None}


@router.get("/reports/{report_id}/view", response_class=HTMLResponse)
async def view_report(report_id: str, db: AsyncSession = Depends(get_db)):
    """Render and return an HTML report inline."""
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    # If HTML file exists, serve it directly
    if report.format == "html" and report.file_path:
        content = await read_file(report.file_path)
        if content:
            return HTMLResponse(content=content.decode("utf-8", errors="replace"))

    # Otherwise, re-render from summary
    summary = report.summary_json or {}
    return HTMLResponse(content=f"<html><body><pre>{summary}</pre></body></html>")


@router.get("/reports/{report_id}/download")
async def download_report(report_id: str, db: AsyncSession = Depends(get_db)):
    """Download a report file by its storage path."""
    from io import BytesIO
    from fastapi.responses import StreamingResponse

    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if not report.file_path:
        raise HTTPException(status_code=404, detail="Report file not found")

    content = await read_file(report.file_path)
    if content is None:
        raise HTTPException(status_code=404, detail="Report file missing")

    ext = report.format or "html"
    mime_map = {"html": "text/html", "json": "application/json", "pdf": "application/pdf"}
    filename = f"report_{report_id[:8]}.{ext}"

    return StreamingResponse(
        BytesIO(content),
        media_type=mime_map.get(ext, "application/octet-stream"),
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/projects/{project_id}/reports/generate")
async def generate_report_on_demand(project_id: str, db: AsyncSession = Depends(get_db)):
    """Generate an on-demand execution report for a project.

    Aggregates all execution results and defects across all pipelines.
    """
    # Fetch project info
    proj = await db.execute(select(Project).where(Project.id == project_id))
    project = proj.scalar_one_or_none()
    if not project:
        return {"success": False, "data": None, "error": "Project not found"}

    # Fetch all execution results for this project
    exec_query = await db.execute(
        select(Execution).where(Execution.project_id == project_id)
    )
    executions = exec_query.scalars().all()

    all_results: list[dict] = []
    all_defects: list[dict] = []
    all_stage_logs: list[dict] = []
    last_pipeline_id = ""

    for ex in executions:
        last_pipeline_id = ex.pipeline_id or last_pipeline_id

        r = await db.execute(
            select(ExecutionResult).where(ExecutionResult.execution_id == ex.id)
        )
        for er in r.scalars().all():
            all_results.append(_serialize_result(er))

        d = await db.execute(
            select(Defect).where(Defect.execution_id == ex.id)
        )
        for defect in d.scalars().all():
            all_defects.append(_serialize_defect(defect))

    if last_pipeline_id:
        sl = await db.execute(
            select(PipelineStageLog)
            .where(PipelineStageLog.pipeline_id == last_pipeline_id)
            .order_by(PipelineStageLog.created_at)
        )
        for log in sl.scalars().all():
            all_stage_logs.append({
                "stage_name": log.stage_name,
                "status": log.status,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "completed_at": log.completed_at.isoformat() if log.completed_at else None,
                "error_message": log.error_message,
            })

    total = len(all_results)
    passed = sum(1 for r in all_results if r["status"] == "passed")

    report_data = build_report_data(
        project_name=project.name,
        platform_type=project.platform_type,
        pipeline_id=last_pipeline_id,
        execution_summary={
            "total_cases": total,
            "passed": passed,
            "failed": sum(1 for r in all_results if r["status"] == "failed"),
            "errors": sum(1 for r in all_results if r["status"] == "error"),
            "pass_rate": round(passed / total * 100, 2) if total > 0 else 0,
        },
        execution_results=all_results,
        defects=all_defects,
        stage_logs=all_stage_logs,
    )

    # Save HTML report
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    html_path = f"reports/{project_id}/report_{ts}.html"
    await export_html(report_data, html_path)

    report = Report(
        project_id=project_id,
        report_type="execution",
        format="html",
        file_path=html_path,
        summary_json=report_data.get("summary", {}),
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    return {
        "success": True,
        "data": _serialize(report),
        "error": None,
    }


@router.get("/projects/{project_id}/regression")
async def list_regression(
    project_id: str,
    size: int = Query(50),
    db: AsyncSession = Depends(get_db),
):
    query = select(RegressionCase).where(RegressionCase.project_id == project_id)
    query = query.order_by(RegressionCase.created_at.desc()).limit(size)
    result = await db.execute(query)
    items = result.scalars().all()
    return {"success": True, "data": [_serialize_reg(r) for r in items], "error": None}


# ══════════════════════════════
# Serializers
# ══════════════════════════════

def _serialize(r: Report) -> dict:
    return {
        "id": r.id,
        "pipeline_id": r.pipeline_id,
        "execution_id": r.execution_id,
        "project_id": r.project_id,
        "report_type": r.report_type,
        "format": r.format,
        "file_path": r.file_path,
        "summary_json": r.summary_json,
        "generated_at": r.generated_at.isoformat() if r.generated_at else None,
    }


def _serialize_reg(r: RegressionCase) -> dict:
    return {
        "id": r.id,
        "project_id": r.project_id,
        "pipeline_id": r.pipeline_id,
        "source_case_id": r.source_case_id,
        "title": r.title,
        "priority": r.priority,
        "selection_reason": r.selection_reason,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _serialize_result(er: ExecutionResult) -> dict:
    return {
        "id": er.id,
        "test_case_id": er.test_case_id,
        "attempt": er.attempt,
        "status": er.status,
        "duration_ms": er.duration_ms,
        "error_message": er.error_message,
        "failure_reason": er.failure_reason,
        "test_type": getattr(er, "test_type", ""),
        "priority": getattr(er, "priority", ""),
        "title": getattr(er, "title", er.test_case_id),
        "executed_at": er.executed_at.isoformat() if er.executed_at else None,
    }


def _serialize_defect(d: Defect) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "description": d.description,
        "severity": d.severity,
        "status": d.status,
    }
