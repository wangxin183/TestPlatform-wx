"""LLM cost-optimization router.

Evaluates declarative routing rules to select the best provider + model
for each request based on task complexity, budget, and availability.
"""

from __future__ import annotations

from typing import Any

from src.core.config import settings
from src.llm.base import AbstractLLMProvider
from src.llm.providers.deepseek import DeepSeekProvider
from src.llm.providers.openai import OpenAIProvider
from src.llm.providers.anthropic import AnthropicProvider
from src.llm.types import LLMRequest
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# Registry of all available providers
PROVIDER_REGISTRY: dict[str, type[AbstractLLMProvider]] = {
    "deepseek": DeepSeekProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
}


class LLMRouter:
    """Selects the best provider + model based on declarative rules."""

    def __init__(self):
        router_config = settings.llm_providers_config.get("router", {})
        self._default_provider = router_config.get("default_provider", "deepseek")
        self._default_model = router_config.get("default_model", "deepseek-v4-pro")
        self._rules = router_config.get("rules", [])

    async def route(self, request: LLMRequest) -> tuple[AbstractLLMProvider, str]:
        """Select a provider instance and model name for the request."""
        selected_provider = self._default_provider
        selected_model = self._default_model

        # Apply rules in priority order, stop at first match
        sorted_rules = sorted(self._rules, key=lambda r: r.get("priority", 99))

        for rule in sorted_rules:
            result = self._evaluate_rule(rule, request)
            if result:
                selected_provider = result.get("provider", selected_provider)
                selected_model = result.get("model", selected_model)
                logger.debug(
                    "llm_rule_matched",
                    rule_id=rule.get("id"),
                    provider=selected_provider,
                    model=selected_model,
                )
                break  # First matching rule wins

        provider_cls = PROVIDER_REGISTRY.get(selected_provider)
        if provider_cls is None:
            logger.warning(
                "llm_provider_not_found",
                provider=selected_provider,
                fallback=self._default_provider,
            )
            provider_cls = PROVIDER_REGISTRY[self._default_provider]
            selected_model = self._default_model

        return provider_cls(), selected_model

    def _evaluate_rule(
        self, rule: dict[str, Any], request: LLMRequest
    ) -> dict[str, str] | None:
        """Evaluate a single routing rule. Returns {provider, model} on match, or None."""
        conditions = rule.get("conditions", [])
        for cond in conditions:
            when = cond.get("when", {})
            route_spec = cond.get("route", {})

            # Failover rules are handled at call time by llm_call() retry logic
            if "primary_failed" in when:
                continue

            # Evaluate budget threshold condition
            budget_threshold = when.get("budget_remaining_pct")
            if budget_threshold is not None:
                threshold_val = float(str(budget_threshold).lstrip("<"))
                if request.budget_remaining_pct >= threshold_val:
                    continue

            task_tags = when.get("task_tag", [])
            required_complexity = when.get("complexity")

            # Match: task_tag in request matches one in rule
            if task_tags and request.task_tag in task_tags:
                # If complexity is specified, must match
                if required_complexity and request.complexity != required_complexity:
                    continue
                return {
                    "provider": route_spec.get("provider", self._default_provider),
                    "model": route_spec.get("model", self._default_model),
                }

        return None

    def list_providers(self) -> list[str]:
        return list(PROVIDER_REGISTRY.keys())


# Singleton
llm_router = LLMRouter()
