"""OpenAI provider — uses the openai SDK."""

import os
import time

from openai import AsyncOpenAI

from src.core.config import settings
from src.llm.base import AbstractLLMProvider
from src.llm.types import LLMRequest, LLMResponse, TokenUsage
from src.llm.providers.deepseek import DeepSeekProvider
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class OpenAIProvider(AbstractLLMProvider):
    provider_name = "openai"

    def __init__(self):
        config = settings.llm_providers_config.get("providers", {}).get("openai", {})
        self._api_key = os.environ.get(config.get("api_key_env", "OPENAI_API_KEY"), "")
        if not self._api_key:
            raise RuntimeError(f"API Key 未配置: {config.get('api_key_env', 'OPENAI_API_KEY')}")
        self._default_model = "gpt-4o-mini"
        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=config.get("api_base", "https://api.openai.com/v1"),
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        model = self._select_model(request)
        t0 = time.monotonic()

        response = await self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )

        latency_ms = int((time.monotonic() - t0) * 1000)
        content = response.choices[0].message.content or ""
        usage = TokenUsage(
            prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
            completion_tokens=response.usage.completion_tokens if response.usage else 0,
            total_tokens=response.usage.total_tokens if response.usage else 0,
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
            await self._client.models.list()
            return True
        except Exception:
            return False

    def _select_model(self, request: LLMRequest) -> str:
        if request.complexity == "high":
            return "gpt-4o"
        return self._default_model
