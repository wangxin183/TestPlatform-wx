"""阿里云百炼 / MaaS OpenAI 兼容 Provider（含多模态 Vision）。"""

from __future__ import annotations

import base64
import os
import time

from openai import AsyncOpenAI

from src.core.config import settings
from src.llm.base import AbstractLLMProvider
from src.llm.providers.deepseek import DeepSeekProvider
from src.llm.types import LLMRequest, LLMResponse, TokenUsage
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class DashScopeMaaSProvider(AbstractLLMProvider):
    provider_name = "dashscope_maas"

    def __init__(self) -> None:
        config = settings.llm_providers_config.get("providers", {}).get(
            "dashscope_maas", {}
        )
        self._api_key = os.environ.get(
            config.get("api_key_env", "DASHSCOPE_API_KEY"), ""
        )
        if not self._api_key:
            raise RuntimeError(
                f"API Key 未配置: {config.get('api_key_env', 'DASHSCOPE_API_KEY')}"
            )
        self._default_model = "qwen3.6-plus"
        models = config.get("models") or []
        if models and isinstance(models[0], dict) and models[0].get("name"):
            self._default_model = str(models[0]["name"])
        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=config.get(
                "api_base",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        model = self._select_model(request)
        messages = request.messages
        if messages is None:
            user_content: list[dict] | str = request.user_prompt
            if request.images:
                parts: list[dict] = [{"type": "text", "text": request.user_prompt}]
                for img in request.images:
                    b64 = base64.b64encode(img).decode("ascii")
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}",
                            },
                        }
                    )
                user_content = parts
            messages = [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": user_content},
            ]

        t0 = time.monotonic()
        create_kwargs: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        # VL 模型本身擅长 grounding；非 VL 才开 thinking 补顺序推理
        if request.images:
            is_vl = "vl" in str(model).lower()
            create_kwargs["extra_body"] = {"enable_thinking": not is_vl}
            if not is_vl:
                create_kwargs["max_tokens"] = max(int(request.max_tokens or 512), 2048)
        response = await self._client.chat.completions.create(**create_kwargs)
        latency_ms = int((time.monotonic() - t0) * 1000)
        msg = response.choices[0].message
        content = (msg.content or "").strip()
        reasoning = (getattr(msg, "reasoning_content", None) or "").strip()
        # 优先正文；正文为空才回退 reasoning（避免把长思考拼进 JSON 提取）
        if not content and reasoning:
            content = reasoning
        usage = TokenUsage(
            prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
            completion_tokens=(
                response.usage.completion_tokens if response.usage else 0
            ),
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
        return bool(self._api_key)

    def _select_model(self, request: LLMRequest) -> str:
        if request.model:
            return request.model
        return self._default_model
