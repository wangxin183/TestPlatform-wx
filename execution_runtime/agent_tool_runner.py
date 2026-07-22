"""无确定性 DSL 或定位漂移时的受控 Agent 工具循环。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.agent_runtime import AgentTask, agent_runtime
from src.agent_runtime.cli_shared import extract_json
from src.services.testcase_module_catalog import module_catalog

from execution_runtime.navigation.path_cache import NavigationPathCache
from execution_runtime.tools.action_catalog import (
    ACTION_CATALOG,
    HIGH_RISK_KEYWORDS,
    tool_schemas_for_prompt,
)
from execution_runtime.tools.gateway import ToolGateway, ToolGatewayError
from execution_runtime.tools.observation import PageStateMatcher, StepGuard

ROLE_NAVIGATOR = "execution.navigator"
DEFAULT_NAVIGATION_CACHE = (
    Path(__file__).resolve().parent.parent
    / "storage"
    / "execution_navigation_paths.json"
)


class AgentToolRunError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentStepResult:
    ok: bool
    call_count: int
    last_tool: str = ""
    message: str = ""


DecisionFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class AgentToolRunner:
    def __init__(
        self,
        gateway: ToolGateway,
        *,
        run_dir: Path,
        decide: DecisionFn | None = None,
        max_calls: int = 6,
        path_cache: NavigationPathCache | None = None,
    ) -> None:
        self.gateway = gateway
        self.run_dir = run_dir
        self.decide = decide or self._agent_decide
        self.max_calls = max(1, max_calls)
        self.guard = StepGuard()
        self.path_cache = path_cache or NavigationPathCache(DEFAULT_NAVIGATION_CACHE)

    async def navigate_to_module(
        self,
        module: str,
        *,
        case_id: str,
    ) -> AgentStepResult:
        """确认目标页，优先回放缓存路径，失败后用受控 Agent 探索。"""
        definition = module_catalog.get(module)
        if definition is None:
            raise AgentToolRunError(f"未知模块，无法导航: {module}")
        if not definition.page_states:
            raise AgentToolRunError(f"模块 {module} 未配置可验证的 page_state")
        state = definition.page_states[0]
        observation = self.gateway.observer.observe()
        if PageStateMatcher().match(state, observation).matched:
            return AgentStepResult(ok=True, call_count=0, message="已在模块页面")

        capabilities = getattr(self.gateway.driver, "capabilities", {}) or {}
        cache_key = {
            "app_id": self.gateway.cfg.target_app.bundle_id,
            "app_version": str(
                capabilities.get("appVersion")
                or capabilities.get("appium:appVersion")
                or ""
            ),
            "module": module,
            "start_package": observation.package,
            "start_activity": observation.activity,
        }
        cached_actions = self.path_cache.load(**cache_key)
        if cached_actions:
            cached_result = self._replay_navigation_path(
                cached_actions,
                state=state,
                module=module,
                case_id=case_id,
            )
            if cached_result is not None:
                return cached_result
            self.path_cache.invalidate(**cache_key)
            observation = self.gateway.observer.observe()

        feedback: dict[str, Any] = {}
        safe_actions: list[dict[str, Any]] = []
        seen_actions: set[tuple[str, str, str]] = set()
        for call_index in range(1, self.max_calls + 1):
            payload = {
                "case_id": case_id,
                "module": module,
                "phase": "module_navigation",
                "entry_nl": definition.entry_nl,
                "target_state": {
                    "id": state.id,
                    "package": state.package,
                    "activity": state.activity,
                    "required_all": state.required_all,
                    "required_any": state.required_any,
                    "forbidden_any": state.forbidden_any,
                },
                "page": observation.as_agent_dict(),
                "last_feedback": feedback,
                "tools": tool_schemas_for_prompt(),
                "rules": [
                    "一次只返回一个工具调用",
                    "目标是进入 target_state",
                    "目标不唯一时先 inspect_elements",
                    "禁止支付、购买、删除、发布操作",
                ],
            }
            decision = await self.decide(payload)
            tool = str(decision.get("tool") or "")
            arguments = decision.get("arguments") or {}
            if tool not in ACTION_CATALOG:
                raise AgentToolRunError(f"工具不在白名单: {tool}")
            if not isinstance(arguments, dict):
                raise AgentToolRunError("工具 arguments 必须是对象")
            signature = (
                observation.source_hash,
                tool,
                json.dumps(arguments, ensure_ascii=False, sort_keys=True),
            )
            if signature in seen_actions:
                raise AgentToolRunError("模块导航检测到重复动作循环，暂停执行")
            seen_actions.add(signature)
            if any(
                keyword in json.dumps(arguments, ensure_ascii=False)
                for keyword in HIGH_RISK_KEYWORDS
            ) and not ACTION_CATALOG[tool]["read_only"]:
                raise AgentToolRunError("模块导航禁止高风险写操作")
            try:
                self.gateway.call(tool, arguments)
            except ToolGatewayError as exc:
                feedback = {"ok": False, "error": str(exc)}
                continue
            if not ACTION_CATALOG[tool]["read_only"]:
                safe_actions.append({"tool": tool, "arguments": dict(arguments)})
            observation = self.gateway.observer.observe()
            match = PageStateMatcher().match(state, observation)
            self._log(
                "module_navigation_tool",
                case_id=case_id,
                module=module,
                call_index=call_index,
                tool=tool,
                matched=match.matched,
                reason=match.reason,
            )
            if match.forbidden_hits:
                raise AgentToolRunError("模块导航遇到禁止状态，暂停执行")
            if match.matched:
                self.path_cache.save(**cache_key, actions=safe_actions)
                return AgentStepResult(
                    ok=True,
                    call_count=call_index,
                    last_tool=tool,
                    message=f"已进入 {module}",
                )
            feedback = {"ok": True, "state_mismatch": match.reason}
        raise AgentToolRunError(
            f"进入模块 {module} 超过最大工具调用次数 {self.max_calls}"
        )

    def _replay_navigation_path(
        self,
        actions: list[dict[str, Any]],
        *,
        state,
        module: str,
        case_id: str,
    ) -> AgentStepResult | None:
        matcher = PageStateMatcher()
        for index, action in enumerate(actions, start=1):
            tool = str(action.get("tool") or "")
            arguments = action.get("arguments") or {}
            if tool not in ACTION_CATALOG or not isinstance(arguments, dict):
                return None
            if ACTION_CATALOG[tool]["read_only"]:
                return None
            if any(
                keyword in json.dumps(arguments, ensure_ascii=False)
                for keyword in HIGH_RISK_KEYWORDS
            ):
                return None
            try:
                self.gateway.call(tool, arguments)
            except ToolGatewayError:
                return None
            observation = self.gateway.observer.observe()
            match = matcher.match(state, observation)
            self._log(
                "module_navigation_cache_replay",
                case_id=case_id,
                module=module,
                call_index=index,
                tool=tool,
                matched=match.matched,
                reason=match.reason,
            )
            if match.forbidden_hits:
                return None
            if match.matched:
                return AgentStepResult(
                    ok=True,
                    call_count=index,
                    last_tool=tool,
                    message=f"已通过缓存路径进入 {module}",
                )
        return None

    async def run_step(
        self,
        contract: dict[str, Any],
        *,
        module: str,
        case_id: str,
    ) -> AgentStepResult:
        definition = module_catalog.get(module)
        if definition is None:
            raise AgentToolRunError(f"未知模块，禁止 Agent 操作: {module}")
        observation = self.gateway.observer.observe()
        start_state_id = str(contract.get("start_state") or "")
        state = next(
            (
                item
                for item in definition.page_states
                if item.id == start_state_id
            ),
            None,
        )
        transition = str(contract.get("expected_transition") or "")
        destination_id = (
            transition.rsplit("->", 1)[-1].strip()
            if "->" in transition
            else start_state_id
        )
        destination_state = next(
            (item for item in definition.page_states if item.id == destination_id),
            None,
        )
        if state is not None:
            match = PageStateMatcher().match(state, observation)
            if not match.matched:
                self._log(
                    "page_state_mismatch",
                    case_id=case_id,
                    module=module,
                    step=contract.get("step"),
                    reason=match.reason,
                )
                if match.forbidden_hits:
                    raise AgentToolRunError(
                        f"当前页面存在禁止状态，暂停执行: {match.reason}"
                    )
                observation = self._recover_page_state(
                    definition,
                    state,
                    case_id=case_id,
                    step=contract.get("step"),
                )

        feedback: dict[str, Any] = {}
        for call_index in range(1, self.max_calls + 1):
            payload = {
                "case_id": case_id,
                "module": module,
                "current_step": contract,
                "page": observation.as_agent_dict(),
                "last_feedback": feedback,
                "tools": tool_schemas_for_prompt(),
                "rules": [
                    "一次只返回一个工具调用",
                    "不得跨越当前步骤",
                    "目标不唯一时先 inspect_elements",
                    "完成后必须满足 postconditions",
                ],
            }
            decision = await self.decide(payload)
            tool = str(decision.get("tool") or "")
            arguments = decision.get("arguments") or {}
            if tool not in ACTION_CATALOG:
                raise AgentToolRunError(f"工具不在白名单: {tool}")
            if not isinstance(arguments, dict):
                raise AgentToolRunError("工具 arguments 必须是对象")
            self._guard_high_risk(contract, tool)

            before = observation
            self._log(
                "tool_call",
                case_id=case_id,
                module=module,
                step=contract.get("step"),
                call_index=call_index,
                tool=tool,
                arguments=arguments,
            )
            try:
                tool_result = self.gateway.call(tool, arguments)
            except ToolGatewayError as exc:
                feedback = {"ok": False, "error": str(exc)}
                self._log(
                    "tool_failed",
                    case_id=case_id,
                    step=contract.get("step"),
                    call_index=call_index,
                    tool=tool,
                    error=str(exc),
                )
                if call_index >= self.max_calls:
                    raise AgentToolRunError(str(exc)) from exc
                continue

            observation = self.gateway.observer.observe()
            verified = self.guard.verify_postconditions(contract, before, observation)
            state_after_ok = True
            state_after_reason = ""
            if destination_state is not None:
                state_after = PageStateMatcher().match(destination_state, observation)
                state_after_ok = state_after.matched
                state_after_reason = state_after.reason
            if verified.ok and state_after_ok:
                self._log(
                    "step_verified",
                    case_id=case_id,
                    module=module,
                    step=contract.get("step"),
                    call_index=call_index,
                    tool=tool,
                )
                return AgentStepResult(
                    ok=True,
                    call_count=call_index,
                    last_tool=tool,
                    message="postconditions 已满足",
                )
            feedback = {
                "ok": bool(tool_result.get("ok")),
                "verification_errors": [
                    *verified.reasons,
                    *(
                        [f"expected_transition 未满足: {state_after_reason}"]
                        if not state_after_ok
                        else []
                    ),
                ],
            }
            self._log(
                "step_not_verified",
                case_id=case_id,
                step=contract.get("step"),
                call_index=call_index,
                reasons=feedback["verification_errors"],
            )

        raise AgentToolRunError(
            f"步骤 {contract.get('step')} 超过最大工具调用次数 {self.max_calls}"
        )

    def _recover_page_state(
        self,
        module,
        state,
        *,
        case_id: str,
        step: Any,
    ):
        """有限恢复：recover_page 回退最多 3 次；仍偏离则重进模块入口。"""
        matcher = PageStateMatcher()
        try:
            recovered = self.gateway.call(
                "recover_page",
                {"max_backs": 3, "relaunch": False},
            )
            observed = self.gateway.observer.observe()
            self._log(
                "page_recovery_back",
                case_id=case_id,
                module=module.name,
                step=step,
                via=(recovered.get("data") or {}).get("via"),
                backs=(recovered.get("data") or {}).get("backs"),
            )
            if matcher.match(state, observed).matched:
                return observed
        except Exception as exc:  # noqa: BLE001
            self._log(
                "page_recovery_back_failed",
                case_id=case_id,
                step=step,
                error=str(exc),
            )

        try:
            self.gateway.call("launch_app", {})
            for setup in module.entry_steps:
                tool = str(setup.get("action") or "")
                arguments = {
                    key: value
                    for key, value in setup.items()
                    if key not in {"action"}
                }
                self.gateway.call(tool, arguments)
            observed = self.gateway.observer.observe()
            self._log(
                "page_recovery_module_entry",
                case_id=case_id,
                module=module.name,
                step=step,
            )
            rematch = matcher.match(state, observed)
            if rematch.matched:
                return observed
            raise AgentToolRunError(f"重进模块后页面仍偏离: {rematch.reason}")
        except AgentToolRunError:
            raise
        except Exception as exc:
            raise AgentToolRunError(f"模块入口恢复失败: {exc}") from exc

    def _guard_high_risk(self, contract: dict[str, Any], tool: str) -> None:
        intent = str(contract.get("intent") or "")
        if not any(keyword in intent for keyword in HIGH_RISK_KEYWORDS):
            return
        if ACTION_CATALOG[tool]["read_only"]:
            return
        if not contract.get("deterministic_target"):
            raise AgentToolRunError("高风险动作缺少确定性目标，禁止 Agent 执行")

    async def _agent_decide(self, payload: dict[str, Any]) -> dict[str, Any]:
        from src.llm.prompts.skill_loader import load_skill

        skill = load_skill("execution-navigator")
        skill_prefix = (skill.body + "\n\n") if skill else ""
        prompt = (
            skill_prefix
            + "你是 App UI 单步执行导航器。只能处理 current_step，严格输出一个 JSON："
            '{"tool":"工具名","arguments":{...}}。不得输出解释。\n'
            "定位失败、卡在阅读器/弹层或页面偏离时，可先调用 recover_page"
            "（until 填期望 locator，max_backs 默认 3）。\n"
            "若 postconditions 含 text_absent:文案，表示该文案不应出现："
            "用 assert_text_absent 或 inspect_elements 确认不存在；"
            "禁止 assert_text 去找该文案，禁止 recover_page(until=该文案)。\n\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        result = await agent_runtime.run(
            AgentTask(
                role=ROLE_NAVIGATOR,
                prompt=prompt,
                workdir=str(self.run_dir),
                timeout=120,
                stage_name="execution_navigate",
                task_id=str(payload.get("case_id") or ""),
            )
        )
        if not result.success:
            raise AgentToolRunError(f"导航 Agent 失败: {result.error or 'unknown'}")
        extracted = extract_json(result.raw_output or "")
        data = extracted.data if extracted.success else None
        if not isinstance(data, dict):
            raise AgentToolRunError("导航 Agent 未返回合法工具调用 JSON")
        return data

    def _log(self, event: str, **payload: Any) -> None:
        from src.services.narrative_log import narrate

        self.run_dir.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **payload,
        }
        if not line.get("message"):
            line["message"] = narrate(event, **payload)
        with (self.run_dir / "agent_tool_ledger.jsonl").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
