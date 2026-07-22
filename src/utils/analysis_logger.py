"""任务逐步操作日志 — JSONL 格式，支持需求分析 / 用例生成等解耦模块。

Usage:
    from src.utils.analysis_logger import AnalysisLogger, GenerationLogger

    alog = AnalysisLogger(analysis_id="RA-0001")
    alog.log("ingest_start", file_type="docx")

    glog = GenerationLogger(generation_id="TCG-0001")
    glog.log("task_created", analysis_id="RA-0001")
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

RA_STORAGE_BASE = Path("storage/requirement_analyses")
TCG_STORAGE_BASE = Path("storage/testcase_generations")
EXE_STORAGE_BASE = Path("storage/execution_runs")

# 向后兼容别名
STORAGE_BASE = RA_STORAGE_BASE


class TaskStepLogger:
    """通用任务逐步日志记录器。

    以 JSONL 格式追加写入日志文件，
    每一步包含序号、时间戳、步骤名称和自定义键值对。
    """

    def __init__(
        self,
        task_id: str,
        *,
        storage_base: Path,
        log_filename: str = "task.log",
        log_event_prefix: str = "task",
    ):
        self.task_id = task_id
        self.analysis_id = task_id  # 兼容旧代码读取 alog.analysis_id
        self._storage_base = Path(storage_base)
        self._dir = self._storage_base / task_id
        self._log_path = self._dir / log_filename
        self._log_event_prefix = log_event_prefix
        self._started = False
        self._seq = self._load_max_seq()

    def log(self, step: str, **kwargs) -> None:
        """记录一步操作（自动补全中文 message）。"""
        from src.services.narrative_log import narrate

        self._ensure_started()
        self._seq += 1

        entry = {
            "seq": self._seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "step": step,
            **kwargs,
        }
        if not entry.get("message"):
            entry["message"] = narrate(step, **kwargs)

        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error(
                f"{self._log_event_prefix}_log_write_error",
                task_id=self.task_id,
                step=step,
                error=str(exc),
            )

    def read_logs(self) -> list[dict]:
        """读取所有日志记录。"""
        try:
            if not self._log_path.exists():
                return []
            logs = []
            with open(self._log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            logs.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            return logs
        except Exception as exc:
            logger.error(
                f"{self._log_event_prefix}_log_read_error",
                task_id=self.task_id,
                error=str(exc),
            )
            return []

    def save_snapshot(self, filename: str, content: str) -> str:
        """保存过程中的关键文件快照。"""
        self._ensure_started()
        path = self._dir / filename
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            logger.debug(
                f"{self._log_event_prefix}_snapshot_saved",
                task_id=self.task_id,
                filename=filename,
                size=len(content),
            )
            return str(path)
        except Exception as exc:
            logger.error(
                f"{self._log_event_prefix}_snapshot_error",
                task_id=self.task_id,
                filename=filename,
                error=str(exc),
            )
            return ""

    def save_json(self, filename: str, data: dict | list) -> str:
        """保存 JSON 数据到文件。"""
        content = json.dumps(data, ensure_ascii=False, indent=2)
        return self.save_snapshot(filename, content)

    def _ensure_started(self) -> None:
        if not self._started:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._started = True

    def _load_max_seq(self) -> int:
        try:
            if not self._log_path.exists():
                return 0
            max_seq = 0
            with open(self._log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        s = entry.get("seq", 0)
                        if s > max_seq:
                            max_seq = s
                    except json.JSONDecodeError:
                        continue
            return max_seq
        except Exception:
            return 0

    @property
    def dir_path(self) -> Path:
        return self._dir


class AnalysisLogger(TaskStepLogger):
    """需求分析任务日志（storage/requirement_analyses/{id}/analysis.log）。"""

    def __init__(self, analysis_id: str):
        super().__init__(
            analysis_id,
            storage_base=RA_STORAGE_BASE,
            log_filename="analysis.log",
            log_event_prefix="analysis",
        )


class GenerationLogger(TaskStepLogger):
    """用例生成任务日志（storage/testcase_generations/{id}/generation.log）。"""

    def __init__(self, generation_id: str):
        super().__init__(
            generation_id,
            storage_base=TCG_STORAGE_BASE,
            log_filename="generation.log",
            log_event_prefix="generation",
        )
        self.generation_id = generation_id


class ExecutionRunLogger(TaskStepLogger):
    """App UI 执行运行时桥接日志（storage/execution_runs/{id}/bridge.log）。"""

    def __init__(self, run_id: str):
        super().__init__(
            run_id,
            storage_base=EXE_STORAGE_BASE,
            log_filename="bridge.log",
            log_event_prefix="execution_run",
        )
        self.run_id = run_id
