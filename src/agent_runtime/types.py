"""AgentRuntime 通用类型。

- `AgentTask`：业务层发起的一次智能体任务。
- `AgentRunResult`：一次调用的归一化结果（无论 CLI 还是 SDK 后端）。
- `AgentBackend`：所有后端实现必须继承的抽象接口。
- `BackendKind`：配置层与运行时之间用于识别后端类别的枚举。

Role 命名规范采用两段式 `<domain>.<role>`（例如 `requirement.analyzer`、
`testcase.generator`），业务代码只需引用字符串，无需引入枚举。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class BackendKind(str, Enum):
    """智能体后端类别，与配置 `backends.<name>.type` 对应。"""

    CLI = "cli"
    SDK = "sdk"


@dataclass
class AgentTask:
    """业务层发起的一次智能体任务。

    role: 两段式角色字符串，如 `requirement.analyzer` / `testcase.generator`。
    prompt: 完整提示词（system + user 已由业务层拼接完成）。
    workdir: 智能体的工作目录；CLI 后端作为 subprocess.cwd，Cursor local 模式作为 cwd。
    timeout: 单次调用超时秒数；None 时使用 role 配置里的 default_timeout_seconds。
    stage_name: 触发本次调用的业务阶段名（如 `requirement_analysis`），用于日志聚合。
    task_id: 业务侧的任务/流水线 ID，用于日志/回溯（如 analysis_id、pipeline_id）。
    metadata: 附加元数据（不参与调用逻辑，仅透传至日志/审计）。
    force_fallback: 若为 True，跳过 primary，直接从 fallbacks[0] 开始尝试；
                    自愈场景（primary 已被证实失败）使用。
    """

    role: str
    prompt: str
    workdir: Optional[str] = None
    timeout: Optional[int] = None
    stage_name: str = ""
    task_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    force_fallback: bool = False


@dataclass
class AgentRunResult:
    """一次智能体调用的归一化结果。

    role/backend/attempt_index/fallback_from 由 runtime 填充，
    业务层无需关心底层是哪种后端。
    """

    success: bool
    raw_output: str = ""
    error: str = ""
    role: str = ""
    backend: str = ""
    attempt_index: int = 0
    fallback_from: Optional[str] = None
    latency_ms: int = 0
    exit_code: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


class AgentBackend(ABC):
    """所有智能体后端实现的抽象基类。

    子类必须实现 `run(task)`；`is_available()` 默认返回 True，用于启动期健康检查。
    `name` 由 runtime 在实例化时注入，对应配置 backends 段的 key。
    """

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self.name = name
        self.config = config

    @abstractmethod
    async def run(self, task: AgentTask) -> AgentRunResult:
        """执行一次智能体任务。

        实现方职责：
        - 内部处理 subprocess/SDK 细节、超时、异常归一。
        - 返回 `AgentRunResult` 时无需填充 `role`/`backend`/`attempt_index`/`fallback_from`，
          这些字段由 `AgentRuntime` 统一注入。
        """

    def is_available(self) -> bool:
        """启动期健康检查。默认可用；子类可覆盖检查二进制/依赖/API Key。"""
        return True
