"""需求分析专用日志 — 记录分析全流程的每一步操作。

每条分析任务一个独立的 analysis.log 文件（JSONL 格式），
用于问题排查、性能分析和 Skill 进化数据源。

Usage:
    from src.utils.analysis_logger import AnalysisLogger

    alog = AnalysisLogger(analysis_id="RA-0001")
    alog.log("ingest_start", file_type="docx")
    alog.log("ingest_done", char_count=12345)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

STORAGE_BASE = Path("storage/requirement_analyses")


class AnalysisLogger:
    """需求分析任务的逐步操作日志记录器。

    以 JSONL 格式追加写入 analysis.log 文件，
    每一步包含序号、时间戳、步骤名称和自定义键值对。
    """

    def __init__(self, analysis_id: str):
        self.analysis_id = analysis_id
        self._dir = STORAGE_BASE / analysis_id
        self._log_path = self._dir / "analysis.log"
        self._started = False
        # 从已有日志文件恢复序号（支持多次实例化续写）
        self._seq = self._load_max_seq()

    # ---- 公共接口 ----

    def log(self, step: str, **kwargs) -> None:
        """记录一步操作。

        Args:
            step: 步骤名称（如 ingest_start、claude_done 等）
            **kwargs: 步骤相关的自定义键值对
        """
        self._ensure_started()
        self._seq += 1

        entry = {
            "seq": self._seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "step": step,
            **kwargs,
        }

        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error(
                "analysis_log_write_error",
                analysis_id=self.analysis_id,
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
                "analysis_log_read_error",
                analysis_id=self.analysis_id,
                error=str(exc),
            )
            return []

    def save_snapshot(self, filename: str, content: str) -> str:
        """保存分析过程中的关键文件快照。

        Args:
            filename: 文件名（如 SKILL_used.md、prompt.txt）
            content: 文件内容

        Returns:
            保存的文件路径
        """
        self._ensure_started()
        path = self._dir / filename
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            logger.debug(
                "analysis_snapshot_saved",
                analysis_id=self.analysis_id,
                filename=filename,
                size=len(content),
            )
            return str(path)
        except Exception as exc:
            logger.error(
                "analysis_snapshot_error",
                analysis_id=self.analysis_id,
                filename=filename,
                error=str(exc),
            )
            return ""

    def save_json(self, filename: str, data: dict | list) -> str:
        """保存 JSON 数据到文件。

        Args:
            filename: 文件名（如 analysis_result.json、review_result.json）
            data: JSON 可序列化的数据

        Returns:
            保存的文件路径
        """
        content = json.dumps(data, ensure_ascii=False, indent=2)
        return self.save_snapshot(filename, content)

    # ---- 内部方法 ----

    def _ensure_started(self) -> None:
        """确保目录已创建。"""
        if not self._started:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._started = True

    def _load_max_seq(self) -> int:
        """从已有日志文件中恢复最大序号，实现续写。

        如果文件不存在或损坏，返回 0 表示从 1 开始。
        """
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
        """分析任务的工作目录路径。"""
        return self._dir
