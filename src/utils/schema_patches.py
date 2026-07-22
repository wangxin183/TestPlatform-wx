"""SQLite 无迁移系统时的列补丁：向已有表添加缺失列。"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# table -> [(column_name, ddl_fragment), ...]
_COLUMN_PATCHES: dict[str, list[tuple[str, str]]] = {
    "test_cases": [
        ("generation_id", "ALTER TABLE test_cases ADD COLUMN generation_id VARCHAR(36)"),
        ("source_analysis_id", "ALTER TABLE test_cases ADD COLUMN source_analysis_id VARCHAR(36)"),
        ("test_point_id", "ALTER TABLE test_cases ADD COLUMN test_point_id VARCHAR(36)"),
        ("automation_level", "ALTER TABLE test_cases ADD COLUMN automation_level VARCHAR(20)"),
        ("module", "ALTER TABLE test_cases ADD COLUMN module VARCHAR(100)"),
        ("exec_script", "ALTER TABLE test_cases ADD COLUMN exec_script JSON"),
        ("compile_status", "ALTER TABLE test_cases ADD COLUMN compile_status VARCHAR(20)"),
        ("compile_errors", "ALTER TABLE test_cases ADD COLUMN compile_errors JSON"),
        ("execution_mode", "ALTER TABLE test_cases ADD COLUMN execution_mode VARCHAR(20)"),
        ("step_contracts", "ALTER TABLE test_cases ADD COLUMN step_contracts JSON"),
        ("precondition_spec", "ALTER TABLE test_cases ADD COLUMN precondition_spec JSON"),
        (
            "automation_block_reason",
            "ALTER TABLE test_cases ADD COLUMN automation_block_reason VARCHAR(500)",
        ),
        ("assertion_quality", "ALTER TABLE test_cases ADD COLUMN assertion_quality VARCHAR(20)"),
    ],
}


async def apply_schema_patches(engine: AsyncEngine) -> None:
    """检查并补齐缺失列（幂等）。"""
    async with engine.begin() as conn:
        for table, patches in _COLUMN_PATCHES.items():
            try:
                result = await conn.execute(text(f"PRAGMA table_info({table})"))
                existing = {row[1] for row in result.fetchall()}
            except Exception as exc:
                logger.warning("schema_patch_table_info_failed", table=table, error=str(exc))
                continue

            for col_name, ddl in patches:
                if col_name in existing:
                    continue
                try:
                    await conn.execute(text(ddl))
                    logger.info("schema_patch_column_added", table=table, column=col_name)
                except Exception as exc:
                    logger.warning(
                        "schema_patch_column_failed",
                        table=table,
                        column=col_name,
                        error=str(exc),
                    )
