"""AgentRuntime 具体后端（CLI + Cursor）行为测试。"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from src.agent_runtime.backends.claude_code import ClaudeCodeBackend
from src.agent_runtime.backends.codex import CodexBackend
from src.agent_runtime.backends.cursor import CursorBackend
from src.agent_runtime.cli_shared import CLICallResult
from src.agent_runtime.types import AgentTask


@pytest.mark.asyncio
async def test_claude_code_backend_renders_prompt_in_command(monkeypatch):
    """{prompt} 占位符会被替换到命令行；stdin_prompt=False 时不用 stdin。"""
    captured: dict[str, Any] = {}

    async def fake_run(cmd, workdir, timeout, agent_name, stdin_input=None):
        captured["cmd"] = list(cmd)
        captured["stdin"] = stdin_input
        captured["workdir"] = workdir
        captured["timeout"] = timeout
        captured["agent_name"] = agent_name
        return CLICallResult(success=True, raw_output="ok", exit_code=0)

    monkeypatch.setattr("src.agent_runtime.backends.base.run_cli_command", fake_run)

    backend = ClaudeCodeBackend(name="claude_code", config={})
    task = AgentTask(role="requirement.analyzer", prompt="hello", workdir="/tmp/x", timeout=42)
    result = await backend.run(task)

    assert result.success is True
    assert result.raw_output == "ok"
    assert captured["cmd"] == ["claude", "-p", "hello"]
    assert captured["stdin"] is None
    assert captured["workdir"] == "/tmp/x"
    assert captured["timeout"] == 42


@pytest.mark.asyncio
async def test_codex_backend_uses_stdin_prompt_and_strips_placeholder(monkeypatch):
    """CodexBackend 默认 stdin_prompt=True；命令中不包含 prompt。"""
    captured: dict[str, Any] = {}

    async def fake_run(cmd, workdir, timeout, agent_name, stdin_input=None):
        captured["cmd"] = list(cmd)
        captured["stdin"] = stdin_input
        return CLICallResult(success=True, raw_output="ok", exit_code=0)

    monkeypatch.setattr("src.agent_runtime.backends.base.run_cli_command", fake_run)

    backend = CodexBackend(name="codex", config={})
    result = await backend.run(AgentTask(role="requirement.reviewer", prompt="hi", timeout=30))

    assert result.success is True
    assert captured["cmd"] == ["codex", "exec", "--skip-git-repo-check"]
    assert captured["stdin"] == "hi"


@pytest.mark.asyncio
async def test_cli_backend_maps_failure_result(monkeypatch):
    """底层失败会被封装为 AgentRunResult(success=False, error=...)。"""

    async def fake_run(cmd, workdir, timeout, agent_name, stdin_input=None):
        return CLICallResult(
            success=False,
            raw_output="",
            error="退出码 2: stderr...",
            exit_code=2,
            latency_ms=123,
            meta={"stderr": "boom"},
        )

    monkeypatch.setattr("src.agent_runtime.backends.base.run_cli_command", fake_run)

    backend = ClaudeCodeBackend(name="claude_code", config={})
    result = await backend.run(AgentTask(role="requirement.analyzer", prompt="x", timeout=10))

    assert result.success is False
    assert result.exit_code == 2
    assert "退出码" in result.error
    assert result.latency_ms == 123


def test_cursor_backend_unavailable_when_sdk_missing():
    """cursor-sdk 未安装或 API Key 缺失时 is_available()=False。"""
    backend = CursorBackend(name="cursor", config={"mode": "local", "api_key_env": "NOPE_KEY"})
    assert backend.is_available() is False


@pytest.mark.asyncio
async def test_cursor_backend_returns_error_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("NOPE_KEY", raising=False)
    backend = CursorBackend(name="cursor", config={"mode": "local", "api_key_env": "NOPE_KEY"})

    result = await backend.run(AgentTask(role="requirement.analyzer", prompt="x"))

    # 两种可能：SDK 未装 → "cursor-sdk 未安装"；SDK 有但 key 缺 → "环境变量 NOPE_KEY 未设置"
    assert result.success is False
    assert result.error


@pytest.mark.asyncio
async def test_cursor_backend_rejects_non_local_mode(monkeypatch):
    """本次仅实现 local 模式；cloud/其他模式必须失败（SDK 缺失或 mode 拒绝均可）。"""
    monkeypatch.setenv("CURSOR_TEST_KEY", "crsr_test")
    backend = CursorBackend(
        name="cursor",
        config={"mode": "cloud", "api_key_env": "CURSOR_TEST_KEY"},
    )
    result = await backend.run(AgentTask(role="requirement.analyzer", prompt="x"))
    assert result.success is False
    # 有 SDK → 检查是拒绝 non-local；无 SDK → 检查 SDK 缺失提示
    assert ("local" in result.error) or ("cursor-sdk" in result.error)
