"""Agent CLI — 兼容 shim。

⚠️  自 AgentRuntime 引入后（`src/agent_runtime/`），业务代码应改用
    `agent_runtime.run(AgentTask(role=..., ...))`。本模块保留仅为向后兼容：

- `CLICallResult` / `JSONExtractResult` / `estimate_tokens` /
  `dynamic_timeout` / `extract_json` / `repair_json_text` 全部 re-export 自
  `src.agent_runtime.cli_shared`。
- `AgentCLI.claude()` / `AgentCLI.codex()` 保留过渡实现，内部走
  `agent_runtime.run(role="requirement.analyzer" | "requirement.reviewer")`；
  下个版本将删除，请勿在新代码中使用。
"""

from __future__ import annotations

from typing import Optional

from src.agent_runtime import AgentTask, agent_runtime
from src.agent_runtime.cli_shared import (
    DEFAULT_CODE_BLOCK_TIMEOUT,
    DEFAULT_TIMEOUT_SECONDS,
    CLICallResult,
    JSONExtractResult,
    dynamic_timeout,
    estimate_tokens,
    extract_json,
    recover_json_from_workdir,
    repair_json_text,
    run_cli_command,
)
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


BINARY_LOOKUP = {
    "claude": "claude",
    "codex": "codex",
}


class AgentCLI:
    """兼容 shim：将旧的 self.cli.claude / self.cli.codex 转发至 AgentRuntime。

    保留原有构造参数与返回类型（`CLICallResult`），使旧代码无缝过渡。
    新代码请直接使用 `from src.agent_runtime import agent_runtime, AgentTask`。
    """

    def __init__(
        self,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        workdir: Optional[str] = None,
    ) -> None:
        self.timeout = timeout_seconds
        self.workdir = workdir

    # ---- 静态工具方法（委托到 cli_shared）----

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return estimate_tokens(text)

    @staticmethod
    def dynamic_timeout(estimated_tokens: int) -> int:
        return dynamic_timeout(estimated_tokens)

    @staticmethod
    def extract_json(raw_output: str) -> JSONExtractResult:
        return extract_json(raw_output)

    @staticmethod
    def _repair_json_text(text: str) -> str:
        return repair_json_text(text)

    # ---- 过渡兼容：直接按 role 调 runtime ----

    async def claude(
        self,
        prompt: str,
        workdir: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
        timeout: Optional[int] = None,
    ) -> CLICallResult:
        """[过渡] 走 requirement.analyzer role。"""
        return await self._route(
            role="requirement.analyzer",
            prompt=prompt,
            workdir=workdir,
            timeout=timeout,
        )

    async def codex(
        self,
        prompt: str,
        workdir: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
        timeout: Optional[int] = None,
    ) -> CLICallResult:
        """[过渡] 走 requirement.reviewer role。"""
        return await self._route(
            role="requirement.reviewer",
            prompt=prompt,
            workdir=workdir,
            timeout=timeout,
        )

    async def _route(
        self,
        role: str,
        prompt: str,
        workdir: Optional[str],
        timeout: Optional[int],
    ) -> CLICallResult:
        result = await agent_runtime.run(
            AgentTask(
                role=role,
                prompt=prompt,
                workdir=workdir or self.workdir,
                timeout=timeout if timeout is not None else self.timeout,
                stage_name="legacy_agent_cli",
            )
        )
        return CLICallResult(
            success=result.success,
            raw_output=result.raw_output,
            error=result.error,
            exit_code=result.exit_code,
            latency_ms=result.latency_ms,
            meta={
                "backend": result.backend,
                "role": result.role,
                "fallback_from": result.fallback_from or "",
                **(result.meta or {}),
            },
        )


__all__ = [
    "AgentCLI",
    "CLICallResult",
    "JSONExtractResult",
    "estimate_tokens",
    "dynamic_timeout",
    "extract_json",
    "repair_json_text",
    "run_cli_command",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_CODE_BLOCK_TIMEOUT",
    "BINARY_LOOKUP",
]
