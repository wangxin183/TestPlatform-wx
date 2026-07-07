"""Abstract base class for all pipeline stages."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from src.pipeline.context import PipelineContext
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class StageInput:
    """Input to a pipeline stage."""
    pipeline_id: str
    project_id: str
    context: PipelineContext
    stage_attempt: int = 1
    idempotency_key: str | None = None


@dataclass
class StageOutput:
    """Output from a pipeline stage."""
    stage_name: str
    status: str = "completed"  # completed / failed / skipped
    data: dict[str, Any] = field(default_factory=dict)
    log_file_path: str | None = None
    error: str | None = None

    @property
    def is_success(self) -> bool:
        return self.status == "completed"


class AbstractStage(ABC):
    """Base class for all pipeline stages.

    Subclasses implement execute() and provide a stage_name.
    """

    # ── Stage contract (override in subclasses) ──

    @classmethod
    def required_context_fields(cls) -> list[str]:
        """Context fields this stage reads. Validated before standalone run."""
        return []

    @classmethod
    def produced_context_fields(cls) -> list[str]:
        """Context fields this stage writes after successful execution."""
        return []

    # ── Abstract interface ──

    stage_name: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "stage_name", None):
            raise TypeError(f"{cls.__name__} must define 'stage_name' ClassVar with a non-empty string")

    @abstractmethod
    async def execute(self, stage_input: StageInput) -> StageOutput:
        ...

    async def run(self, stage_input: StageInput) -> StageOutput:
        """Template method that wraps execute() with logging."""
        logger.info(
            "stage_started",
            pipeline_id=stage_input.pipeline_id,
            stage=self.stage_name,
        )
        try:
            output = await self.execute(stage_input)
            logger.info(
                "stage_completed",
                pipeline_id=stage_input.pipeline_id,
                stage=self.stage_name,
                status=output.status,
            )
            return output
        except Exception as exc:
            logger.error(
                "stage_failed",
                pipeline_id=stage_input.pipeline_id,
                stage=self.stage_name,
                error=str(exc),
            )
            return StageOutput(
                stage_name=self.stage_name,
                status="failed",
                error=str(exc),
            )
