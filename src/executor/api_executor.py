"""API executor — httpx-based HTTP API testing."""

from __future__ import annotations

import json
import time

import httpx
import jsonschema

from src.executor.base import AbstractExecutor
from src.executor.registry import ExecutorRegistry
from src.executor.types import ExecutorConfig, StepAction, StepResult
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class APIExecutor(AbstractExecutor):
    platform_type = "api"

    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._config: ExecutorConfig | None = None

    async def setup(self, config: ExecutorConfig) -> None:
        self._config = config
        base_url = config.api_base_url or ""
        headers = config.auth_headers or {}

        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=config.timeout_seconds,
        )

    async def execute_step(self, action: StepAction) -> StepResult:
        if not self._client:
            return StepResult(
                step_number=action.step_number,
                status="error",
                error_message="API client not initialized",
            )

        t0 = time.monotonic()
        try:
            if action.action_type == "api_call":
                method = (action.value or "GET").upper()
                resp = await self._client.request(method, action.target)

                actual = f"{method} {action.target} → {resp.status_code}"
                body = resp.text[:1000]

                # Validate status if expected in value
                if action.value and action.value.isdigit():
                    if resp.status_code != int(action.value):
                        raise AssertionError(
                            f"Expected status {action.value}, got {resp.status_code}"
                        )

                return StepResult(
                    step_number=action.step_number,
                    status="passed" if resp.status_code < 400 else "failed",
                    duration_ms=(time.monotonic() - t0) * 1000,
                    actual_result=f"{actual}\n{body}",
                )

            elif action.action_type == "assert":
                # action.target = JSON schema, action.value = response body
                schema = json.loads(action.target) if action.target else {}
                instance = json.loads(action.value) if action.value else {}
                jsonschema.validate(instance, schema)
                return StepResult(
                    step_number=action.step_number,
                    status="passed",
                    duration_ms=(time.monotonic() - t0) * 1000,
                    actual_result="Schema validation passed",
                )

            else:
                return StepResult(
                    step_number=action.step_number,
                    status="passed",
                    duration_ms=0,
                    actual_result=f"API action: {action.action_type}",
                )

        except Exception as exc:
            return StepResult(
                step_number=action.step_number,
                status="failed",
                duration_ms=(time.monotonic() - t0) * 1000,
                error_message=str(exc),
            )

    async def execute_steps(self, actions: list[StepAction]) -> list[StepResult]:
        results = []
        for action in actions:
            result = await self.execute_step(action)
            results.append(result)
        return results

    async def screenshot(self) -> str:
        return ""  # API testing doesn't take screenshots

    async def teardown(self) -> None:
        if self._client:
            await self._client.aclose()

    async def health_check(self) -> dict:
        if not self._client:
            return {"connected": False, "details": "Client not initialized"}
        try:
            resp = await self._client.get("/")
            return {"connected": True, "details": f"Base URL reachable: {resp.status_code}"}
        except Exception as exc:
            return {"connected": False, "details": str(exc)}


ExecutorRegistry.register("api", APIExecutor)
