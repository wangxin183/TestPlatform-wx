"""Per-stage file logger — writes detailed INFO-level logs for each pipeline stage.

Creates: logs/stages/{pipeline_id[:8]}/{stage_name}_{timestamp}.log
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path("logs/stages")


def get_stage_logger(pipeline_id: str, stage_name: str) -> logging.Logger:
    """Create a logger that writes to a stage-specific file.

    Usage:
        slog = get_stage_logger(pid, "generation")
        slog.info("llm_call_start", model="deepseek-v4-pro", input_len=12345)
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    safe_pid = pipeline_id[:8].replace("/", "_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / safe_pid / f"{stage_name}_{ts}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"stage.{pipeline_id[:8]}.{stage_name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # don't send to root logger

    # Remove existing handlers to avoid duplicates
    for h in logger.handlers[:]:
        logger.removeHandler(h)

    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)

    return logger
