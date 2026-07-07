"""Abstract LLM provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.llm.types import LLMRequest, LLMResponse


class AbstractLLMProvider(ABC):
    """Every LLM provider must implement this interface."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        ...
