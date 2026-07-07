"""Abstract executor base class."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.executor.types import ExecutorConfig, StepAction, StepResult


class AbstractExecutor(ABC):
    """Every platform executor must implement this interface."""

    @property
    @abstractmethod
    def platform_type(self) -> str:
        ...

    @abstractmethod
    async def setup(self, config: ExecutorConfig) -> None:
        ...

    @abstractmethod
    async def execute_step(self, action: StepAction) -> StepResult:
        ...

    @abstractmethod
    async def execute_steps(self, actions: list[StepAction]) -> list[StepResult]:
        ...

    @abstractmethod
    async def screenshot(self) -> str:
        """Take a screenshot. Returns file path."""
        ...

    @abstractmethod
    async def teardown(self) -> None:
        ...

    @abstractmethod
    async def health_check(self) -> dict:
        """Check if the executor environment is ready."""
        ...
