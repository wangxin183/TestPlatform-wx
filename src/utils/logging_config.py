"""Structured logging setup using structlog.

Output:
  - Colored console output for development
  - JSON file output to logs/ directory with rotation
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

from src.core.config import settings

LOG_DIR = Path(settings.logs.dir)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging() -> None:
    """Configure structlog with dual output (console + file)."""

    timestamper = structlog.processors.TimeStamper(fmt="iso")

    # Configure structlog to route through standard logging
    structlog.configure(
        processors=[
            structlog.stdlib.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.PositionalArgumentsFormatter(),
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Set up root logger with console + file output
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.logs.level.upper(), logging.INFO))
    root_logger.handlers.clear()

    # Console handler (human-readable)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(
        _StructlogConsoleFormatter()
    )
    root_logger.addHandler(console_handler)

    # Rotating file handler (JSON)
    log_file = LOG_DIR / "platform.log"
    file_handler = RotatingFileHandler(
        str(log_file),
        maxBytes=_parse_size(settings.logs.rotation),
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        _JSONFormatter()
    )
    root_logger.addHandler(file_handler)

    # Silence noisy libs
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("celery").setLevel(logging.WARNING)


class _StructlogConsoleFormatter(logging.Formatter):
    """Formats log records for human-readable console output."""

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, self.datefmt or "%Y-%m-%d %H:%M:%S,%f")
        level = record.levelname
        name = record.name

        # Extract the raw event/message
        msg = record.getMessage()

        # Parse JSON message from structlog to extract event and kwargs
        try:
            import json
            parsed = json.loads(msg)
            event = parsed.get("event", msg)
            # Build a human-readable string with key-value pairs
            parts = [f"{ts} {level:5} {name:20} {event}"]
            for k, v in parsed.items():
                if k != "event" and k != "timestamp" and k != "level" and k != "logger":
                    parts.append(f"{k}={v}")
            return " ".join(parts)
        except (json.JSONDecodeError, Exception):
            return f"{ts} {level:5} {name:20} {record.getMessage()}"


class _JSONFormatter(logging.Formatter):
    """Formats log records as JSON for file output."""

    def format(self, record: logging.LogRecord) -> str:
        import json as _json
        try:
            parsed = _json.loads(record.getMessage())
            return _json.dumps(parsed, ensure_ascii=False)
        except (_json.JSONDecodeError, Exception):
            return _json.dumps({
                "timestamp": self.formatTime(record, self.datefmt or "%Y-%m-%d %H:%M:%S,%f"),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }, ensure_ascii=False)


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    """Return a bound logger instance."""
    return structlog.get_logger(name)


def _parse_size(size_str: str) -> int:
    """Parse a size string like '10 MB' into bytes."""
    mapping = {"KB": 1024, "MB": 1024**2, "GB": 1024**3}
    parts = size_str.strip().split()
    if len(parts) == 2:
        num, unit = parts
        return int(float(num) * mapping.get(unit.upper(), 1))
    return int(size_str)


# Auto-setup on import
setup_logging()
