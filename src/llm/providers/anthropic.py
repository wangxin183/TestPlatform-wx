"""Anthropic provider — uses the anthropic SDK."""

import os
import time

from anthropic import AsyncAnthropic

from src.core.config import settings
from src.llm.base import AbstractLLMProvider
from src.llm.types import LLMRequest, LLMResponse, TokenUsage
from src.llm.providers.deepseek import DeepSeekProvider
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class AnthropicProvider(AbstractLLMProvider):
    provider_name = "anthropic"

    def __init__(self):
        config = settings.llm_providers_config.get("providers", {}).get("anthropic", {})
        self._api_key = os.environ.get(config.get("api_key_env", "ANTHROPIC_API_KEY"), "")
        if not self._api_key:
            raise RuntimeError(f"API Key 未配置: {config.get('api_key_env', 'ANTHROPIC_API_KEY')}")
        self._default_model = "claude-sonnet-4-20250514"
        self._client = AsyncAnthropic(api_key=self._api_key)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        model = self._default_model
        t0 = time.monotonic()

        response = await self._client.messages.create(
            model=model,
            system=request.system_prompt,
            messages=[{"role": "user", "content": request.user_prompt}],
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )

        latency_ms = int((time.monotonic() - t0) * 1000)
        content = response.content[0].text if response.content else ""
        usage = TokenUsage(
            prompt_tokens=response.usage.input_tokens if response.usage else 0,
            completion_tokens=response.usage.output_tokens if response.usage else 0,
            total_tokens=(response.usage.input_tokens + response.usage.output_tokens) if response.usage else 0,
        )

        parsed = None
        if request.expect_json:
            parsed = DeepSeekProvider._extract_json(content)

        return LLMResponse(
            content=content,
            usage=usage,
            model=model,
            latency_ms=latency_ms,
            parsed_json=parsed,
        )

    async def health_check(self) -> bool:
        if not self._api_key:
            return False
        try:
            await self._client.messages.create(
                model=self._default_model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception:
            return False
