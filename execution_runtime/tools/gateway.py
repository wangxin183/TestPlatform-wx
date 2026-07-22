"""受控工具网关：确定性执行器与 Agent 共用同一 Appium 实现。"""

from __future__ import annotations

from typing import Any

from execution_runtime.config import RuntimeConfig
from execution_runtime.dsl.models import Locator, Step
from execution_runtime.engine.executor import StepExecutor
from execution_runtime.tools.action_catalog import ACTION_CATALOG
from execution_runtime.tools.observation import PageObserver
from execution_runtime.tools.recovery import recover_page


class ToolGatewayError(RuntimeError):
    pass


class ToolGateway:
    def __init__(self, driver, cfg: RuntimeConfig) -> None:
        self.driver = driver
        self.cfg = cfg
        self.executor = StepExecutor(driver, cfg)
        self.observer = PageObserver(driver)

    def call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict:
        args = dict(arguments or {})
        for key in ("locator", "until"):
            args[key] = self._normalize_locator(args.get(key))
        spec = ACTION_CATALOG.get(tool_name)
        if spec is None:
            raise ToolGatewayError(f"工具不在白名单: {tool_name}")
        self._validate_arguments(tool_name, spec, args)

        if tool_name == "observe_page":
            return {"ok": True, "data": self.observer.observe().as_dict()}
        if tool_name == "get_current_activity":
            return {
                "ok": True,
                "data": {
                    "package": str(getattr(self.driver, "current_package", "") or ""),
                    "activity": str(getattr(self.driver, "current_activity", "") or ""),
                },
            }
        if tool_name == "inspect_elements":
            query = str(args["query"])
            observation = self.observer.observe()
            matches = [
                element
                for element in observation.elements
                if query in element.text
                or query in element.resource_id
                or query in element.accessibility_id
            ]
            return {
                "ok": True,
                "data": {
                    "count": len(matches),
                    "elements": [
                        {
                            "text": element.text,
                            "resource_id": element.resource_id,
                            "accessibility_id": element.accessibility_id,
                            "clickable": element.clickable,
                            "enabled": element.enabled,
                            "displayed": element.displayed,
                        }
                        for element in matches[:50]
                    ],
                },
            }
        if tool_name == "recover_page":
            return self._call_recover_page(args)

        step_data = {
            "action": tool_name,
            "description": str(args.pop("description", "") or ""),
            "expected": str(args.pop("expected", "") or ""),
            **args,
        }
        try:
            step = Step.model_validate(step_data)
            if tool_name in {"tap", "input", "clear", "assert_visible"}:
                self._require_unique_target(step.locator)
            matched_by = self.executor.execute(step)
        except Exception as exc:
            raise ToolGatewayError(f"{tool_name} 执行失败: {exc}") from exc
        return {
            "ok": True,
            "data": {
                "tool": tool_name,
                "matched_by": matched_by,
                "observation": self.observer.observe().as_dict(),
            },
        }

    def _call_recover_page(self, args: dict[str, Any]) -> dict:
        until = args.get("until")
        max_backs = int(args.get("max_backs") if args.get("max_backs") is not None else 3)
        probe_timeout = int(args.get("timeout") if args.get("timeout") is not None else 2)
        relaunch_arg = args.get("relaunch")
        relaunch: bool | None
        if relaunch_arg is None or relaunch_arg == "":
            relaunch = None
        elif isinstance(relaunch_arg, bool):
            relaunch = relaunch_arg
        else:
            relaunch = str(relaunch_arg).strip().lower() in {"1", "true", "yes", "y"}

        def _exists(locator: dict[str, str], timeout: int) -> bool:
            try:
                return self.executor._exists(Locator.model_validate(locator), timeout)
            except Exception:
                return False

        def _execute(action: str, **kwargs: Any) -> Any:
            payload = {"action": action, "description": "recover_page", **kwargs}
            return self.executor.execute(Step.model_validate(payload))

        result = recover_page(
            exists=_exists,
            execute=_execute,
            until=until if isinstance(until, dict) else None,
            max_backs=max_backs,
            relaunch=relaunch,
            probe_timeout=probe_timeout,
        )
        if not result.ok:
            raise ToolGatewayError(result.message or "recover_page 失败")
        return {
            "ok": True,
            "data": {
                "tool": "recover_page",
                "via": result.via,
                "backs": result.backs,
                "relaunched": result.relaunched,
                "message": result.message,
                "warnings": result.warnings,
                "observation": self.observer.observe().as_dict(),
            },
        }

    @staticmethod
    def _normalize_locator(locator: Any) -> Any:
        if isinstance(locator, str) and ":" in locator:
            prefix, value = locator.split(":", 1)
            locator_type = {
                "resource-id": "id",
                "resource_id": "id",
                "accessibility-id": "accessibility_id",
                "content-desc": "accessibility_id",
            }.get(prefix, prefix)
            if locator_type in {"id", "text", "name", "accessibility_id", "xpath"}:
                return {"type": locator_type, "value": value}
        return locator

    def _require_unique_target(self, locator: Locator | None) -> None:
        if locator is None:
            raise ToolGatewayError("Agent 写操作必须提供 locator")
        elements, _ = self.executor.find_all(locator)
        actionable = [
            element
            for element in elements
            if _element_flag(element, "is_displayed", True)
            and _element_flag(element, "is_enabled", True)
        ]
        if len(actionable) != 1:
            raise ToolGatewayError(
                f"Agent 目标必须唯一、可见、启用，实际候选数: {len(actionable)}"
            )

    @staticmethod
    def _validate_arguments(
        tool_name: str,
        spec: dict[str, Any],
        args: dict[str, Any],
    ) -> None:
        for name, param in spec.get("parameters", {}).items():
            if param.get("required") and (
                name not in args or args[name] is None or args[name] == ""
            ):
                raise ToolGatewayError(f"{tool_name} 缺少必填参数: {name}")


def _element_flag(element: Any, method: str, default: bool) -> bool:
    try:
        value = getattr(element, method)()
        return bool(value)
    except Exception:
        return default
