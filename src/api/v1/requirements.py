"""Requirement Management API — upload, list, download, auto-process."""

from __future__ import annotations
import asyncio
import json
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import async_session_factory, get_db
from src.core.models.models import Document, RequirementTask
from src.pipeline.context import PipelineContext
from src.pipeline.stages.analysis import AnalysisStage
from src.pipeline.stages.base import StageInput
from src.pipeline.stages.ingestion import IngestionStage
from src.pipeline.stages.parsing import ParsingStage
from src.utils.file_storage import exists as storage_exists
from src.utils.file_storage import read as storage_read
from src.utils.file_storage import save as storage_save
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/requirements", tags=["requirements"])

VALID_FORMATS = {"json", "md", "docx", "pdf", "url"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


@router.post("/upload")
async def upload_requirement(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(None),
    url: str = Form(None),
    name: str = Form(""),
    project_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Upload a requirement document (file or URL) and trigger auto-processing."""
    if not file and not url:
        return {"success": False, "data": None, "error": "请上传文件或输入URL"}

    if file and url:
        return {"success": False, "data": None, "error": "文件上传和URL链接请二选一"}

    try:
        if file:
            content = await file.read()
            if len(content) > MAX_FILE_SIZE:
                return {"success": False, "data": None, "error": f"文件大小超过限制（{MAX_FILE_SIZE // 1024 // 1024}MB）"}

            ext = Path(file.filename).suffix.lower().lstrip(".")
            fmt = ext if ext in ("json", "md", "docx", "pdf") else "txt"
            task_name = name or file.filename

            rel_path = f"requirements/{project_id or 'global'}/{uuid.uuid4().hex[:8]}_{file.filename}"
            await storage_save(rel_path, content)
            source_url_val = None
            char_count = len(content.decode("utf-8", errors="replace"))
        else:
            fmt = "url"
            task_name = name or url[:80]
            rel_path = None
            content = url.encode("utf-8")
            source_url_val = url
            char_count = 0

        task = RequirementTask(
            project_id=project_id or "",
            name=task_name,
            source_format=fmt,
            source_url=source_url_val,
            file_path=rel_path,
            char_count=char_count,
            chunk_count=0,
            chunk_status="pending",
            req_count=0,
            status="pending",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

        # Schedule background processing
        task_id = task.id
        background_tasks.add_task(
            _process_requirement,
            task_id=task_id,
            file_path=rel_path,
            filename=task_name,
            file_type=fmt,
            project_id=project_id or "",
            source_url=source_url_val,
            content=content,
        )

        return {"success": True, "data": _serialize(task), "error": None}

    except Exception as e:
        logger.error("requirement_upload_failed", error=str(e))
        return {"success": False, "data": None, "error": f"上传失败: {str(e)}"}


async def _process_requirement(
    task_id: str,
    file_path: str,
    filename: str,
    file_type: str,
    project_id: str,
    source_url: str | None,
    content: bytes,
):
    """Background task: run ingestion → parsing → analysis for a requirement."""
    pid_short = task_id[:8]
    logger.info("requirement_process_start", task_id=pid_short, file_type=file_type)

    async with async_session_factory() as db:
        try:
            # Mark as processing
            task = await db.get(RequirementTask, task_id)
            if not task:
                logger.error("requirement_process_task_not_found", task_id=pid_short)
                return
            task.status = "processing"
            await db.commit()

            # ── Handle URL type: store content as a temp file ──
            if file_type == "url" and source_url:
                md_path = f"requirements/{project_id or 'global'}/{uuid.uuid4().hex[:8]}_url_doc.md"
                md_content = f"# URL Source\n\n{source_url}\n\n"
                await storage_save(md_path, md_content.encode("utf-8"))
                file_path = md_path
                filename = f"url_{pid_short}.md"
                file_type = "md"

            if not file_path:
                task.status = "failed"
                task.error_message = "文件路径为空"
                await db.commit()
                return

            # ── Create temporary Document record for pipeline stages ──
            temp_doc = Document(
                project_id=project_id,
                filename=filename,
                file_type=file_type,
                file_path=file_path,
                status="uploaded",
            )
            db.add(temp_doc)
            await db.commit()
            await db.refresh(temp_doc)
            doc_id = temp_doc.id

            # ── Build PipelineContext ──
            pipeline_id = f"req-{task_id[:8]}"
            ctx = PipelineContext(
                pipeline_id=pipeline_id,
                project_id=project_id,
                project_config={"platform_type": ""},
                document_ids=[doc_id],
            )

            # ── Stage 1: Ingestion ──
            logger.info("requirement_stage_start", task_id=pid_short, stage="ingestion")
            ingest_stage = IngestionStage(db_session=db)
            ingest_output = await ingest_stage.run(StageInput(
                pipeline_id=pipeline_id,
                project_id=project_id,
                context=ctx,
            ))

            if not ingest_output.is_success:
                task.status = "failed"
                task.error_message = f"文档摄入失败: {ingest_output.error}"
                await db.commit()
                await _cleanup_doc(db, doc_id)
                return

            # Update task with ingestion results
            raw_texts = ctx.raw_texts or {}
            task.raw_text = next(iter(raw_texts.values()), "") if raw_texts else ""
            task.char_count = len(task.raw_text) if task.raw_text else task.char_count
            await db.commit()

            # ── Stage 2: Parsing ──
            logger.info("requirement_stage_start", task_id=pid_short, stage="parsing")
            parse_stage = ParsingStage(db_session=db)
            parse_output = await parse_stage.run(StageInput(
                pipeline_id=pipeline_id,
                project_id=project_id,
                context=ctx,
            ))

            if not parse_output.is_success:
                task.chunk_status = "failed"
                task.status = "failed"
                task.error_message = f"文档解析失败: {parse_output.error}"
                await db.commit()
                await _cleanup_doc(db, doc_id)
                return

            # Update task with parsing results
            parsed_reqs = ctx.parsed_requirements or []
            if parsed_reqs:
                chunk_data = parsed_reqs[0] if isinstance(parsed_reqs[0], dict) else {}
                func_reqs = chunk_data.get("functional_requirements", [])
                task.chunk_count = len(parsed_reqs)
                task.req_count = len(func_reqs)

            # Save structured data as a file
            structured_path = f"requirements/{project_id or 'global'}/{pid_short}_structured.json"
            await storage_save(structured_path, json.dumps(parsed_reqs, ensure_ascii=False, indent=2).encode("utf-8"))
            task.structured_file = structured_path
            task.chunk_status = "completed"
            await db.commit()

            # ── Stage 3: Analysis ──
            logger.info("requirement_stage_start", task_id=pid_short, stage="analysis")
            analysis_stage = AnalysisStage(db_session=db)
            analysis_output = await analysis_stage.run(StageInput(
                pipeline_id=pipeline_id,
                project_id=project_id,
                context=ctx,
            ))

            if not analysis_output.is_success:
                task.status = "failed"
                task.error_message = f"需求分析失败: {analysis_output.error}"
                await db.commit()
                await _cleanup_doc(db, doc_id)
                return

            # Update task with analysis results
            test_plan_file = ctx.test_plan_file
            if test_plan_file:
                task.test_plan_file = test_plan_file

            # Count requirements from analysis
            if ctx.analysis_report:
                task.req_count = ctx.analysis_report.get("requirements_count", task.req_count)

            task.status = "completed"
            await db.commit()

            # ── Cleanup temp Document ──
            await _cleanup_doc(db, doc_id)

            logger.info("requirement_process_done", task_id=pid_short)

        except Exception as e:
            logger.error("requirement_process_error", task_id=pid_short, error=str(e))
            try:
                task = await db.get(RequirementTask, task_id)
                if task:
                    task.status = "failed"
                    task.error_message = f"处理异常: {str(e)[:500]}"
                    await db.commit()
            except Exception:
                pass


async def _cleanup_doc(db: AsyncSession, doc_id: str):
    """Delete temporary Document record."""
    try:
        await db.execute(delete(Document).where(Document.id == doc_id))
        await db.commit()
    except Exception as e:
        logger.warning("requirement_cleanup_doc_failed", doc_id=doc_id[:8], error=str(e))


@router.get("")
async def list_requirements(
    project_id: str = Query(None),
    status: str = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List all requirement tasks."""
    query = select(RequirementTask)
    if project_id:
        query = query.where(RequirementTask.project_id == project_id)
    if status:
        query = query.where(RequirementTask.status == status)
    query = query.order_by(RequirementTask.created_at.desc()).offset((page - 1) * size).limit(size)

    result = await db.execute(query)
    tasks = result.scalars().all()
    return {"success": True, "data": [_serialize(t) for t in tasks], "error": None}


@router.get("/{task_id}")
async def get_requirement(task_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RequirementTask).where(RequirementTask.id == task_id))
    t = result.scalar_one_or_none()
    if not t:
        return {"success": False, "data": None, "error": "Not found"}
    return {"success": True, "data": _serialize(t), "error": None}


@router.get("/{task_id}/download/{file_type}")
async def download_file(task_id: str, file_type: str, db: AsyncSession = Depends(get_db)):
    """Download requirement-related files: original, structured, testplan."""
    result = await db.execute(select(RequirementTask).where(RequirementTask.id == task_id))
    t = result.scalar_one_or_none()
    if not t:
        return {"success": False, "data": None, "error": "Not found"}

    if file_type == "original" and t.file_path:
        path = t.file_path
    elif file_type == "structured" and t.structured_file:
        path = t.structured_file
    elif file_type == "testplan" and t.test_plan_file:
        path = t.test_plan_file
    else:
        return {"success": False, "data": None, "error": f"文件类型 '{file_type}' 不存在或未生成"}

    if not await storage_exists(path):
        return {"success": False, "data": None, "error": "文件不存在"}

    content = await storage_read(path)
    if content is None:
        return {"success": False, "data": None, "error": "文件读取失败"}

    filename = path.split("/")[-1]
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.delete("/{task_id}")
async def delete_requirement(task_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RequirementTask).where(RequirementTask.id == task_id))
    t = result.scalar_one_or_none()
    if not t:
        return {"success": False, "data": None, "error": "Not found"}
    await db.delete(t)
    await db.commit()
    return {"success": True, "data": None, "error": None}


def _serialize(t: RequirementTask) -> dict:
    return {
        "id": t.id,
        "project_id": t.project_id,
        "name": t.name,
        "source_format": t.source_format,
        "source_url": t.source_url,
        "file_path": t.file_path,
        "char_count": t.char_count,
        "chunk_count": t.chunk_count,
        "chunk_status": t.chunk_status,
        "req_count": t.req_count,
        "structured_file": t.structured_file,
        "test_plan_file": t.test_plan_file,
        "status": t.status,
        "error_message": t.error_message,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
