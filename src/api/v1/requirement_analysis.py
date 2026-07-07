"""需求分析 API — 与项目解耦的独立需求分析端点。

端点：
  POST   /requirement-analyses              创建分析任务（上传文档 + 后台分析）
  GET    /requirement-analyses              分析记录列表
  GET    /requirement-analyses/{id}          获取分析详情
  GET    /requirement-analyses/{id}/status   轮询分析状态
  POST   /requirement-analyses/{id}/review   提交人工审查
  POST   /requirement-analyses/{id}/retry    驳回后重试
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Query, UploadFile

from src.services.requirement_analysis_service import requirement_analysis_svc
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/requirement-analyses", tags=["requirement-analysis"])


# ============================================================
# 序列化辅助
# ============================================================

def _serialize_task(task) -> dict:
    """将 AnalysisTask 转为 API 响应字典。"""
    return {
        "analysis_id": task.analysis_id,
        "filename": task.filename,
        "file_type": task.file_type,
        "platform_type": task.platform_type,
        "custom_prompt": task.custom_prompt,
        "status": task.status,
        "current_step": task.current_step,
        "progress_pct": task.progress_pct,
        "analysis_json": task.analysis_json,
        "review_json": task.review_json,
        "human_review": task.human_review,
        "created_at": task.created_at,
        "completed_at": task.completed_at,
        "error_message": task.error_message,
    }


# ============================================================
# 端点
# ============================================================

@router.post("")
async def create_analysis(
    file: UploadFile,
    platform_type: str = Form(""),
    custom_prompt: str = Form(""),
    obsidian_modules: str = Form(""),
):
    """创建需求分析任务。

    接收上传的需求文档（docx/pdf/xlsx/json/yaml/md/txt），
    启动后台分析流程（文档摄取 → Claude Code 分析 → Codex 审查 → 飞书通知）。

    Args:
        file: 需求文档文件
        platform_type: 目标平台类型（web/ios/android/api，可选）
        custom_prompt: 用户自定义分析要求（可选）
        obsidian_modules: 逗号分隔的 Obsidian 模块名（可选，用于按需加载知识库）

    Returns:
        201: {"success": true, "data": {"analysis_id": "RA-0001", ...}}
    """
    content = await file.read()

    if not content:
        return {
            "success": False,
            "data": None,
            "error": "上传文件内容为空",
        }

    # 文件大小限制（10MB）
    max_size = 10 * 1024 * 1024
    if len(content) > max_size:
        return {
            "success": False,
            "data": None,
            "error": f"文件过大（{len(content) / 1024 / 1024:.1f}MB），最大支持 10MB",
        }

    try:
        analysis_id = await requirement_analysis_svc.start_analysis(
            file_content=content,
            filename=file.filename or "未命名文档",
            platform_type=platform_type,
            custom_prompt=custom_prompt,
            obsidian_modules=obsidian_modules,
        )

        task = await requirement_analysis_svc.get_task(analysis_id)
        if not task:
            return {
                "success": False,
                "data": None,
                "error": "创建分析任务后无法获取任务信息",
            }

        return {
            "success": True,
            "data": _serialize_task(task),
            "error": None,
        }

    except Exception as exc:
        logger.error("create_analysis_error", error=str(exc))
        return {
            "success": False,
            "data": None,
            "error": f"创建分析任务失败: {str(exc)}",
        }


@router.get("")
async def list_analyses(
    status: str = Query("", description="按状态过滤"),
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(20, ge=1, le=100, description="每页数量"),
):
    """列出需求分析记录。

    Args:
        status: 可选状态过滤（uploading/processing/reviewing/pending_review/approved/rejected/failed）
        page: 页码（从 1 开始）
        size: 每页数量（1-100）

    Returns:
        200: {"success": true, "data": [...], "meta": {"page": 1, "size": 20, "total": 42}}
    """
    tasks, total = await requirement_analysis_svc.list_tasks(
        status=status,
        page=page,
        size=size,
    )

    return {
        "success": True,
        "data": [_serialize_task(t) for t in tasks],
        "meta": {
            "page": page,
            "size": size,
            "total": total,
        },
        "error": None,
    }


@router.get("/{analysis_id}")
async def get_analysis(analysis_id: str):
    """获取分析详情（含分析 JSON、审查报告、日志、Skill 快照）。

    Args:
        analysis_id: 分析任务 ID（如 RA-0001）

    Returns:
        200: {"success": true, "data": {...}}
    """
    task = await requirement_analysis_svc.get_task(analysis_id)
    if not task:
        return {
            "success": False,
            "data": None,
            "error": f"分析任务未找到: {analysis_id}",
        }

    # 加载详细日志
    from src.utils.analysis_logger import AnalysisLogger
    alog = AnalysisLogger(analysis_id)
    logs = alog.read_logs()

    data = _serialize_task(task)
    data["logs"] = logs

    # 加载 Skill 快照
    skill_path = alog.dir_path / "SKILL_used.md"
    if skill_path.exists():
        data["skill_snapshot"] = skill_path.read_text(encoding="utf-8")

    return {
        "success": True,
        "data": data,
        "error": None,
    }


@router.get("/{analysis_id}/status")
async def get_analysis_status(analysis_id: str):
    """轮询分析状态（轻量级端点，用于前端实时进度更新）。

    Args:
        analysis_id: 分析任务 ID

    Returns:
        200: {"success": true, "data": {"status": "processing", "current_step": "...", "progress_pct": 40}}
    """
    task = await requirement_analysis_svc.get_task(analysis_id)
    if not task:
        return {
            "success": False,
            "data": None,
            "error": f"分析任务未找到: {analysis_id}",
        }

    return {
        "success": True,
        "data": {
            "status": task.status,
            "current_step": task.current_step,
            "progress_pct": task.progress_pct,
        },
        "error": None,
    }


@router.post("/{analysis_id}/review")
async def submit_review(
    analysis_id: str,
    body: dict,
):
    """提交人工审查结果。

    请求体：
    {
        "decision": "approved" | "rejected",
        "comment": "审查意见内容",
        "corrections": [{"field": "functional_requirements.FR-003.description", "value": "..."}]
    }

    Args:
        analysis_id: 分析任务 ID
        body: 审查决定和意见

    Returns:
        200: {"success": true, "data": {"status": "approved"}}
    """
    decision = body.get("decision", "")
    comment = body.get("comment", "")
    corrections = body.get("corrections", [])

    if decision not in ("approved", "rejected"):
        return {
            "success": False,
            "data": None,
            "error": "decision 必须为 approved 或 rejected",
        }

    result = await requirement_analysis_svc.submit_human_review(
        analysis_id=analysis_id,
        decision=decision,
        comment=comment,
        corrections=corrections,
    )

    if result.get("success"):
        return {
            "success": True,
            "data": result,
            "error": None,
        }
    else:
        return {
            "success": False,
            "data": None,
            "error": result.get("error", "审核提交失败"),
        }


@router.post("/{analysis_id}/retry")
async def retry_analysis(
    analysis_id: str,
    body: dict,
):
    """驳回或失败后重新分析。

    请求体：
    {
        "feedback": "补充的人工审查意见"
    }

    Args:
        analysis_id: 分析任务 ID
        body: 可选的补充意见

    Returns:
        200: {"success": true, "data": {"status": "processing"}}
    """
    feedback = body.get("feedback", "")

    result = await requirement_analysis_svc.retry_analysis(
        analysis_id=analysis_id,
        feedback=feedback,
    )

    if result.get("success"):
        return {
            "success": True,
            "data": result,
            "error": None,
        }
    else:
        return {
            "success": False,
            "data": None,
            "error": result.get("error", "重试失败"),
        }
