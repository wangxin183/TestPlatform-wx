"""Case Library API — directories + test cases (CRUD + import + auto-generate)."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import async_session_factory, get_db
from src.core.models.models import Project, RequirementTask, TestCase, TestCaseDirectory
from src.pipeline.context import PipelineContext
from src.pipeline.stages.base import StageInput
from src.pipeline.stages.generation import GenerationStage
from src.utils.case_parser import parse_cases
from src.utils.file_storage import exists as storage_exists, read as storage_read, save as storage_save
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/case-library", tags=["case_library"])

# ═══════════════════════════════════════════════════════
# Directories
# ═══════════════════════════════════════════════════════

@router.post("/directories")
async def create_directory(
    name: str = Form(...),
    parent_id: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    dir = TestCaseDirectory(name=name, parent_id=parent_id or None)
    db.add(dir)
    await db.commit()
    # Refresh with eager-loaded test_cases to avoid MissingGreenlet
    result = await db.execute(
        select(TestCaseDirectory).options(selectinload(TestCaseDirectory.test_cases)).where(TestCaseDirectory.id == dir.id)
    )
    dir = result.scalar_one()
    return {"success": True, "data": _serialize_dir(dir), "error": None}


@router.get("/directories")
async def list_directories(db: AsyncSession = Depends(get_db)):
    query = select(TestCaseDirectory).options(selectinload(TestCaseDirectory.test_cases)).order_by(TestCaseDirectory.sort_order, TestCaseDirectory.name)
    result = await db.execute(query)
    dirs = result.scalars().all()
    return {"success": True, "data": [_serialize_dir(d) for d in dirs], "error": None}


@router.put("/directories/{dir_id}")
async def update_directory(
    dir_id: str,
    name: str = Form(None),
    parent_id: str = Form(None),
    sort_order: int = Form(None),
    db: AsyncSession = Depends(get_db),
):
    d = await db.get(TestCaseDirectory, dir_id)
    if not d:
        return {"success": False, "data": None, "error": "目录不存在"}
    if name is not None:
        d.name = name
    if parent_id is not None:
        d.parent_id = parent_id or None
    if sort_order is not None:
        d.sort_order = sort_order
    await db.commit()
    result = await db.execute(
        select(TestCaseDirectory).options(selectinload(TestCaseDirectory.test_cases)).where(TestCaseDirectory.id == d.id)
    )
    d = result.scalar_one()
    return {"success": True, "data": _serialize_dir(d), "error": None}


@router.delete("/directories/{dir_id}")
async def delete_directory(dir_id: str, db: AsyncSession = Depends(get_db)):
    d = await db.get(TestCaseDirectory, dir_id)
    if not d:
        return {"success": False, "data": None, "error": "目录不存在"}
    # Move cases to root (directory_id = None)
    result = await db.execute(
        select(TestCase).where(TestCase.directory_id == dir_id)
    )
    cases = result.scalars().all()
    for c in cases:
        c.directory_id = None
    await db.delete(d)
    await db.commit()
    return {"success": True, "data": None, "error": None}


# ═══════════════════════════════════════════════════════
# Test Cases
# ═══════════════════════════════════════════════════════

@router.get("/cases")
async def list_cases(
    directory_id: str = Query(None),
    project_id: str = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    query = select(TestCase).where(TestCase.pipeline_id.is_(None))
    if directory_id:
        query = query.where(TestCase.directory_id == directory_id)
    elif directory_id == "":
        query = query.where(TestCase.directory_id.is_(None))
    if project_id:
        query = query.where(TestCase.project_id == project_id)
    query = query.order_by(TestCase.created_at.desc()).offset((page - 1) * size).limit(size)
    result = await db.execute(query)
    cases = result.scalars().all()
    return {"success": True, "data": [_serialize_case(c) for c in cases], "error": None}


@router.post("/cases/import")
async def import_cases(
    file: UploadFile = File(...),
    directory_id: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    if not file:
        return {"success": False, "data": None, "error": "请选择文件"}
    try:
        content = await file.read()
        ext = Path(file.filename).suffix.lower().lstrip(".")
        fmt_map = {"xlsx": "excel", "xls": "excel", "xmind": "xmind", "json": "json", "md": "markdown"}
        fmt = fmt_map.get(ext, "markdown")

        if fmt in ("excel", "xmind"):
            text = content
        else:
            text = content.decode("utf-8", errors="replace")

        cases, error, _ = await parse_cases(fmt, text)
        if error:
            return {"success": False, "data": None, "error": f"解析失败: {error}"}
        if not cases:
            return {"success": False, "data": None, "error": "文件中未识别到有效用例"}

        saved = []
        for c in cases:
            tc = TestCase(
                title=c.get("title", "未命名用例"),
                description=c.get("description", ""),
                preconditions=c.get("preconditions", ""),
                steps=c.get("steps", []),
                priority=c.get("priority", "中"),
                test_type=c.get("test_type", "ui"),
                tags=c.get("tags", []),
                platform_type=c.get("platform_type", ""),
                directory_id=directory_id or None,
                source="import",
            )
            db.add(tc)
            saved.append(tc)
        await db.commit()
        for tc in saved:
            await db.refresh(tc)
        return {"success": True, "data": [_serialize_case(c) for c in saved], "error": None}
    except Exception as e:
        logger.error("case_import_failed", error=str(e))
        return {"success": False, "data": None, "error": f"导入失败: {str(e)}"}


@router.post("/cases")
async def create_case(
    title: str = Form(...),
    directory_id: str = Form(None),
    description: str = Form(""),
    preconditions: str = Form(""),
    steps: str = Form("[]"),
    test_type: str = Form("ui"),
    priority: str = Form("中"),
    platform_type: str = Form(""),
    tags: str = Form("[]"),
    db: AsyncSession = Depends(get_db),
):
    try:
        steps_list = json.loads(steps) if isinstance(steps, str) else steps
        tags_list = json.loads(tags) if isinstance(tags, str) else tags
    except json.JSONDecodeError:
        return {"success": False, "data": None, "error": "steps 或 tags 格式错误"}

    tc = TestCase(
        title=title,
        description=description,
        preconditions=preconditions,
        steps=steps_list,
        priority=priority,
        test_type=test_type,
        platform_type=platform_type,
        tags=tags_list,
        directory_id=directory_id or None,
        source="manual",
    )
    db.add(tc)
    await db.commit()
    await db.refresh(tc)
    return {"success": True, "data": _serialize_case(tc), "error": None}


@router.put("/cases/{case_id}")
async def update_case(
    case_id: str,
    title: str = Form(None),
    description: str = Form(None),
    preconditions: str = Form(None),
    steps: str = Form(None),
    test_type: str = Form(None),
    priority: str = Form(None),
    directory_id: str = Form(None),
    tags: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    tc = await db.get(TestCase, case_id)
    if not tc:
        return {"success": False, "data": None, "error": "用例不存在"}
    if title is not None: tc.title = title
    if description is not None: tc.description = description
    if preconditions is not None: tc.preconditions = preconditions
    if test_type is not None: tc.test_type = test_type
    if priority is not None: tc.priority = priority
    if directory_id is not None: tc.directory_id = directory_id or None
    if steps is not None:
        try:
            tc.steps = json.loads(steps) if isinstance(steps, str) else steps
        except json.JSONDecodeError:
            pass
    if tags is not None:
        try:
            tc.tags = json.loads(tags) if isinstance(tags, str) else tags
        except json.JSONDecodeError:
            pass
    await db.commit()
    await db.refresh(tc)
    return {"success": True, "data": _serialize_case(tc), "error": None}


@router.delete("/cases/{case_id}")
async def delete_case(case_id: str, db: AsyncSession = Depends(get_db)):
    tc = await db.get(TestCase, case_id)
    if not tc:
        return {"success": False, "data": None, "error": "用例不存在"}
    await db.delete(tc)
    await db.commit()
    return {"success": True, "data": None, "error": None}


# ═══════════════════════════════════════════════════════
# Auto-Generate
# ═══════════════════════════════════════════════════════

@router.post("/cases/generate")
async def generate_cases(
    background_tasks: BackgroundTasks,
    project_id: str = Form(None),
    test_plan_id: str = Form(...),
    directory_id: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Auto-generate test cases from a test plan (reuses GenerationStage)."""
    # Validate project
    proj = await db.get(Project, project_id)
    if not proj:
        return {"success": False, "data": None, "error": "项目不存在"}

    # Get the test plan data
    req_task = await db.get(RequirementTask, test_plan_id)
    if not req_task or not req_task.structured_file:
        return {"success": False, "data": None, "error": "测试计划不存在或不完整"}

    # Load parsed requirements from the structured file
    if req_task.structured_file and await storage_exists(req_task.structured_file):
        content = await storage_read(req_task.structured_file)
        if content:
            parsed_requirements = json.loads(content.decode("utf-8"))
        else:
            parsed_requirements = []
    else:
        parsed_requirements = []

    if not parsed_requirements:
        return {"success": False, "data": None, "error": "无法加载测试计划的需求数据"}

    # Load analysis report from test plan file
    test_plan_md = ""
    if req_task.test_plan_file and await storage_exists(req_task.test_plan_file):
        tp_content = await storage_read(req_task.test_plan_file)
        if tp_content:
            test_plan_md = tp_content.decode("utf-8", errors="replace")

    # Schedule background generation
    background_tasks.add_task(
        _run_generation,
        project_id=project_id,
        platform_type=proj.platform_type or "web",
        parsed_requirements=parsed_requirements,
        test_plan_md=test_plan_md,
        directory_id=directory_id,
        test_plan_id=test_plan_id,
    )
    return {"success": True, "data": {"message": "用例生成已启动，请稍后刷新列表"}, "error": None}


async def _run_generation(
    project_id: str,
    platform_type: str,
    parsed_requirements: list,
    test_plan_md: str,
    directory_id: str | None,
    test_plan_id: str,
):
    """Background: run GenerationStage and save cases to directory."""
    pipeline_id = f"gen-{uuid.uuid4().hex[:8]}"
    logger.info("case_generation_start", pipeline_id=pipeline_id, project_id=project_id[:8])

    async with async_session_factory() as db:
        try:
            ctx = PipelineContext(
                pipeline_id=pipeline_id,
                project_id=project_id,
                project_config={"platform_type": platform_type},
                parsed_requirements=parsed_requirements,
                analysis_report={"test_plan_md": test_plan_md},
            )

            stage = GenerationStage(db_session=db)
            output = await stage.run(StageInput(
                pipeline_id=pipeline_id,
                project_id=project_id,
                context=ctx,
            ))

            if not output.is_success:
                logger.error("case_generation_failed", pipeline_id=pipeline_id, error=output.error)
                return

            generated = ctx.generated_test_cases or []
            for c_data in generated:
                tc = TestCase(
                    project_id=project_id,
                    title=c_data.get("title", "未命名"),
                    description=c_data.get("description", ""),
                    preconditions=c_data.get("preconditions", ""),
                    steps=c_data.get("steps", []),
                    priority=c_data.get("priority", "中"),
                    test_type=c_data.get("test_type", "ui"),
                    tags=c_data.get("tags", []),
                    platform_type=platform_type,
                    directory_id=directory_id or None,
                    source="auto",
                    test_plan_id=test_plan_id,
                )
                db.add(tc)
            await db.commit()
            logger.info("case_generation_done", pipeline_id=pipeline_id, count=len(generated))
        except Exception as e:
            logger.error("case_generation_error", pipeline_id=pipeline_id, error=str(e))


# ═══════════════════════════════════════════════════════
# Serializers
# ═══════════════════════════════════════════════════════

def _serialize_dir(d: TestCaseDirectory) -> dict:
    case_count = len(d.test_cases) if d.test_cases else 0
    return {
        "id": d.id,
        "parent_id": d.parent_id, "name": d.name,
        "sort_order": d.sort_order, "case_count": case_count,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def _serialize_case(c: TestCase) -> dict:
    return {
        "id": c.id, "project_id": c.project_id,
        "directory_id": c.directory_id, "title": c.title,
        "description": c.description, "preconditions": c.preconditions,
        "steps": c.steps, "priority": c.priority, "test_type": c.test_type,
        "tags": c.tags, "platform_type": c.platform_type,
        "source": c.source or "manual", "test_plan_id": c.test_plan_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
