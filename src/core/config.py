from __future__ import annotations

"""Application configuration loaded from YAML files and environment variables."""

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = BASE_DIR / "config"

# 加载项目根目录 .env 文件，使环境变量（如 API Key）生效
load_dotenv(BASE_DIR / ".env")


def _load_yaml(filename: str) -> dict[str, Any]:
    path = CONFIG_DIR / filename
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


class AppSettings(BaseSettings):
    name: str = "TestPlatform"
    version: str = "0.1.0"
    debug: bool = True
    secret_key: str = "change-me-in-development-only"


class ServerSettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]


class DatabaseSettings(BaseSettings):
    url: str = "sqlite+aiosqlite:///storage/test_platform.db"
    echo_sql: bool = False
    # Create tables on startup (development convenience). In non-debug,
    # this should be False and schema should be managed by Alembic.
    auto_create_tables: Optional[bool] = None


class RedisSettings(BaseSettings):
    url: str = "redis://localhost:6379/0"


class CelerySettings(BaseSettings):
    broker_url: str = "redis://localhost:6379/1"
    result_backend: str = "redis://localhost:6379/2"
    task_track_started: bool = True


class StorageSettings(BaseSettings):
    root: str = "storage"
    documents_dir: str = "storage/documents"
    screenshots_dir: str = "storage/screenshots"
    reports_dir: str = "storage/reports"


class LogSettings(BaseSettings):
    dir: str = "logs"
    level: str = "INFO"
    rotation: str = "10 MB"
    retention: str = "30 days"


class LLMSettings(BaseSettings):
    default_provider: str = "deepseek"
    per_pipeline_budget_usd: float = 3.0
    max_retries: int = 2
    request_timeout_seconds: int = 120


class PipelineSettings(BaseSettings):
    max_retries_per_case: int = 3
    execution_timeout_minutes: int = 60


class SecuritySettings(BaseSettings):
    """Security-related settings.

    API-key auth provides a minimal protection layer before a full user system exists.
    """

    # Enable API key auth for write endpoints (POST/PUT/PATCH/DELETE).
    # Default: enabled in non-debug, disabled in debug.
    api_key_auth_enabled: Optional[bool] = None
    # Shared API key value (recommended: set via environment and YAML in production).
    api_key: str = os.environ.get("TESTPLATFORM_API_KEY", "")


class Settings:
    """Aggregated settings from YAML config files.

    Usage:
        from src.core.config import settings
        print(settings.server.port)
    """

    def __init__(self) -> None:
        settings_yaml = _load_yaml("settings.yaml")
        self.app = AppSettings(**settings_yaml.get("app", {}))
        self.server = ServerSettings(**settings_yaml.get("server", {}))
        self.database = DatabaseSettings(**settings_yaml.get("database", {}))
        self.redis = RedisSettings(**settings_yaml.get("redis", {}))
        self.celery = CelerySettings(**settings_yaml.get("celery", {}))
        self.storage = StorageSettings(**settings_yaml.get("storage", {}))
        self.logs = LogSettings(**settings_yaml.get("logs", {}))
        self.llm = LLMSettings(**settings_yaml.get("llm", {}))
        self.pipeline = PipelineSettings(**settings_yaml.get("pipeline", {}))
        self.security = SecuritySettings(**settings_yaml.get("security", {}))
        self.llm_providers_config = _load_yaml("llm_providers.yaml")
        self.platforms_config = _load_yaml("platforms.yaml")

        # Default security behavior: protect write endpoints outside debug.
        if self.security.api_key_auth_enabled is None:
            self.security.api_key_auth_enabled = not bool(self.app.debug)

        if self.database.auto_create_tables is None:
            self.database.auto_create_tables = bool(self.app.debug)

    def resolve_path(self, relative_path: str) -> str:
        """Resolve a path relative to the project root."""
        return str(BASE_DIR / relative_path)


settings = Settings()
