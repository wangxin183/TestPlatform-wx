"""LLM type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    content: str
    usage: TokenUsage
    model: str
    latency_ms: int = 0
    parsed_json: dict | list | None = None


@dataclass
class LLMRequest:
    system_prompt: str
    user_prompt: str
    task_tag: str = "parsing"          # parsing/analysis/generation/failure_reasoning/regression_selection
    complexity: str = "medium"          # low/medium/high
    expect_json: bool = True
    max_tokens: int = 4096
    temperature: float = 0.3
    pipeline_id: str | None = None
    stage_name: str | None = None
    budget_remaining_pct: float = 100.0
    messages: list[dict] | None = None  # optional: raw message list override
