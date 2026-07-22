"""Common LLM call wrapper with retry, timeout handling, and fallback logging.

All pipeline stages should use llm_call() instead of calling provider.complete()
directly. This provides consistent error handling, JSON parsing diagnostics,
and timeout/retry logic.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from src.core.config import settings
from src.llm.router import llm_router
from src.llm.types import LLMRequest, LLMResponse
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


async def llm_call(request: LLMRequest, max_retries: Optional[int] = None) -> LLMResponse:
    """Call an LLM with automatic routing, retry, and diagnostic logging.

    Retries once on timeout, and once on parse failure (with model fallback).
    All failures are logged with the raw response content for debugging.
    """
    last_error = None
    request.max_tokens = request.max_tokens or 16384
    if max_retries is None:
        max_retries = int(getattr(settings.llm, "max_retries", 0) or 0)

    for attempt in range(max_retries + 1):
        try:
            provider, model = await llm_router.route(request)
            if not request.model:
                request.model = model

            logger.debug(
                "llm_call_attempt",
                task_tag=request.task_tag,
                model=request.model or model,
                attempt=attempt + 1,
                pipeline_id=request.pipeline_id,
                stage_name=request.stage_name,
            )

            response = await provider.complete(request)

            # If JSON was expected but not parsed, log diagnostic info
            if request.expect_json and response.parsed_json is None:
                logger.error(
                    "llm_parse_failed",
                    task_tag=request.task_tag,
                    model=response.model,
                    attempt=attempt + 1,
                    content_length=len(response.content),
                    content_preview=response.content[:500],
                    content_tail=response.content[-200:] if len(response.content) > 200 else "",
                    latency_ms=response.latency_ms,
                    pipeline_id=request.pipeline_id,
                    stage_name=request.stage_name,
                )

                # Retry once on parse failure
                if attempt < max_retries:
                    logger.info(
                        "llm_retry_parse",
                        task_tag=request.task_tag,
                        next_attempt=attempt + 2,
                        pipeline_id=request.pipeline_id,
                        stage_name=request.stage_name,
                    )
                    continue
            logger.info(
                "llm_call_done",
                task_tag=request.task_tag,
                model=response.model,
                latency_ms=response.latency_ms,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
                pipeline_id=request.pipeline_id,
                stage_name=request.stage_name,
            )
            return response

        except asyncio.TimeoutError:
            last_error = f"Timeout after {settings.llm.request_timeout_seconds}s"
            logger.error(
                "llm_timeout",
                task_tag=request.task_tag,
                attempt=attempt + 1,
                timeout=settings.llm.request_timeout_seconds,
                pipeline_id=request.pipeline_id,
                stage_name=request.stage_name,
            )
            if attempt < max_retries:
                continue

        except Exception as exc:
            last_error = str(exc)
            logger.error(
                "llm_call_error",
                task_tag=request.task_tag,
                error=last_error[:500],
                attempt=attempt + 1,
                pipeline_id=request.pipeline_id,
                stage_name=request.stage_name,
            )
            if attempt < max_retries:
                continue

    # All retries exhausted — raise an exception
    raise RuntimeError(f"LLM调用失败(已重试{max_retries}次): {last_error}")


def truncate_prompt(text: str, max_chars: int) -> str:
    """Truncate prompt text to max_chars, logging a warning if truncation occurs."""
    if len(text) <= max_chars:
        return text
    logger.warning(
        "prompt_truncated",
        original_length=len(text),
        max_chars=max_chars,
        discarded_chars=len(text) - max_chars,
    )
    return text[:max_chars]
