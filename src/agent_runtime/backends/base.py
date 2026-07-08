"""CLI 类后端的通用基类：处理 command 模板、stdin_prompt 分流、可用性检查。

新增 CLI 智能体（例如 gemini-cli）只需继承 `CliBackend` 并声明默认配置，无需重写。
"""

from __future__ import annotations

import shutil
from typing import Any

from src.agent_runtime.cli_shared import (
    DEFAULT_TIMEOUT_SECONDS,
    dynamic_timeout,
    estimate_tokens,
    run_cli_command,
)
from src.agent_runtime.types import AgentBackend, AgentRunResult, AgentTask
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class CliBackend(AgentBackend):
    """基于本地 CLI 子进程执行的智能体后端。

    支持的配置字段：
    - command: list[str]  命令模板；含 `{prompt}` 占位符时会被替换为实际 prompt。
    - stdin_prompt: bool  为 True 时 prompt 通过 stdin 传入而非命令行参数（如 codex）。
    - default_timeout_seconds: int  默认超时；task.timeout 优先。
    - env: dict[str, str]  预留（当前 run_cli_command 未启用）。
    """

    @property
    def command_template(self) -> list[str]:
        cmd = self.config.get("command")
        if not isinstance(cmd, list) or not cmd:
            raise ValueError(
                f"CliBackend '{self.name}' 缺少 `command` 配置或格式不正确"
            )
        return list(cmd)

    @property
    def stdin_prompt(self) -> bool:
        return bool(self.config.get("stdin_prompt", False))

    @property
    def default_timeout(self) -> int:
        return int(self.config.get("default_timeout_seconds", DEFAULT_TIMEOUT_SECONDS))

    def is_available(self) -> bool:
        binary = self.command_template[0]
        found = shutil.which(binary) is not None
        if not found:
            logger.warning(
                "cli_backend_binary_missing",
                backend=self.name,
                binary=binary,
            )
        return found

    def _build_cmd(self, prompt: str) -> tuple[list[str], str | None]:
        """按配置渲染命令行 argv，并决定是否通过 stdin 传入 prompt。

        返回 `(cmd, stdin_input)`：
        - stdin_prompt=True 时 `{prompt}` 占位符会被剔除；prompt 走 stdin。
        - stdin_prompt=False 时 `{prompt}` 占位符被替换为实际 prompt。
        """
        rendered: list[str] = []
        for arg in self.command_template:
            if arg == "{prompt}":
                if self.stdin_prompt:
                    continue
                rendered.append(prompt)
            else:
                rendered.append(arg)
        stdin_input = prompt if self.stdin_prompt else None
        return rendered, stdin_input

    async def run(self, task: AgentTask) -> AgentRunResult:
        timeout = task.timeout or self.default_timeout
        if timeout <= 0:
            timeout = dynamic_timeout(estimate_tokens(task.prompt))

        cmd, stdin_input = self._build_cmd(task.prompt)

        cli_result = await run_cli_command(
            cmd=cmd,
            workdir=task.workdir,
            timeout=timeout,
            agent_name=self.name,
            stdin_input=stdin_input,
        )

        return AgentRunResult(
            success=cli_result.success,
            raw_output=cli_result.raw_output,
            error=cli_result.error,
            latency_ms=cli_result.latency_ms,
            exit_code=cli_result.exit_code,
            meta={
                "stderr": cli_result.meta.get("stderr", ""),
                "cmd": " ".join(cmd),
                "stdin_used": stdin_input is not None,
            },
        )
