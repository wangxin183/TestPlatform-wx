"""AgentRuntime — 项目级统一智能体运行时。

服务于软件测试全流程（需求分析、用例生成、用例评审、执行、缺陷分析等），
向业务层提供以「角色」为中心的智能体调用入口。

Usage:
    from src.agent_runtime import agent_runtime, AgentTask

    result = await agent_runtime.run(AgentTask(
        role="requirement.analyzer",
        prompt="...",
        workdir="/tmp/analysis-001",
        stage_name="requirement_analysis",
        task_id="RA-0001",
    ))
"""

from __future__ import annotations

from src.agent_runtime.runtime import agent_runtime, AgentRuntime
from src.agent_runtime.types import (
    AgentTask,
    AgentRunResult,
    AgentBackend,
    BackendKind,
)

__all__ = [
    "agent_runtime",
    "AgentRuntime",
    "AgentTask",
    "AgentRunResult",
    "AgentBackend",
    "BackendKind",
]
