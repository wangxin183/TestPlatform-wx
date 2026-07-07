"""Performance test executor — generates scripts instead of running step-by-step.

Performance tests are not directly executable like UI/API tests. This executor:
1. Calls PerformanceScriptGenerator to produce a test plan + Locust script
2. Saves the generated artifacts
3. Returns results with status="generated" and paths to the generated files
"""

from __future__ import annotations

from src.executor.base import AbstractExecutor
from src.executor.registry import ExecutorRegistry
from src.executor.types import ExecutorConfig, StepAction, StepResult
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class PerformanceExecutor(AbstractExecutor):
    """Performance test executor — generates scripts, doesn't run them directly.

    Locust scripts are saved to storage/scripts/ for manual or CI execution.
    """

    platform_type = "performance"

    def __init__(self):
        self._config: ExecutorConfig | None = None
        self._generated_scripts: list[str] = []

    async def setup(self, config: ExecutorConfig) -> None:
        self._config = config
        self._generated_scripts = []
        logger.info("performance_executor_setup", url=config.target_url)

    async def execute_step(self, action: StepAction) -> StepResult:
        # Individual steps are not directly executed for performance tests.
        # The generator handles the full test case at once in execute_steps().
        return StepResult(
            step_number=action.step_number,
            status="generated",
            duration_ms=0,
            actual_result="Performance step — handled in batch generation",
        )

    async def execute_steps(self, actions: list[StepAction]) -> list[StepResult]:
        """All steps are recorded as 'generated' — actual script generation
        happens externally via PerformanceScriptGenerator before this call."""
        results: list[StepResult] = []
        for action in actions:
            results.append(StepResult(
                step_number=action.step_number,
                status="generated",
                duration_ms=0,
                actual_result=self._config.target_url or "性能测试脚本已生成",
            ))
        return results

    async def screenshot(self) -> str:
        return ""  # Performance tests don't take screenshots

    async def teardown(self) -> None:
        logger.info(
            "performance_executor_teardown",
            scripts_generated=len(self._generated_scripts),
        )

    async def health_check(self) -> dict:
        return {
            "connected": True,
            "details": "Performance executor ready (script generation mode)",
        }


# Register
ExecutorRegistry.register("performance", PerformanceExecutor)
