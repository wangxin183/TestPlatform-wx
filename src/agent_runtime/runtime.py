"""AgentRuntime — 统一路由入口。

负责：
1. 从 `settings.agent_runtime` 装载 backends 与 roles 配置。
2. 启动期健康检查：任一 role 的 primary + fallbacks 全部 unavailable 时 fail-fast
   （可通过 `agent_runtime.strict_startup_check: false` 关闭）。
3. run(task)：按 primary → fallbacks 顺序执行；success=True 立即返回；
   全部失败返回聚合错误。
4. 结构化日志字段全流程统一（role / backend / attempt_index / fallback_from /
   stage_name / task_id / latency_ms / exit_code）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from src.agent_runtime.backends.base import CliBackend
from src.agent_runtime.backends.claude_code import ClaudeCodeBackend
from src.agent_runtime.backends.codex import CodexBackend
from src.agent_runtime.backends.cursor import CursorBackend
from src.agent_runtime.types import AgentBackend, AgentRunResult, AgentTask
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


BACKEND_CLASSES: dict[str, type[AgentBackend]] = {
    "claude_code": ClaudeCodeBackend,
    "codex": CodexBackend,
    "cursor": CursorBackend,
}


class AgentRuntime:
    """统一智能体运行时。

    实例化建议通过 `AgentRuntime.from_settings(settings)` 或模块级
    `agent_runtime` 单例，避免各处重复解析配置。
    """

    def __init__(
        self,
        backends: dict[str, AgentBackend],
        roles: dict[str, dict[str, Any]],
        strict_startup_check: bool = True,
    ) -> None:
        self._backends = backends
        self._roles = roles
        self._strict = strict_startup_check
        self._health_check()

    @classmethod
    def from_settings(cls, settings_obj: Any) -> "AgentRuntime":
        """从聚合 Settings 对象装载运行时。

        期望 settings_obj 有 `agent_runtime` 属性，字段包括 `enabled`、
        `backends`、`roles`、`strict_startup_check`。若整个 `agent_runtime`
        段缺失或 enabled=False，返回空 runtime（`run()` 会抛错）。
        """
        cfg = getattr(settings_obj, "agent_runtime", None)
        if cfg is None or not getattr(cfg, "enabled", True):
            logger.info("agent_runtime_disabled", reason="config_missing_or_disabled")
            return cls(backends={}, roles={}, strict_startup_check=False)

        backends_cfg: dict[str, dict[str, Any]] = getattr(cfg, "backends", {}) or {}
        roles_cfg: dict[str, dict[str, Any]] = getattr(cfg, "roles", {}) or {}
        strict = bool(getattr(cfg, "strict_startup_check", True))

        backends: dict[str, AgentBackend] = {}
        for name, bcfg in backends_cfg.items():
            backend = _instantiate_backend(name, bcfg or {})
            if backend is not None:
                backends[name] = backend

        return cls(backends=backends, roles=roles_cfg, strict_startup_check=strict)

    def _health_check(self) -> None:
        """启动期健康检查。发现无可用 backend 的 role 时按 strict 决定行为。"""
        if not self._roles:
            logger.warning("agent_runtime_no_roles_configured")
            return

        report: dict[str, dict[str, Any]] = {}
        for role_name, role_cfg in self._roles.items():
            chain = self._resolve_chain(role_cfg, force_fallback=False)
            availability = [(name, self._backends.get(name)) for name in chain]
            available_names = [
                name
                for name, backend in availability
                if backend is not None and backend.is_available()
            ]
            report[role_name] = {
                "primary": (role_cfg or {}).get("primary", ""),
                "fallbacks": list((role_cfg or {}).get("fallbacks", []) or []),
                "chain": chain,
                "available": available_names,
            }
            if not available_names:
                msg = (
                    f"agent_runtime role '{role_name}' 无任何可用 backend "
                    f"（chain={chain}）"
                )
                if self._strict:
                    logger.error("agent_runtime_role_unavailable_fail_fast", role=role_name)
                    raise RuntimeError(msg)
                logger.warning("agent_runtime_role_unavailable", role=role_name, chain=chain)

        logger.info(
            "agent_runtime_ready",
            backends_registered=list(self._backends.keys()),
            roles=report,
        )

    def list_available_backends(self) -> list[str]:
        return [name for name, b in self._backends.items() if b.is_available()]

    def get_role_chain(self, role: str, force_fallback: bool = False) -> list[str]:
        role_cfg = self._roles.get(role)
        if role_cfg is None:
            return []
        return self._resolve_chain(role_cfg, force_fallback=force_fallback)

    @staticmethod
    def _resolve_chain(role_cfg: dict[str, Any], force_fallback: bool) -> list[str]:
        chain: list[str] = []
        primary = (role_cfg or {}).get("primary")
        if primary and not force_fallback:
            chain.append(primary)
        for fb in (role_cfg or {}).get("fallbacks", []) or []:
            if fb and fb not in chain:
                chain.append(fb)
        return chain

    async def run(self, task: AgentTask) -> AgentRunResult:
        """执行一次智能体任务，按 primary → fallbacks 顺序尝试。"""
        role_cfg = self._roles.get(task.role)
        if role_cfg is None:
            error = f"未配置 role '{task.role}'（settings.agent_runtime.roles 中未定义）"
            logger.error(
                "agent_runtime_role_not_found",
                role=task.role,
                stage_name=task.stage_name,
                task_id=task.task_id,
            )
            return AgentRunResult(success=False, role=task.role, error=error)

        chain = self._resolve_chain(role_cfg, force_fallback=task.force_fallback)
        if not chain:
            error = f"role '{task.role}' 未定义 primary 或 fallbacks"
            logger.error(
                "agent_runtime_empty_chain",
                role=task.role,
                stage_name=task.stage_name,
                task_id=task.task_id,
            )
            return AgentRunResult(success=False, role=task.role, error=error)

        effective_timeout = task.timeout or int((role_cfg or {}).get("default_timeout_seconds", 0))
        errors: list[str] = []
        previous: Optional[str] = None
        last_attempt_backend: Optional[str] = None
        last_attempt_index: int = 0
        last_previous: Optional[str] = None

        for idx, backend_name in enumerate(chain):
            backend = self._backends.get(backend_name)
            if backend is None:
                errors.append(f"{backend_name}: 未注册")
                logger.warning(
                    "agent_runtime_backend_not_registered",
                    role=task.role,
                    backend=backend_name,
                    stage_name=task.stage_name,
                )
                previous = backend_name
                continue
            if not backend.is_available():
                errors.append(f"{backend_name}: 不可用")
                logger.warning(
                    "agent_runtime_backend_unavailable",
                    role=task.role,
                    backend=backend_name,
                    stage_name=task.stage_name,
                )
                previous = backend_name
                continue

            attempt_task = AgentTask(
                role=task.role,
                prompt=task.prompt,
                workdir=task.workdir,
                timeout=effective_timeout or task.timeout,
                stage_name=task.stage_name,
                task_id=task.task_id,
                metadata=task.metadata,
                force_fallback=task.force_fallback,
            )

            logger.info(
                "agent_runtime_attempt_start",
                role=task.role,
                backend=backend_name,
                attempt_index=idx,
                fallback_from=previous,
                stage_name=task.stage_name,
                task_id=task.task_id,
                prompt_len=len(task.prompt),
                timeout=attempt_task.timeout,
            )

            result = await backend.run(attempt_task)
            result.role = task.role
            result.backend = backend_name
            result.attempt_index = idx
            result.fallback_from = previous

            logger.info(
                "agent_runtime_attempt_done",
                role=task.role,
                backend=backend_name,
                attempt_index=idx,
                success=result.success,
                latency_ms=result.latency_ms,
                exit_code=result.exit_code,
                output_len=len(result.raw_output or ""),
                error=(result.error or "")[:200],
                stage_name=task.stage_name,
                task_id=task.task_id,
            )

            if result.success:
                return result

            errors.append(f"{backend_name}: {result.error or 'unknown'}")
            last_attempt_backend = backend_name
            last_attempt_index = idx
            last_previous = previous
            previous = backend_name

        aggregated = "; ".join(errors) or "所有 backend 均失败"
        logger.error(
            "agent_runtime_all_backends_failed",
            role=task.role,
            chain=chain,
            errors=errors,
            stage_name=task.stage_name,
            task_id=task.task_id,
        )
        return AgentRunResult(
            success=False,
            role=task.role,
            backend=last_attempt_backend or (chain[-1] if chain else ""),
            attempt_index=last_attempt_index,
            fallback_from=last_previous,
            error=aggregated,
        )


def _instantiate_backend(name: str, config: dict[str, Any]) -> Optional[AgentBackend]:
    cls = BACKEND_CLASSES.get(name)
    if cls is None:
        btype = (config.get("type") or "").lower()
        if btype == "cli":
            cls = CliBackend
        else:
            logger.warning(
                "agent_runtime_unknown_backend",
                backend=name,
                type=btype,
                hint="未在 BACKEND_CLASSES 注册且 type != cli",
            )
            return None
    try:
        return cls(name=name, config=config)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "agent_runtime_backend_init_failed",
            backend=name,
            error=str(exc)[:300],
            exc_type=type(exc).__name__,
        )
        return None


class _RuntimeProxy:
    """全局 lazy 单例代理：首次调用时才根据当前 settings 初始化 AgentRuntime。

    这样 import `agent_runtime` 不会触发 Settings 装载失败，也方便测试时
    通过 `agent_runtime._reset()` 重置。
    """

    def __init__(self) -> None:
        self._instance: Optional[AgentRuntime] = None
        self._lock = asyncio.Lock()

    def _get_sync(self) -> AgentRuntime:
        if self._instance is not None:
            return self._instance
        from src.core.config import settings  # 延迟导入，避免循环
        self._instance = AgentRuntime.from_settings(settings)
        return self._instance

    def _reset(self) -> None:
        """测试专用：清空缓存的 runtime 实例。"""
        self._instance = None

    def _set_instance(self, instance: AgentRuntime) -> None:
        """测试专用：直接注入 runtime 实例。"""
        self._instance = instance

    def list_available_backends(self) -> list[str]:
        return self._get_sync().list_available_backends()

    def get_role_chain(self, role: str, force_fallback: bool = False) -> list[str]:
        return self._get_sync().get_role_chain(role, force_fallback=force_fallback)

    async def run(self, task: AgentTask) -> AgentRunResult:
        return await self._get_sync().run(task)


agent_runtime = _RuntimeProxy()
