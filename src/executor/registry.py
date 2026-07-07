"""Executor registry — maps platform types to executor classes."""

from __future__ import annotations

from src.executor.base import AbstractExecutor


class ExecutorRegistry:
    _executors: dict[str, type[AbstractExecutor]] = {}

    @classmethod
    def register(cls, platform_type: str, executor_cls: type[AbstractExecutor]):
        cls._executors[platform_type] = executor_cls

    @classmethod
    def get(cls, platform_type: str) -> AbstractExecutor:
        if platform_type not in cls._executors:
            raise ValueError(
                f"No executor registered for platform '{platform_type}'. "
                f"Available: {cls.list_all()}"
            )
        return cls._executors[platform_type]()

    @classmethod
    def list_all(cls) -> list[str]:
        return list(cls._executors.keys())
