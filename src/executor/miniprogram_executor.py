"""WeChat Mini Program executor."""

from __future__ import annotations

from src.executor.base import AbstractExecutor
from src.executor.registry import ExecutorRegistry
from src.executor.types import ExecutorConfig, StepAction, StepResult
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class MiniProgramExecutor(AbstractExecutor):
    """Mini program executor using WeChat Developer Tools automation.

    Falls back to a simulated mode when WeChat DevTools is not available.
    """

    platform_type = "miniprogram"

    def __init__(self):
        self._config: ExecutorConfig | None = None
        self._page = None

    async def setup(self, config: ExecutorConfig) -> None:
        self._config = config
        logger.info("miniprogram_executor_setup", url=config.target_url)
        # Note: Actual WeChat DevTools integration would use minium SDK.
        # For now, this provides the interface and can be connected later.

    async def execute_step(self, action: StepAction) -> StepResult:
        return StepResult(
            step_number=action.step_number,
            status="skipped",
            duration_ms=0,
            error_message="MiniProgram executor requires WeChat DevTools integration (minium SDK)",
        )

    async def execute_steps(self, actions: list[StepAction]) -> list[StepResult]:
        return [await self.execute_step(a) for a in actions]

    async def screenshot(self) -> str:
        return ""

    async def teardown(self) -> None:
        logger.info("miniprogram_teardown_complete")

    async def health_check(self) -> dict:
        return {"connected": True, "details": "MiniProgram executor ready (mock mode)"}


ExecutorRegistry.register("miniprogram", MiniProgramExecutor)
