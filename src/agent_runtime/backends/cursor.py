"""Cursor SDK 后端（local 模式）。

依赖 `cursor-sdk` PyPI 包及环境变量 `CURSOR_API_KEY`。SDK 未安装或 API Key
未配置时，`is_available()` 返回 False，runtime 将自动跳过此后端并 fallback。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from src.agent_runtime.cli_shared import DEFAULT_TIMEOUT_SECONDS, _loop_time
from src.agent_runtime.types import AgentBackend, AgentRunResult, AgentTask
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


try:
    from cursor_sdk import AsyncClient, LocalAgentOptions  # type: ignore
    _CURSOR_SDK_AVAILABLE = True
    _CURSOR_SDK_IMPORT_ERROR = ""
except Exception as _exc:  # noqa: BLE001 — SDK 未装或版本不兼容
    AsyncClient = None  # type: ignore
    LocalAgentOptions = None  # type: ignore
    _CURSOR_SDK_AVAILABLE = False
    _CURSOR_SDK_IMPORT_ERROR = str(_exc)


DEFAULT_MODEL = "composer-2.5"


class CursorBackend(AgentBackend):
    """通过 Cursor Python SDK 在 local 模式下执行智能体任务。

    支持的配置字段：
    - model: str  Cursor 模型 ID（默认 `composer-2.5`）。
    - mode: str   `local` | `cloud`；本次仅实现 local。
    - api_key_env: str  存储 API Key 的环境变量名（默认 `CURSOR_API_KEY`）。
    - default_timeout_seconds: int  单次调用超时；task.timeout 优先。
    - workspace: str  可选，`AsyncClient.launch_bridge(workspace=...)` 参数；
                      默认使用 task.workdir，若为空则用 Path.cwd()。
    """

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        self._warned: set[str] = set()

    def _warn_once(self, kind: str, **extra: Any) -> None:
        if kind in self._warned:
            return
        self._warned.add(kind)
        logger.warning(f"cursor_backend_{kind}", backend=self.name, **extra)

    @property
    def model(self) -> str:
        return str(self.config.get("model") or DEFAULT_MODEL)

    @property
    def mode(self) -> str:
        return str(self.config.get("mode") or "local").lower()

    @property
    def api_key_env(self) -> str:
        return str(self.config.get("api_key_env") or "CURSOR_API_KEY")

    @property
    def default_timeout(self) -> int:
        return int(self.config.get("default_timeout_seconds", DEFAULT_TIMEOUT_SECONDS))

    def _api_key(self) -> str:
        return os.environ.get(self.api_key_env, "").strip()

    def is_available(self) -> bool:
        if not _CURSOR_SDK_AVAILABLE:
            self._warn_once(
                "sdk_missing",
                error=_CURSOR_SDK_IMPORT_ERROR,
                hint="pip install cursor-sdk",
            )
            return False
        if not self._api_key():
            self._warn_once("api_key_missing", env=self.api_key_env)
            return False
        if self.mode != "local":
            self._warn_once(
                "mode_unsupported",
                mode=self.mode,
                note="本次仅实现 local 模式，cloud 模式暂不支持",
            )
            return False
        return True

    async def run(self, task: AgentTask) -> AgentRunResult:
        start = _loop_time()

        if not _CURSOR_SDK_AVAILABLE:
            return AgentRunResult(
                success=False,
                error=f"cursor-sdk 未安装：{_CURSOR_SDK_IMPORT_ERROR}",
            )
        api_key = self._api_key()
        if not api_key:
            return AgentRunResult(
                success=False,
                error=f"环境变量 {self.api_key_env} 未设置",
            )
        if self.mode != "local":
            return AgentRunResult(
                success=False,
                error=f"Cursor 后端仅支持 local 模式，当前 mode={self.mode}",
            )

        workdir = task.workdir or str(Path.cwd())
        workspace = str(self.config.get("workspace") or workdir)
        timeout = task.timeout or self.default_timeout
        if timeout <= 0:
            timeout = self.default_timeout

        logger.info(
            "cursor_backend_start",
            backend=self.name,
            model=self.model,
            workdir=workdir,
            workspace=workspace,
            timeout=timeout,
            prompt_len=len(task.prompt),
        )

        async def _do_call() -> str:
            async with await AsyncClient.launch_bridge(workspace=workspace) as client:
                async with await client.agents.create(
                    model=self.model,
                    api_key=api_key,
                    local=LocalAgentOptions(cwd=workdir),
                ) as agent:
                    run = await agent.send(task.prompt)
                    return await run.text()

        try:
            text = await asyncio.wait_for(_do_call(), timeout=timeout)
            latency_ms = int((_loop_time() - start) * 1000)
            logger.info(
                "cursor_backend_done",
                backend=self.name,
                latency_ms=latency_ms,
                output_len=len(text or ""),
            )
            return AgentRunResult(
                success=True,
                raw_output=text or "",
                latency_ms=latency_ms,
                meta={"model": self.model, "workdir": workdir},
            )
        except asyncio.TimeoutError:
            latency_ms = int((_loop_time() - start) * 1000)
            logger.error("cursor_backend_timeout", backend=self.name, timeout=timeout)
            return AgentRunResult(
                success=False,
                error=f"Cursor 调用超时（{timeout}秒）",
                latency_ms=latency_ms,
            )
        except Exception as exc:  # noqa: BLE001 — SDK 各类异常统一归一
            latency_ms = int((_loop_time() - start) * 1000)
            logger.error(
                "cursor_backend_error",
                backend=self.name,
                error=str(exc)[:500],
                exc_type=type(exc).__name__,
            )
            return AgentRunResult(
                success=False,
                error=f"Cursor 调用异常: {exc}",
                latency_ms=latency_ms,
                meta={"exc_type": type(exc).__name__},
            )
