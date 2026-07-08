"""Claude Code CLI 后端。"""

from __future__ import annotations

from src.agent_runtime.backends.base import CliBackend


class ClaudeCodeBackend(CliBackend):
    """通过本地 `claude` CLI 执行智能体任务。

    默认命令：`claude -p {prompt}`（prompt 作为命令行参数）。
    可在 config 中覆盖 `command` 字段以支持不同参数（例如 model 指定）。
    """

    DEFAULT_COMMAND = ["claude", "-p", "{prompt}"]

    @property
    def command_template(self) -> list[str]:
        cmd = self.config.get("command") or self.DEFAULT_COMMAND
        return list(cmd)
