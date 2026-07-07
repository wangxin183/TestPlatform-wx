"""需求分析核心服务 — 编排文档摄取 → Claude Code 分析 → Codex 审查 → 飞书通知。

与项目/Pipeline 完全解耦，每个分析任务由唯一 analysis_id 标识。

Usage:
    from src.services.requirement_analysis_service import RequirementAnalysisService

    svc = RequirementAnalysisService()
    analysis_id = await svc.start_analysis(
        file_content=b"...",
        filename="需求文档.docx",
        platform_type="ios",
        custom_prompt="",
    )
    # 后台异步执行，前端通过 /status 轮询进度
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.llm.prompts.skill_loader import load_skill
from src.services.agent_cli import AgentCLI, CLICallResult
from src.services.feishu_notifier import FeishuNotifier
from src.services.knowbase_loader import KnowledgeBaseLoader
from src.services.self_healing import (
    FailureCategory,
    FailureInfo,
    HealingContext,
    SelfHealingOrchestrator,
    classify_failure,
)
from src.utils.analysis_logger import AnalysisLogger
from src.utils.document_converter import (
    convert_to_markdown,
    detect_file_type,
    has_binary_signature,
)
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# ============================================================
# 配置常量
# ============================================================

STORAGE_BASE = Path("storage/requirement_analyses")

# 分析状态流转：
#   uploading → processing → reviewing → pending_review → approved / rejected
#   任何阶段出错 → failed
STATUS_TRANSITIONS = {
    "uploading": ["processing", "failed"],
    "processing": ["reviewing", "failed"],
    "reviewing": ["pending_review", "failed"],
    "pending_review": ["approved", "rejected"],
    "rejected": ["processing", "failed"],  # 驳回后重新分析
}

CLAUDE_ANALYSIS_TIMEOUT = 600  # Claude Code 分析超时（秒）
CODEX_REVIEW_TIMEOUT = 300  # Codex 审查超时（秒）
MAX_RECOVERY_ATTEMPTS = 3    # 每个任务最多自动恢复次数（防无限重试循环）

# 上下文窗口管理
MODEL_CONTEXT_WINDOW = 180000   # 上下文窗口上限（90% 利用率，200K 窗口取 180K）
OUTPUT_TOKEN_BUDGET = 20000     # 预留给模型输出
SAFETY_MARGIN = 5000            # 安全余量
MAX_INPUT_TOKENS = MODEL_CONTEXT_WINDOW - OUTPUT_TOKEN_BUDGET - SAFETY_MARGIN  # 155000


# ============================================================
# 数据模型
# ============================================================

@dataclass
class AnalysisTask:
    """需求分析任务的数据模型。"""
    analysis_id: str
    filename: str = ""
    file_type: str = ""
    platform_type: str = ""
    custom_prompt: str = ""
    status: str = "uploading"
    current_step: str = "等待开始"
    progress_pct: int = 0

    # 分析输出
    doc_markdown: str = ""
    analysis_json: dict | None = None
    review_json: dict | None = None
    human_review: dict | None = None

    # 时间戳
    created_at: str = ""
    completed_at: str = ""

    # 元数据
    skill_snapshot: str = ""
    logs: list[dict] = field(default_factory=list)
    error_message: str = ""
    recovery_count: int = 0  # 已尝试的自动恢复次数（防无限循环）


# 全局任务表（内存缓存 + 文件持久化）
_task_store: dict[str, AnalysisTask] = {}


def _scan_storage_for_tasks() -> dict[str, AnalysisTask]:
    """启动时扫描 storage/requirement_analyses/ 恢复任务状态。

    每个任务目录下的 task_state.json 记录核心状态。
    如果文件不存在但 analysis.log 存在，则从日志重建。
    """
    restored: dict[str, AnalysisTask] = {}
    if not STORAGE_BASE.exists():
        return restored

    for task_dir in STORAGE_BASE.iterdir():
        if not task_dir.is_dir():
            continue
        analysis_id = task_dir.name

        # 优先读取状态文件
        state_file = task_dir / "task_state.json"
        task = None

        if state_file.exists():
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                task = AnalysisTask(
                    analysis_id=data.get("analysis_id", analysis_id),
                    filename=data.get("filename", ""),
                    file_type=data.get("file_type", ""),
                    platform_type=data.get("platform_type", ""),
                    custom_prompt=data.get("custom_prompt", ""),
                    status=data.get("status", "failed"),
                    current_step=data.get("current_step", ""),
                    progress_pct=data.get("progress_pct", 0),
                    doc_markdown=data.get("doc_markdown", ""),
                    created_at=data.get("created_at", ""),
                    completed_at=data.get("completed_at", ""),
                    error_message=data.get("error_message", ""),
                    recovery_count=data.get("recovery_count", 0),
                )
            except Exception as exc:
                logger.warning("task_state_restore_failed", analysis_id=analysis_id, error=str(exc))

        # 从日志文件重建分析/审查数据
        log_file = task_dir / "analysis.log"
        if log_file.exists() and task is not None:
            _restore_task_from_logs(task, task_dir)

        # 状态修复：如果 review 文件已存在但状态未更新（并发写入竞争导致），
        # 自动修正为 pending_review，防止错误触发中断恢复
        if task is not None and task.status in ("uploading", "processing", "reviewing"):
            review_file = task_dir / f"review_{analysis_id}.json"
            if review_file.exists():
                logger.info(
                    "task_status_auto_corrected",
                    analysis_id=analysis_id,
                    old_status=task.status,
                    new_status="pending_review",
                    note="审查文件已存在但状态文件未更新，自动修正",
                )
                task.status = "pending_review"
                task.current_step = "审查完成，等待人工确认"
                task.progress_pct = 90
                _save_task_state(task)

        if task is not None:
            restored[analysis_id] = task

    return restored


def _restore_task_from_logs(task: AnalysisTask, task_dir: Path) -> None:
    """从 analysis.log 和 JSON 文件恢复任务的详细数据。"""
    # 恢复分析 JSON
    analysis_file = task_dir / f"{task.analysis_id}.json"
    if analysis_file.exists():
        try:
            task.analysis_json = json.loads(analysis_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 恢复审查 JSON
    review_file = task_dir / f"review_{task.analysis_id}.json"
    if review_file.exists():
        try:
            task.review_json = json.loads(review_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 恢复 Skill 快照
    skill_file = task_dir / "SKILL_used.md"
    if skill_file.exists():
        task.skill_snapshot = skill_file.read_text(encoding="utf-8")


def _next_analysis_id() -> str:
    """生成下一个分析 ID（RA-XXXX 格式）。扫描内存和文件系统。"""
    max_num = 0

    # 内存中的任务
    for k in _task_store:
        if k.startswith("RA-"):
            try:
                max_num = max(max_num, int(k.split("-")[1]))
            except ValueError:
                pass

    # 文件系统中的任务目录
    if STORAGE_BASE.exists():
        for d in STORAGE_BASE.iterdir():
            if d.is_dir() and d.name.startswith("RA-"):
                try:
                    max_num = max(max_num, int(d.name.split("-")[1]))
                except ValueError:
                    pass

    return f"RA-{max_num + 1:04d}"


def _acquire_run_lock(analysis_id: str) -> bool:
    """获取文件锁 — 跨进程/重启有效，防止同一任务重复执行。

    使用 O_CREAT|O_EXCL 实现原子创建，消除 TOCTOU 竞态条件。
    锁文件内容为当前 PID，_release_run_lock 验证 PID 防止误删。

    Returns:
        True: 成功获取锁，可以安全执行
        False: 已有其他进程在执行此任务，应跳过
    """
    lock_file = STORAGE_BASE / analysis_id / ".running.lock"
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)

        # 使用 os.open + O_CREAT|O_EXCL 实现原子"检查-创建"，
        # 消除 exists() + write_text() 之间的 TOCTOU 竞态窗口
        fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode("utf-8"))
        finally:
            os.close(fd)
        return True

    except FileExistsError:
        # 锁文件已存在 → 检查是否僵尸锁（超过 2 小时未更新）
        try:
            age = time.time() - lock_file.stat().st_mtime
        except OSError:
            # 文件在检查和 stat 之间被删除了，重试一次
            return _acquire_run_lock(analysis_id)

        if age > 7200:
            logger.warning(
                "stale_lock_removed",
                analysis_id=analysis_id,
                lock_age_hours=round(age / 3600, 1),
            )
            try:
                lock_file.unlink()
            except OSError:
                pass
            # 递归重试（此时锁文件已被删除，应能成功创建）
            return _acquire_run_lock(analysis_id)

        # 锁被其他进程持有且未过期
        return False

    except OSError:
        # 磁盘满、权限错误等 — 不 fail-open，保守拒绝
        logger.error(
            "lock_acquire_os_error",
            analysis_id=analysis_id,
            exc_info=True,
        )
        return False


def _release_run_lock(analysis_id: str) -> None:
    """释放文件锁。仅当锁文件中的 PID 与当前进程 PID 匹配时才删除，
    防止一个进程误删另一个进程的锁。"""
    lock_file = STORAGE_BASE / analysis_id / ".running.lock"
    try:
        if not lock_file.exists():
            return
        # PID 验证：只删除自己持有的锁
        stored_pid = lock_file.read_text(encoding="utf-8").strip()
        if stored_pid != str(os.getpid()):
            logger.warning(
                "lock_release_pid_mismatch",
                analysis_id=analysis_id,
                stored_pid=stored_pid,
                current_pid=str(os.getpid()),
                note="锁文件不属于当前进程，跳过删除",
            )
            return
        lock_file.unlink()
    except Exception as exc:
        logger.warning("lock_release_failed", analysis_id=analysis_id, error=str(exc))


def _save_task_state(task: AnalysisTask) -> None:
    """将任务核心状态持久化到 task_state.json。

    不含 analysis_json 和 review_json（它们有独立文件）。
    """
    try:
        state_file = STORAGE_BASE / task.analysis_id / "task_state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "analysis_id": task.analysis_id,
            "filename": task.filename,
            "file_type": task.file_type,
            "platform_type": task.platform_type,
            "custom_prompt": task.custom_prompt,
            "status": task.status,
            "current_step": task.current_step,
            "progress_pct": task.progress_pct,
            "doc_markdown": task.doc_markdown,
            "created_at": task.created_at,
            "completed_at": task.completed_at,
            "error_message": task.error_message,
            "recovery_count": task.recovery_count,
        }
        state_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("task_state_save_failed", analysis_id=task.analysis_id, error=str(exc))


# ============================================================
# RequirementAnalysisService
# ============================================================

class RequirementAnalysisService:
    """需求分析核心编排服务。

    协调文档摄取、Agent 调用、审查、通知的完整流程。
    每个分析任务独立存储，不依赖 Project/Pipeline。
    任务状态持久化到文件系统，支持服务器重启恢复。
    """

    def __init__(self):
        self.cli = AgentCLI()
        self.feishu = FeishuNotifier()
        self.knowbase = KnowledgeBaseLoader()
        self.healer = SelfHealingOrchestrator(self.cli, self.feishu)
        self._store_lock = asyncio.Lock()  # 保护 _task_store 的并发访问

        # 从文件系统恢复任务状态
        restored = _scan_storage_for_tasks()
        if restored:
            _task_store.update(restored)
            logger.info("tasks_restored_from_disk", count=len(restored))

        # 检测中断任务并触发恢复（文件锁防重入，跨 process reload 有效）
        interrupted = [
            t for t in restored.values()
            if t.status in ("uploading", "processing", "reviewing")
        ]
        if interrupted:
            for task in interrupted:
                # 恢复次数上限：超过 MAX_RECOVERY_ATTEMPTS 次自动恢复后，
                # 标记为 failed 并跳过，避免无限重试循环消耗资源
                if task.recovery_count >= MAX_RECOVERY_ATTEMPTS:
                    logger.warning(
                        "task_recovery_limit_exceeded",
                        analysis_id=task.analysis_id,
                        recovery_count=task.recovery_count,
                        max_recovery_attempts=MAX_RECOVERY_ATTEMPTS,
                    )
                    task.status = "failed"
                    task.current_step = "自动恢复次数超限"
                    task.error_message = (
                        f"已尝试 {task.recovery_count} 次自动恢复，"
                        f"超过上限 {MAX_RECOVERY_ATTEMPTS} 次，标记为失败"
                    )
                    _save_task_state(task)
                    continue

                # 文件锁：如果已有进程在执行此任务则跳过
                if not _acquire_run_lock(task.analysis_id):
                    logger.info(
                        "task_recovery_skipped_already_running",
                        analysis_id=task.analysis_id,
                    )
                    continue

                # 递增恢复次数并持久化
                task.recovery_count += 1
                _save_task_state(task)

                alog = AnalysisLogger(task.analysis_id)
                old_status = task.status
                alog.log(
                    "task_interrupted",
                    previous_status=old_status,
                    previous_step=task.current_step,
                    recovery_attempt=task.recovery_count,
                    max_recovery=MAX_RECOVERY_ATTEMPTS,
                    note="服务重启检测到中断任务，将在下一轮恢复执行",
                )
                logger.info(
                    "task_interrupted_recovery",
                    analysis_id=task.analysis_id,
                    previous_status=old_status,
                    recovery_attempt=task.recovery_count,
                )

                md_path = STORAGE_BASE / task.analysis_id / f"{task.filename}.md"
                if md_path.exists():
                    async def _recover_with_cleanup(aid, mp, pt, cp):
                        try:
                            await self._run_analysis_with_content(aid, mp, pt, cp)
                        except Exception as exc:
                            logger.error("recovery_task_error", analysis_id=aid, error=str(exc))
                            # 注意：不在此处释放文件锁。锁只在任务正常完成或手动重试时释放。
                            # 如果进程崩溃/重启，锁文件保留，阻止下次 init 重复触发恢复。
                            # 僵尸锁由 _acquire_run_lock 的 2 小时过期机制自动清理。

                    asyncio.create_task(
                        _recover_with_cleanup(
                            task.analysis_id, md_path,
                            task.platform_type, task.custom_prompt,
                        )
                    )
                else:
                    alog.log(
                        "task_interrupted_no_md",
                        note="原始 Markdown 文件丢失，无法恢复",
                    )

    # ============================================================
    # 公共接口 — 创建和查询
    # ============================================================

    async def start_analysis(
        self,
        file_content: bytes,
        filename: str,
        platform_type: str = "",
        custom_prompt: str = "",
        obsidian_modules: str = "",
    ) -> str:
        """启动需求分析任务（异步后台执行）。

        Args:
            file_content: 上传文件的原始字节内容
            filename: 原始文件名
            platform_type: 目标平台类型（web/ios/android/api）
            custom_prompt: 用户自定义的分析要求
            obsidian_modules: 逗号分隔的 Obsidian 模块名（可选）

        Returns:
            analysis_id（用于后续查询状态和结果）
        """
        async with self._store_lock:
            analysis_id = _next_analysis_id()
            file_type = detect_file_type(filename)

            task = AnalysisTask(
                analysis_id=analysis_id,
                filename=filename,
                file_type=file_type,
                platform_type=platform_type,
                custom_prompt=custom_prompt,
                status="uploading",
                current_step="正在摄取文档...",
                progress_pct=5,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            _task_store[analysis_id] = task
            _save_task_state(task)

        alog = AnalysisLogger(analysis_id)
        alog.log(
            "task_created",
            filename=filename,
            file_type=file_type,
            file_size_bytes=len(file_content),
            platform_type=platform_type,
        )

        # 后台执行（不阻塞 API 响应）
        _acquire_run_lock(analysis_id)
        asyncio.create_task(
            self._run_analysis_pipeline(
                analysis_id,
                file_content,
                filename,
                file_type,
                platform_type,
                custom_prompt,
                obsidian_modules,
            )
        )

        logger.info(
            "analysis_task_started",
            analysis_id=analysis_id,
            filename=filename,
        )
        return analysis_id

    async def get_task(self, analysis_id: str) -> AnalysisTask | None:
        """查询分析任务。"""
        async with self._store_lock:
            return _task_store.get(analysis_id)

    async def list_tasks(
        self,
        status: str = "",
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[AnalysisTask], int]:
        """列出分析任务（支持按状态过滤和分页）。

        Returns:
            (tasks, total_count)
        """
        async with self._store_lock:
            all_tasks = list(_task_store.values())
        # 按创建时间倒序
        all_tasks.sort(key=lambda t: t.created_at, reverse=True)

        if status:
            all_tasks = [t for t in all_tasks if t.status == status]

        total = len(all_tasks)
        start = (page - 1) * size
        end = start + size
        return all_tasks[start:end], total

    async def submit_human_review(
        self,
        analysis_id: str,
        decision: str,
        comment: str = "",
        corrections: list[dict] | None = None,
    ) -> dict:
        """提交人工审查结果。

        Args:
            analysis_id: 分析任务 ID
            decision: approved 或 rejected
            comment: 审查意见
            corrections: 修正内容列表 [{"field": "...", "value": "..."}]

        Returns:
            操作结果
        """
        async with self._store_lock:
            task = _task_store.get(analysis_id)
            if not task:
                return {"success": False, "error": f"分析任务未找到: {analysis_id}"}

            if task.status != "pending_review":
                return {
                    "success": False,
                    "error": f"当前状态 {task.status} 不允许提交审核",
                }

            task.human_review = {
                "reviewer": "人工审查",
                "comment": comment,
                "decision": decision,
                "applied_changes": corrections or [],
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
            }
            task.status = decision  # approved 或 rejected
            task.completed_at = datetime.now(timezone.utc).isoformat()

            # 驳回时：将审核意见注入 custom_prompt，重试时 Claude Code 自动读取
            if decision == "rejected" and comment:
                task.custom_prompt = (
                    f"{task.custom_prompt}\n\n"
                    f"## 人工审查意见（驳回重试）\n"
                    f"{comment}"
                )
            _save_task_state(task)

        alog = AnalysisLogger(analysis_id)
        alog.log(
            "human_review_submitted",
            decision=decision,
            comment=comment[:200],
            corrections_count=len(corrections or []),
        )

        # 飞书通知审核结果
        await self.feishu.notify_review_result(
            analysis_id=analysis_id,
            decision=decision,
            comment=comment,
        )

        return {"success": True, "status": decision}

    async def retry_analysis(
        self,
        analysis_id: str,
        feedback: str = "",
    ) -> dict:
        """驳回后重新分析。

        Args:
            analysis_id: 分析任务 ID
            feedback: 补充的人工审查意见

        Returns:
            操作结果
        """
        async with self._store_lock:
            task = _task_store.get(analysis_id)
            if not task:
                return {"success": False, "error": f"分析任务未找到: {analysis_id}"}

            if task.status not in ("rejected", "failed"):
                return {
                    "success": False,
                    "error": f"当前状态 {task.status} 不允许重试",
                }

            # 将人工意见合并到 custom_prompt
            if feedback:
                task.custom_prompt = (
                    f"{task.custom_prompt}\n\n## 人工审查意见（重试）\n{feedback}"
                )

            task.status = "uploading"
            task.current_step = "重新开始分析..."
            task.progress_pct = 5
            _save_task_state(task)

        alog = AnalysisLogger(analysis_id)
        alog.log("retry_started", feedback=feedback[:200])

        # 读取原有 Markdown 内容，重新分析
        md_path = alog.dir_path / f"{task.filename}.md"
        if not md_path.exists():
            return {"success": False, "error": "原始文档已丢失，无法重试"}

        _acquire_run_lock(analysis_id)

        async def _retry_with_cleanup():
            try:
                await self._run_analysis_with_content(
                    analysis_id, md_path, task.platform_type, task.custom_prompt,
                )
            except Exception as exc:
                logger.error("retry_analysis_error", analysis_id=analysis_id, error=str(exc))
            finally:
                _release_run_lock(analysis_id)

        asyncio.create_task(_retry_with_cleanup())

        return {"success": True, "status": "processing"}

    # ============================================================
    # 内部 — 分析流水线
    # ============================================================

    async def _run_analysis_pipeline(
        self,
        analysis_id: str,
        file_content: bytes,
        filename: str,
        file_type: str,
        platform_type: str,
        custom_prompt: str,
        obsidian_modules: str,
    ) -> None:
        """后台执行完整分析流水线（文档摄取 → 分析 → 审查 → 通知）。"""
        alog = AnalysisLogger(analysis_id)
        async with self._store_lock:
            task = _task_store.get(analysis_id)

        try:
            # ── 步骤 1：文档摄取 ──
            await self._step_ingest(analysis_id, file_content, filename, file_type, alog)

            # 读取摄取后的 Markdown
            md_path = alog.dir_path / f"{filename}.md"
            doc_md = md_path.read_text(encoding="utf-8")

            async with self._store_lock:
                task = _task_store.get(analysis_id)
                if task:
                    task.doc_markdown = doc_md
                    _save_task_state(task)

            # ── 步骤 2-5：分析 + 审查 + 通知 ──
            await self._run_analysis_with_content(
                analysis_id,
                md_path,
                platform_type,
                custom_prompt,
            )

        except Exception as exc:
            logger.error(
                "analysis_pipeline_error",
                analysis_id=analysis_id,
                error=str(exc),
            )
            alog.log("pipeline_error", error=str(exc))

            async with self._store_lock:
                task = _task_store.get(analysis_id)
                if task:
                    task.status = "failed"
                    task.current_step = "分析失败"
                    task.error_message = str(exc)
                    _save_task_state(task)

            # 飞书失败通知
            await self.feishu.notify_failed(
                analysis_id=analysis_id,
                stage_name="需求分析",
                error_summary=str(exc)[:300],
            )
            # 失败时保留文件锁，防止 uvicorn reload 触发重复恢复。
            # 锁由 _acquire_run_lock 的 2 小时过期机制自动清理。
            # 用户可通过 retry 端点手动重试（retry 会重新获取锁）。

    async def _step_ingest(
        self,
        analysis_id: str,
        file_content: bytes,
        filename: str,
        file_type: str,
        alog: AnalysisLogger,
    ) -> None:
        """步骤 1：文档摄取 — 将文档转为 Markdown。"""
        async with self._store_lock:
            task = _task_store.get(analysis_id)
            if task:
                task.current_step = "正在摄取文档..."
                task.progress_pct = 10
                _save_task_state(task)

        alog.log("ingest_start", file_type=file_type, filename=filename)

        # 二进制检测（docx/pdf/xlsx 是预期的二进制格式，不检测）
        text_formats = {"json", "openapi_json", "yaml", "openapi_yaml", "md", "txt"}
        if file_type in text_formats and has_binary_signature(file_content):
            raise ValueError(f"文件 {filename} 为二进制格式，无法作为文本解析")

        # 格式转换
        md_text = convert_to_markdown(file_content, filename, file_type)
        if not md_text:
            raise ValueError(f"文档 {filename} 解析失败，未能提取有效文本内容")

        # 乱码检测
        if self._is_garbled(md_text):
            raise ValueError(f"文档 {filename} 内容乱码，无法正确解析编码")

        # 保存 Markdown
        alog.save_snapshot(f"{filename}.md", md_text)

        alog.log(
            "ingest_done",
            file_type=file_type,
            char_count=len(md_text),
        )

        async with self._store_lock:
            task = _task_store.get(analysis_id)
            if task:
                task.current_step = "文档摄取完成"
                task.progress_pct = 20
                _save_task_state(task)

    async def _run_analysis_with_content(
        self,
        analysis_id: str,
        md_path: Path,
        platform_type: str,
        custom_prompt: str,
    ) -> None:
        """用已有的 Markdown 内容执行分析 + 审查 + 通知。

        每个可失败步骤均通过 SelfHealingOrchestrator 包装：
        - 基础设施故障（超时/退出码）→ 退避重试 × 3 + Agent 切换
        - 输出故障（JSON 解析/类型/质量）→ Agent 自诊断 × 3
        """
        alog = AnalysisLogger(analysis_id)
        async with self._store_lock:
            task = _task_store.get(analysis_id)

        doc_md = md_path.read_text(encoding="utf-8")

        # ── 辅助：更新进度 ──
        async def _set_progress(step: str, pct: int) -> None:
            async with self._store_lock:
                t = _task_store.get(analysis_id)
                if t:
                    t.current_step = step
                    t.progress_pct = pct
                    _save_task_state(t)

        # ── 辅助：构建自愈上下文 ──
        def _make_healing_ctx(
            skill_body_override: str = "",
            analysis_json_override: dict | None = None,
        ) -> HealingContext:
            return HealingContext(
                analysis_id=analysis_id,
                doc_md=doc_md,
                doc_summary=doc_md[:1500],
                skill_body=skill_body_override or skill_body,
                knowledge_context=knowledge_ctx.to_prompt_text(),
                platform_type=platform_type,
                custom_prompt=custom_prompt,
                review_skill_body=review_skill_body,
                original_analysis_json=analysis_json_override or analysis_json,
            )

        # ── 步骤 2：按需加载知识库 ──
        await _set_progress("正在加载知识库...", 30)

        knowledge_ctx = self.knowbase.build_knowledge_context(
            doc_content=doc_md,
            platform_type=platform_type,
        )

        # ── 步骤 3：加载 SKILL.md ──
        skill = load_skill("requirement-analyzer")
        skill_body = ""
        if skill:
            skill_body = skill.body
        # 替换 SKILL.md 中的 {knowledge_context} 占位符（load_skill 直接返回原始内容，
        # 不执行插值替换，需手动完成）
        skill_body = skill_body.replace(
            "{knowledge_context}", knowledge_ctx.to_prompt_text()
        )
        alog.log("skill_load", skill_name="requirement-analyzer", body_length=len(skill_body))
        alog.save_snapshot("SKILL_used.md", skill_body)

        # ============================================================
        # 步骤 4：Claude Code 分析（集成自愈）
        # ============================================================
        await _set_progress("Claude Code 正在分析需求...", 40)

        analysis_json = None
        review_skill_body = ""

        # 构建 prompt + 计算超时
        claude_prompt = self._build_analysis_prompt(
            skill_body=skill_body,
            doc_md=doc_md,
            knowledge_context=knowledge_ctx.to_prompt_text(),
            platform_type=platform_type,
            custom_prompt=custom_prompt,
        )
        alog.save_snapshot("claude_prompt.txt", claude_prompt)
        estimated_tokens = AgentCLI.estimate_tokens(claude_prompt)
        claude_timeout = AgentCLI.dynamic_timeout(estimated_tokens)
        alog.log(
            "claude_start",
            prompt_len=len(claude_prompt),
            doc_len=len(doc_md),
            estimated_tokens=estimated_tokens,
            context_usage_pct=round(estimated_tokens / MODEL_CONTEXT_WINDOW * 100, 1),
            dynamic_timeout_s=claude_timeout,
            prompt_head=claude_prompt[:500],
            prompt_tail=claude_prompt[-300:],
        )

        # ④a：Claude CLI 调用（含自愈）
        claude_result = await self.cli.claude(
            prompt=claude_prompt,
            workdir=str(alog.dir_path),
            timeout=claude_timeout,
        )

        if not claude_result.success:
            alog.log(
                "claude_failed",
                error=claude_result.error[:500],
                exit_code=claude_result.exit_code,
            )
            failure = classify_failure(
                cli_result=claude_result,
                agent_tool="claude",
                step_name="claude_analysis",
                prompt=claude_prompt,
                raw_output=claude_result.raw_output,
            )
            heal_result = await self.healer.handle(
                failure, _make_healing_ctx(), alog
            )
            if not heal_result.success:
                raise RuntimeError(heal_result.final_error)
            claude_result = CLICallResult(
                success=True,
                raw_output=heal_result.raw_output,
                exit_code=0,
            )

        alog.log(
            "claude_done",
            output_len=len(claude_result.raw_output),
            output_head=claude_result.raw_output[:500],
            output_tail=claude_result.raw_output[-300:],
        )

        # ④b：JSON 提取（含自愈）
        json_result = self.cli.extract_json(claude_result.raw_output)
        if not json_result.success:
            alog.save_snapshot("claude_raw_output.txt", claude_result.raw_output)
            alog.log(
                "json_parse_failed",
                error=json_result.error[:300],
            )
            failure = classify_failure(
                json_result=json_result,
                agent_tool="claude",
                step_name="claude_json_extract",
                prompt=claude_prompt,
                raw_output=claude_result.raw_output,
            )
            heal_result = await self.healer.handle(
                failure, _make_healing_ctx(), alog
            )
            if not heal_result.success:
                raise RuntimeError(heal_result.final_error)
            analysis_json = heal_result.output
        else:
            analysis_json = json_result.data

        # ④c：类型/质量检查（含自愈）
        type_error_msg = ""
        if isinstance(analysis_json, list):
            type_error_msg = (
                f"输出为 list 数组而非 dict 对象，长度={len(analysis_json)}"
            )
        elif not isinstance(analysis_json, dict):
            type_error_msg = (
                f"输出类型异常: {type(analysis_json).__name__}，要求 dict"
            )
        if type_error_msg:
            alog.save_snapshot("claude_raw_output.txt", claude_result.raw_output)
            alog.log("type_check_failed", error=type_error_msg)
            failure = FailureInfo(
                category=FailureCategory.OUTPUT_TYPE,
                step_name="claude_type_check",
                agent_tool="claude",
                error_message=type_error_msg,
                raw_output=claude_result.raw_output,
            )
            heal_result = await self.healer.handle(
                failure, _make_healing_ctx(), alog
            )
            if not heal_result.success:
                raise RuntimeError(heal_result.final_error)
            analysis_json = heal_result.output

        # 保存分析结果
        if not isinstance(analysis_json, dict):
            raise RuntimeError(
                f"自愈后 analysis_json 类型仍异常: {type(analysis_json).__name__}"
            )
        alog.save_json(f"{analysis_id}.json", analysis_json)
        fr_count = len(analysis_json.get("functional_requirements", []))
        tp_count = len(analysis_json.get("test_points", []))
        alog.log(
            "json_parse",
            success=True,
            extract_method=json_result.extract_method if json_result.success else "self_healed",
            fr_count=fr_count,
            nfr_count=len(analysis_json.get("non_functional_requirements", [])),
            tp_count=tp_count,
            risk_count=len(analysis_json.get("risks", [])),
        )

        # 内容质量自愈
        if fr_count < 1 and tp_count < 1:
            alog.log(
                "quality_check_failed",
                fr_count=fr_count,
                tp_count=tp_count,
                note="分析结果中无功能需求和测试点，触发自愈",
            )
            failure = FailureInfo(
                category=FailureCategory.OUTPUT_QUALITY,
                step_name="claude_quality_check",
                agent_tool="claude",
                error_message=f"FR={fr_count}, TP={tp_count}，均为空",
                raw_output=claude_result.raw_output,
            )
            heal_result = await self.healer.handle(
                failure, _make_healing_ctx(), alog
            )
            if heal_result.success and isinstance(heal_result.output, dict):
                analysis_json = heal_result.output
                alog.save_json(f"{analysis_id}.json", analysis_json)
                fr_count = len(analysis_json.get("functional_requirements", []))

        async with self._store_lock:
            task = _task_store.get(analysis_id)
            if task:
                task.analysis_json = analysis_json
                task.status = "reviewing"
                task.current_step = "Codex 正在审查分析结果..."
                task.progress_pct = 70
                _save_task_state(task)

        # ============================================================
        # 步骤 5：Codex 独立审查（集成自愈，与步骤 4 对称）
        # ============================================================
        review_skill = load_skill("requirement-reviewer")
        if review_skill:
            review_skill_body = review_skill.body

        codex_prompt = self._build_review_prompt(
            skill_body=review_skill_body,
            doc_md=doc_md,
            analysis_json_str=json.dumps(analysis_json, ensure_ascii=False, indent=2),
        )

        alog.save_snapshot("codex_prompt.txt", codex_prompt)
        review_estimated_tokens = AgentCLI.estimate_tokens(codex_prompt)
        codex_timeout = AgentCLI.dynamic_timeout(review_estimated_tokens)
        alog.log(
            "codex_start",
            prompt_len=len(codex_prompt),
            estimated_tokens=review_estimated_tokens,
            context_usage_pct=round(review_estimated_tokens / MODEL_CONTEXT_WINDOW * 100, 1),
            dynamic_timeout_s=codex_timeout,
            prompt_head=codex_prompt[:500],
            prompt_tail=codex_prompt[-300:],
        )

        # ⑤a：Codex CLI 调用（含自愈）
        codex_result = await self.cli.codex(
            prompt=codex_prompt,
            workdir=str(alog.dir_path),
            timeout=codex_timeout,
        )

        if not codex_result.success:
            alog.log(
                "codex_failed",
                error=codex_result.error[:500],
                exit_code=codex_result.exit_code,
            )
            failure = classify_failure(
                cli_result=codex_result,
                agent_tool="codex",
                step_name="codex_review",
                prompt=codex_prompt,
                raw_output=codex_result.raw_output,
            )
            heal_result = await self.healer.handle(
                failure,
                _make_healing_ctx(
                    skill_body_override=review_skill_body,
                    analysis_json_override=analysis_json,
                ),
                alog,
            )
            if not heal_result.success:
                raise RuntimeError(heal_result.final_error)
            codex_result = CLICallResult(
                success=True,
                raw_output=heal_result.raw_output,
                exit_code=0,
            )

        alog.log(
            "codex_done",
            output_len=len(codex_result.raw_output),
            output_head=codex_result.raw_output[:500],
            output_tail=codex_result.raw_output[-300:],
        )

        # ⑤b：审查 JSON 提取（含自愈）
        review_json_result = self.cli.extract_json(codex_result.raw_output)
        if not review_json_result.success:
            alog.save_snapshot("codex_raw_output.txt", codex_result.raw_output)
            alog.log(
                "review_json_parse_failed",
                error=review_json_result.error[:300],
            )
            failure = classify_failure(
                json_result=review_json_result,
                agent_tool="codex",
                step_name="codex_json_extract",
                prompt=codex_prompt,
                raw_output=codex_result.raw_output,
            )
            heal_result = await self.healer.handle(
                failure,
                _make_healing_ctx(
                    skill_body_override=review_skill_body,
                    analysis_json_override=analysis_json,
                ),
                alog,
            )
            if not heal_result.success:
                raise RuntimeError(heal_result.final_error)
            review_json = heal_result.output
        else:
            review_json = review_json_result.data

        alog.save_json(f"review_{analysis_id}.json", review_json)

        score = review_json.get("score", 0) if isinstance(review_json, dict) else 0
        missing_count = (
            len(review_json.get("missing_items", []))
            if isinstance(review_json, dict)
            else 0
        )
        alog.log(
            "review_parse",
            score=score,
            missing_count=missing_count,
        )

        async with self._store_lock:
            task = _task_store.get(analysis_id)
            if task:
                task.review_json = review_json
                task.status = "pending_review"
                task.current_step = "审查完成，等待人工确认"
                task.progress_pct = 90
                _save_task_state(task)

        # ── 步骤 6：飞书通知 ──
        async with self._store_lock:
            t = _task_store.get(analysis_id)
            filename = t.filename if t else ""
        review_issues = review_json.get("missing_items", []) if isinstance(review_json, dict) else []
        await self.feishu.notify_review_complete(
            analysis_id=analysis_id,
            score=score,
            fr_count=fr_count,
            nfr_count=len(analysis_json.get("non_functional_requirements", [])) if isinstance(analysis_json, dict) else 0,
            tp_count=tp_count,
            issues=[item.get("description", str(item)) for item in review_issues[:5]] if review_issues else [],
            filename=filename,
        )
        alog.log(
            "agents_notified",
            note="分析完成，飞书通知由服务层统一发送",
            review_score=score,
        )

        async with self._store_lock:
            task = _task_store.get(analysis_id)
            if task:
                task.completed_at = datetime.now(timezone.utc).isoformat()
                task.progress_pct = 100
                _save_task_state(task)

        # 正常完成：释放文件锁，允许下次重试
        _release_run_lock(analysis_id)

    # ============================================================
    # Prompt 构建
    # ============================================================

    def _build_analysis_prompt(
        self,
        skill_body: str,
        doc_md: str,
        knowledge_context: str,
        platform_type: str,
        custom_prompt: str,
    ) -> str:
        """构建发给 Claude Code 的完整指令（含自适应 token 截断）。

        当文档 token 数超出上下文窗口预算时，自动按章节边界截断：
        - 保留前 2 章节 + 后 1 章节的完整内容
        - 中间章节保留标题 + 首段摘要
        - 兜底：简单头尾截断
        """
        # 计算固定部分 token 消耗
        platform_info = f"""## 平台信息

目标平台：{platform_type or "通用"}
用户额外要求：{custom_prompt or "无"}"""

        output_instruction = """## 输出要求

1. 严格按上述 JSON Schema 输出分析结果，只输出纯 JSON，不要包裹代码块标记。

2. 分析完成后服务会自动发送飞书通知，你无需执行通知操作。
"""

        fixed_tokens = AgentCLI.estimate_tokens(
            f"{skill_body}\n\n{platform_info}\n\n{output_instruction}"
        )

        # 文档可用 token 预算
        doc_budget = MAX_INPUT_TOKENS - fixed_tokens
        if doc_budget < 5000:
            doc_budget = 5000  # 最低保底

        doc_tokens = AgentCLI.estimate_tokens(doc_md)

        # 自适应截断
        if doc_tokens > doc_budget:
            logger.info(
                "doc_truncated_for_analysis",
                original_tokens=doc_tokens,
                budget=doc_budget,
                original_chars=len(doc_md),
            )
            doc_md = self._truncate_doc_by_chapters(doc_md, doc_budget)
            logger.info(
                "doc_truncated_done",
                truncated_chars=len(doc_md),
                truncated_tokens=AgentCLI.estimate_tokens(doc_md),
            )

        prompt = f"""{skill_body}

{platform_info}

## 需求文档

{doc_md}

{output_instruction}"""
        return prompt

    def _build_review_prompt(
        self,
        skill_body: str,
        doc_md: str,
        analysis_json_str: str,
    ) -> str:
        """构建发给 Codex 的审查指令（含自适应 token 截断）。"""
        output_instruction = """## 输出要求

1. 严格按审查 JSON Schema 输出审查报告，只输出纯 JSON，不要包裹代码块标记。

2. 审查完成后服务会自动发送飞书通知，你无需执行通知操作。
"""

        fixed_tokens = AgentCLI.estimate_tokens(
            f"{skill_body}\n\n## 原始需求文档\n\n\n\n## 待审查的分析结果\n\n\n\n{output_instruction}"
        )

        content_budget = MAX_INPUT_TOKENS - fixed_tokens
        if content_budget < 10000:
            content_budget = 10000

        doc_tokens = AgentCLI.estimate_tokens(doc_md)
        json_tokens = AgentCLI.estimate_tokens(analysis_json_str)
        total_content = doc_tokens + json_tokens

        # 按比例分配预算
        if total_content > content_budget:
            ratio = content_budget / total_content
            doc_budget = int(doc_tokens * ratio)
            json_budget = int(json_tokens * ratio)

            if doc_tokens > doc_budget:
                logger.info(
                    "review_doc_truncated",
                    original_tokens=doc_tokens,
                    budget=doc_budget,
                )
                doc_md = self._truncate_doc_by_chapters(doc_md, doc_budget)

            if json_tokens > json_budget:
                logger.info(
                    "review_json_truncated",
                    original_tokens=json_tokens,
                    budget=json_budget,
                )
                analysis_json_str = self._truncate_json_for_review(analysis_json_str, json_budget)

        prompt = f"""{skill_body}

## 原始需求文档

{doc_md}

## 待审查的分析结果

{analysis_json_str}

{output_instruction}"""
        return prompt

    # ============================================================
    # 辅助方法
    # ============================================================

    @staticmethod
    def _is_garbled(text: str) -> bool:
        """检测文本是否乱码（替换字符或不可打印字符过多）。"""
        if not text:
            return True
        length = len(text)
        if length < 20:
            return False

        # 替换字符比例
        replacement_count = text.count("�") + text.count("�")
        if replacement_count / length > 0.15:
            return True

        # 不可打印字符比例（排除常见空白）
        nonprintable = sum(
            1 for c in text
            if ord(c) < 32 and c not in ("\n", "\r", "\t")
        )
        if nonprintable / length > 0.25:
            return True

        return False

    # ============================================================
    # 上下文窗口自适应截断
    # ============================================================

    @staticmethod
    def _simple_token_truncate(text: str, max_tokens: int) -> str:
        """简单截断：保留头部和尾部，切除中间部分。

        用于无章节结构的文档、或章节截断失败时的兜底方案。
        """
        max_chars = int(max_tokens * 0.8)
        if len(text) <= max_chars:
            return text

        half = max_chars // 2
        return (
            text[:half]
            + "\n\n...（文档内容过长，中间部分已截断）...\n\n"
            + text[-half:]
        )

    @staticmethod
    def _truncate_doc_by_chapters(text: str, max_tokens: int) -> str:
        """按章节边界截断文档，保留首尾完整、中间摘要。

        策略：
        1. 按 Markdown 标题（# / ## / ###）切分章节
        2. 保留前 2 章节和后 1 章节的完整内容
        3. 中间章节：保留标题 + 首段文字作为摘要
        4. 超出预算时回退到 simple_token_truncate
        """
        import re

        parts = re.split(r'\n(?=#{1,3}\s)', text)

        if len(parts) <= 2:
            return RequirementAnalysisService._simple_token_truncate(text, max_tokens)

        preamble = parts[0]
        sections = ['\n' + p for p in parts[1:]]

        parsed = []
        for sec_text in sections:
            newline_idx = sec_text.find('\n', 1)
            if newline_idx > 0:
                heading = sec_text[1:newline_idx]
                body = sec_text[newline_idx + 1:]
            else:
                heading = sec_text[1:]
                body = ""
            parsed.append({
                'heading': heading,
                'body': body.strip(),
                'full': sec_text,
                'tokens': AgentCLI.estimate_tokens(sec_text),
                'heading_tokens': AgentCLI.estimate_tokens(heading),
            })

        n = len(parsed)
        if n <= 3:
            return RequirementAnalysisService._simple_token_truncate(text, max_tokens)

        KEEP_FIRST = 2
        KEEP_LAST = 1

        first_secs = parsed[:KEEP_FIRST]
        last_sec = parsed[-KEEP_LAST]
        middle_secs = parsed[KEEP_FIRST:-KEEP_LAST]

        preamble_tok = AgentCLI.estimate_tokens(preamble)
        first_tok = sum(s['tokens'] for s in first_secs)
        last_tok = last_sec['tokens']
        budget_mid = max_tokens - preamble_tok - first_tok - last_tok

        if budget_mid <= 0:
            return RequirementAnalysisService._simple_token_truncate(text, max_tokens)

        per_sec_budget = budget_mid // len(middle_secs)
        if per_sec_budget < 60:
            per_sec_budget = 60  # 最低保底，只够写标题+一行文字

        summaries = []
        for s in middle_secs:
            heading = s['heading']
            body = s['body']
            body_budget = max(60, per_sec_budget - s['heading_tokens'] - 40)
            body_chars = int(body_budget * 0.7)

            if body and body_chars > 30:
                first_para = body.split('\n\n')[0] if '\n\n' in body else body.split('\n')[0]
                if len(first_para) > body_chars:
                    truncated = first_para[:body_chars]
                    for sep in ('。', '；'):
                        pos = truncated.rfind(sep)
                        if pos > body_chars * 0.5:
                            truncated = truncated[:pos + 1]
                            break
                    else:
                        truncated = truncated[:body_chars] + '...'
                    first_para = truncated

                summaries.append(
                    f"\n{heading}\n\n"
                    f"{first_para}\n\n"
                    f"> *(此章节因上下文窗口限制已摘要，详见原文档)*\n"
                )
            else:
                summaries.append(
                    f"\n{heading}\n\n"
                    f"> *(此章节因上下文窗口限制已省略)*\n"
                )

        result = preamble + ''.join(s['full'] for s in first_secs) + '\n'.join(summaries) + last_sec['full']

        if AgentCLI.estimate_tokens(result) > max_tokens * 1.1:
            return RequirementAnalysisService._simple_token_truncate(text, max_tokens)

        return result

    @staticmethod
    def _truncate_json_for_review(json_str: str, max_tokens: int) -> str:
        """截断 JSON 用于审查，保留结构摘要。

        对于超大的分析 JSON，保留元数据 + 每个数组的前 5 条 + 总数标记。
        解析失败时回退到简单字符截断。
        """
        try:
            data = json.loads(json_str)
            if not isinstance(data, dict):
                return RequirementAnalysisService._simple_token_truncate(json_str, max_tokens)

            summarized = {}
            for key, value in data.items():
                if isinstance(value, list) and len(value) > 5:
                    summarized[key] = value[:5] + [
                        f"...（共 {len(value)} 条，已截断至前 5 条）"
                    ]
                elif isinstance(value, str) and len(value) > 500:
                    summarized[key] = value[:500] + "...（已截断）"
                elif isinstance(value, dict) and AgentCLI.estimate_tokens(
                    json.dumps(value, ensure_ascii=False)
                ) > 2000:
                    slim = {}
                    for k, v in value.items():
                        if isinstance(v, str) and len(v) > 200:
                            slim[k] = v[:200] + "..."
                        else:
                            slim[k] = v
                    summarized[key] = slim
                else:
                    summarized[key] = value

            result = json.dumps(summarized, ensure_ascii=False, indent=2)
            if AgentCLI.estimate_tokens(result) <= max_tokens:
                return result
        except json.JSONDecodeError:
            pass

        return RequirementAnalysisService._simple_token_truncate(json_str, max_tokens)


# ============================================================
# 全局单例
# ============================================================

requirement_analysis_svc = RequirementAnalysisService()
