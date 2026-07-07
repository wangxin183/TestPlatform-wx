"""Base agent class — provides skill loading and LLM retry shared by all agents.

Agents are domain-logic objects that use Skills (SKILL.md files) as
system prompts to accomplish tasks via LLM. The BaseAgent provides:

1. On-demand skill loading with variable interpolation (saves token cost)
2. Unified LLM retry with structured logging
3. A standard run(ctx) -> AgentOutput interface
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.llm.caller import llm_call
from src.llm.prompts.skill_loader import load_skill
from src.llm.types import LLMRequest, LLMResponse
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class AgentContext:
    """Standard input context for all agents."""

    pipeline_id: str = ""
    project_id: str = ""
    platform_type: str = ""
    custom_prompt: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentOutput:
    """Standard output from all agents."""

    success: bool = True
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class BaseAgent(ABC):
    """Lightweight agent base.

    Subclasses set `skill_name` and implement `run()`.
    Call `self._load_skill(**kwargs)` to load and interpolate a SKILL.md file.
    Call `self._retry_llm(request, max_retries, error_prefix)` for LLM retry.
    """

    skill_name: str | None = None  # e.g. "requirement-analyzer"

    # ── Skill loading ──

    def _load_skill(self, **interpolations: str) -> str | None:
        """Load a persistent skill from .agents/skills/<skill_name>/SKILL.md.

        Interpolates {placeholder} variables with provided kwargs.
        Returns None if no skill file exists or no skill_name is configured.
        """
        if not self.skill_name:
            return None

        skill = load_skill(self.skill_name)
        if not skill or not skill.body:
            logger.warning(
                "agent_skill_not_found",
                skill_name=self.skill_name,
            )
            return None

        body = skill.body
        for key, value in interpolations.items():
            body = body.replace(f"{{{key}}}", str(value))

        logger.info(
            "agent_skill_loaded",
            skill_name=skill.name,
            body_length=len(body),
        )
        return body

    # ── LLM retry ──

    async def _retry_llm(
        self,
        request: LLMRequest,
        max_retries: int = 1,
        error_prefix: str = "LLM 调用",
    ) -> LLMResponse:
        """Call LLM with retry on failure/empty response.

        Retries up to max_retries additional times (total = 1 + max_retries).
        Each retry is logged. Raises RuntimeError if all attempts fail.

        Args:
            request: The LLM request to send.
            max_retries: Additional attempts after the first (default 1).
            error_prefix: Prefix for the final error message.

        Returns:
            LLMResponse on success.

        Raises:
            RuntimeError: All attempts failed or returned empty.
        """
        last_error: Exception | None = None
        total_attempts = max_retries + 1

        for attempt in range(total_attempts):
            try:
                logger.info(
                    "agent_llm_attempt",
                    pipeline_id=request.pipeline_id,
                    attempt=attempt + 1,
                    total=total_attempts,
                    task_tag=request.task_tag,
                )
                response = await llm_call(request)

                # Check for empty response
                if response.content or response.parsed_json:
                    logger.info(
                        "agent_llm_done",
                        pipeline_id=request.pipeline_id,
                        attempt=attempt + 1,
                        model=response.model,
                        latency_ms=response.latency_ms,
                    )
                    return response

                raise ValueError("LLM returned empty response")

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "agent_llm_retry",
                    pipeline_id=request.pipeline_id,
                    attempt=attempt + 1,
                    total=total_attempts,
                    error=str(exc),
                )
                if attempt >= max_retries:
                    raise RuntimeError(
                        f"{error_prefix}失败（已重试 {max_retries} 次）: {last_error}"
                    ) from last_error

        # Unreachable — kept for type checker
        raise RuntimeError(f"{error_prefix}失败: {last_error}")

    # ── Interface ──

    @abstractmethod
    async def run(self, ctx: AgentContext) -> AgentOutput:
        """Execute the agent's task. Subclasses must implement."""
        ...
