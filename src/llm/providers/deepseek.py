"""DeepSeek provider — OpenAI-compatible API via httpx."""

from __future__ import annotations

import json
import os
import time

import httpx

from src.core.config import settings
from src.llm.base import AbstractLLMProvider
from src.llm.types import LLMRequest, LLMResponse, TokenUsage
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class DeepSeekProvider(AbstractLLMProvider):
    provider_name = "deepseek"

    def __init__(self):
        config = settings.llm_providers_config.get("providers", {}).get("deepseek", {})
        self._api_base = config.get("api_base", "https://api.deepseek.com/v1")
        self._api_key = os.environ.get(config.get("api_key_env", "DEEPSEEK_API_KEY"), "")
        if not self._api_key:
            raise RuntimeError(f"API Key 未配置: {config.get('api_key_env', 'DEEPSEEK_API_KEY')}")
        self._default_model = "deepseek-v4-pro"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        model = self._select_model(request)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=settings.llm.request_timeout_seconds) as client:
            resp = await client.post(
                f"{self._api_base}/chat/completions",
                headers=headers,
                json=body,
            )
        latency_ms = int((time.monotonic() - t0) * 1000)

        if resp.status_code != 200:
            logger.error(
                "deepseek_api_error",
                status=resp.status_code,
                body=resp.text[:500],
            )
            raise RuntimeError(f"DeepSeek API error {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        choice = data["choices"][0]
        content = choice["message"]["content"]

        usage = TokenUsage(
            prompt_tokens=data.get("usage", {}).get("prompt_tokens", 0),
            completion_tokens=data.get("usage", {}).get("completion_tokens", 0),
            total_tokens=data.get("usage", {}).get("total_tokens", 0),
        )

        # Try to parse JSON from content
        parsed = None
        if request.expect_json:
            parsed = self._extract_json(content)

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
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self._api_base}/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
            return resp.status_code == 200
        except Exception:
            return False

    def _select_model(self, request: LLMRequest) -> str:
        if request.model:
            return request.model
        config = settings.llm_providers_config.get("providers", {}).get("deepseek", {})
        models = config.get("models", [])
        if request.complexity == "low" and len(models) > 1:
            return models[1]["name"]  # cheaper model
        return self._default_model

    @staticmethod
    def _extract_json(text: str) -> dict | list | None:
        """Try to extract a JSON object or array from LLM output."""
        text = text.strip()
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Try between ```json ... ```
        if "```json" in text:
            try:
                start = text.index("```json") + 7
                end = text.index("```", start)
                return json.loads(text[start:end].strip())
            except (ValueError, json.JSONDecodeError):
                pass
        # Try first [ ... ] (array)
        try:
            start = text.index("[")
            end = text.rindex("]") + 1
            return json.loads(text[start:end])
        except (ValueError, json.JSONDecodeError):
            pass
        # Try first { ... } (object)
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            return json.loads(text[start:end])
        except (ValueError, json.JSONDecodeError):
            pass
        return None
