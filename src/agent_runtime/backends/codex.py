"""Codex CLI 后端。"""

from __future__ import annotations

from src.agent_runtime.backends.base import CliBackend


class CodexBackend(CliBackend):
    """通过本地 `codex exec` 执行智能体任务。

    Codex 通过 stdin 管道接收 prompt，因此 `stdin_prompt` 默认 True。
    默认命令：`codex exec --skip-git-repo-check`。
    """

    DEFAULT_COMMAND = ["codex", "exec", "--skip-git-repo-check"]

    @property
    def command_template(self) -> list[str]:
        cmd = self.config.get("command") or self.DEFAULT_COMMAND
        return list(cmd)

    @property
    def stdin_prompt(self) -> bool:
        return bool(self.config.get("stdin_prompt", True))
