"""DSL 失败后的受控 Agent 工具循环单测。"""

from __future__ import annotations

import json

import pytest

from execution_runtime.agent_tool_runner import AgentToolRunner, AgentToolRunError
from execution_runtime.agent_tool_runner import AgentStepResult
from execution_runtime.case_runner import run_case
from execution_runtime.config import RuntimeConfig, TargetApp
from execution_runtime.dsl.models import ExecScript
from execution_runtime.engine.executor import StepExecError
from execution_runtime.models.result import StepOutcome
from execution_runtime.navigation.path_cache import NavigationPathCache
from execution_runtime.tools.gateway import ToolGateway


class FakeDriver:
    current_package = "com.iqiyi.acg"
    current_activity = ".SearchActivity"
    page_source = (
        '<hierarchy><node text="搜索" resource-id="search_title" '
        'clickable="false" enabled="true" displayed="true"/>'
        '<node text="航海王" resource-id="result_title" '
        'clickable="true" enabled="true" displayed="true"/></hierarchy>'
    )

    def get_screenshot_as_png(self):
        return b"page"

    def get_screenshot_as_file(self, _path):
        return True

    def back(self):
        pass

    def activate_app(self, _bundle_id):
        pass

    def terminate_app(self, _bundle_id):
        pass


def _cfg() -> RuntimeConfig:
    return RuntimeConfig(
        target_app=TargetApp(
            platform="android",
            bundle_id="com.iqiyi.acg",
        )
    )


@pytest.mark.asyncio
async def test_agent_runner_executes_one_tool_and_verifies(tmp_path):
    calls = []

    async def decide(payload):
        calls.append(payload)
        return {"tool": "assert_text", "arguments": {"value": "航海王"}}

    runner = AgentToolRunner(
        ToolGateway(FakeDriver(), _cfg()),
        run_dir=tmp_path,
        decide=decide,
        max_calls=3,
    )
    result = await runner.run_step(
        {
            "step": 2,
            "start_state": "search_main",
            "intent": "确认航海王搜索结果可见",
            "target": {"description": "航海王"},
            "postconditions": ["text_visible:航海王"],
        },
        module="搜索",
        case_id="c1",
    )
    assert result.ok is True
    assert result.call_count == 1
    assert calls[0]["current_step"]["step"] == 2
    ledger = [
        json.loads(line)
        for line in (tmp_path / "agent_tool_ledger.jsonl").read_text().splitlines()
    ]
    assert ledger[-1]["event"] == "step_verified"


@pytest.mark.asyncio
async def test_agent_runner_rejects_tool_outside_catalog(tmp_path):
    async def decide(_payload):
        return {"tool": "shell", "arguments": {"command": "echo bad"}}

    runner = AgentToolRunner(
        ToolGateway(FakeDriver(), _cfg()),
        run_dir=tmp_path,
        decide=decide,
        max_calls=1,
    )
    with pytest.raises(AgentToolRunError, match="工具不在白名单"):
        await runner.run_step(
            {
                "step": 1,
                "start_state": "search_main",
                "intent": "确认搜索页",
                "target": {"description": "搜索"},
                "postconditions": ["text_visible:搜索"],
            },
            module="搜索",
            case_id="c2",
        )


@pytest.mark.asyncio
async def test_agent_runner_blocks_high_risk_without_deterministic_target(tmp_path):
    async def decide(_payload):
        return {
            "tool": "tap",
            "arguments": {"locator": {"type": "text", "value": "立即支付"}},
        }

    runner = AgentToolRunner(
        ToolGateway(FakeDriver(), _cfg()),
        run_dir=tmp_path,
        decide=decide,
        max_calls=1,
    )
    with pytest.raises(AgentToolRunError, match="高风险动作"):
        await runner.run_step(
            {
                "step": 1,
                "start_state": "search_main",
                "intent": "点击立即支付",
                "target": {"description": "立即支付"},
                "postconditions": ["expected_state_visible"],
            },
            module="搜索",
            case_id="c3",
        )


def test_case_runner_switches_to_agent_after_dsl_locator_failure(
    tmp_path,
    monkeypatch,
):
    import execution_runtime.case_runner as module

    class FailingExecutor:
        def __init__(self, _driver, _cfg):
            pass

        def execute(self, _step):
            raise StepExecError("定位漂移", kind="broken")

    class SuccessfulAgentRunner:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run_step(self, _contract, **_kwargs):
            return AgentStepResult(
                ok=True,
                call_count=1,
                last_tool="tap",
                message="Agent 已重新定位",
            )

    monkeypatch.setattr(module, "StepExecutor", FailingExecutor)
    monkeypatch.setattr(module, "AgentToolRunner", SuccessfulAgentRunner)
    monkeypatch.setattr(module, "ToolGateway", lambda *_args, **_kwargs: object())

    script = ExecScript.from_dict(
        {
            "case_id": "hybrid-case",
            "module": "搜索",
            "execution_mode": "hybrid",
            "step_contracts": [
                {
                    "step": 1,
                    "start_state": "search_main",
                    "intent": "点击搜索结果",
                    "postconditions": ["text_visible:航海王"],
                }
            ],
            "steps": [
                {
                    "action": "tap",
                    "description": "点击搜索结果",
                    "locator": {"type": "text", "value": "航海王"},
                }
            ],
        }
    )
    result = run_case(FakeDriver(), script, _cfg(), tmp_path)
    assert result.outcome == StepOutcome.PASSED
    assert result.healed_count == 1
    assert result.steps[0].matched_by == "agent:tap"


def test_case_runner_verifies_cross_page_transition_after_successful_tap(
    tmp_path,
    monkeypatch,
):
    import execution_runtime.case_runner as module

    agent_calls = []

    class SuccessfulButNoTransitionExecutor:
        def __init__(self, _driver, _cfg):
            pass

        def execute(self, _step):
            return "text"

    class TransitionAgentRunner:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run_step(self, contract, **_kwargs):
            agent_calls.append(contract)
            return AgentStepResult(
                ok=True,
                call_count=1,
                last_tool="tap",
                message="Agent 完成跨页跳转",
            )

    monkeypatch.setattr(module, "StepExecutor", SuccessfulButNoTransitionExecutor)
    monkeypatch.setattr(module, "AgentToolRunner", TransitionAgentRunner)
    monkeypatch.setattr(module, "ToolGateway", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(module, "TRANSITION_WAIT_SECONDS", 0)
    script = ExecScript.from_dict(
        {
            "case_id": "transition-case",
            "module": "漫画阅读器",
            "execution_mode": "hybrid",
            "step_contracts": [
                {
                    "step": 1,
                    "start_state": "reader_main",
                    "intent": "点击开通会员",
                    "expected_transition": "reader_main -> external:会员页",
                    "postconditions": ["page_changed"],
                }
            ],
            "steps": [
                {
                    "action": "tap",
                    "description": "点击开通会员",
                    "locator": {"type": "text", "value": "开通会员"},
                }
            ],
        }
    )

    result = run_case(FakeDriver(), script, _cfg(), tmp_path)

    assert result.outcome == StepOutcome.PASSED
    assert len(agent_calls) == 1
    assert result.healed_count == 1


@pytest.mark.asyncio
async def test_agent_runner_navigates_to_module_until_page_state_matches(tmp_path):
    class NavigateDriver(FakeDriver):
        page_source = '<hierarchy><node text="首页" resource-id="home"/></hierarchy>'

        def back(self):
            self.page_source = (
                '<hierarchy><node text="搜索" resource-id="search_title" '
                'enabled="true" displayed="true"/></hierarchy>'
            )

    async def decide(_payload):
        return {"tool": "back", "arguments": {}}

    runner = AgentToolRunner(
        ToolGateway(NavigateDriver(), _cfg()),
        run_dir=tmp_path,
        decide=decide,
        max_calls=2,
    )
    result = await runner.navigate_to_module("搜索", case_id="nav-1")
    assert result.ok is True
    assert result.last_tool == "back"


@pytest.mark.asyncio
async def test_module_navigation_replays_cached_path_without_agent(tmp_path):
    class NavigateDriver(FakeDriver):
        page_source = '<hierarchy><node text="首页" resource-id="home"/></hierarchy>'

        def back(self):
            self.page_source = (
                '<hierarchy><node text="搜索" resource-id="search_title" '
                'enabled="true" displayed="true"/></hierarchy>'
            )

    cache = NavigationPathCache(tmp_path / "paths.json")
    cache.save(
        app_id="com.iqiyi.acg",
        module="搜索",
        start_package="com.iqiyi.acg",
        start_activity=".SearchActivity",
        actions=[{"tool": "back", "arguments": {}}],
    )

    async def should_not_decide(_payload):
        raise AssertionError("缓存命中时不应调用 Agent")

    runner = AgentToolRunner(
        ToolGateway(NavigateDriver(), _cfg()),
        run_dir=tmp_path,
        decide=should_not_decide,
        path_cache=cache,
    )
    result = await runner.navigate_to_module("搜索", case_id="cached-nav")

    assert result.ok is True
    assert result.message == "已通过缓存路径进入 搜索"


@pytest.mark.asyncio
async def test_module_navigation_invalidates_bad_cache_then_explores(tmp_path):
    class NavigateDriver(FakeDriver):
        page_source = '<hierarchy><node text="首页" resource-id="home"/></hierarchy>'

        def back(self):
            self.page_source = (
                '<hierarchy><node text="搜索" resource-id="search_title" '
                'enabled="true" displayed="true"/></hierarchy>'
            )

    cache = NavigationPathCache(tmp_path / "paths.json")
    cache.save(
        app_id="com.iqiyi.acg",
        module="搜索",
        start_package="com.iqiyi.acg",
        start_activity=".SearchActivity",
        actions=[{"tool": "不存在", "arguments": {}}],
    )
    decisions = []

    async def decide(_payload):
        decisions.append(True)
        return {"tool": "back", "arguments": {}}

    runner = AgentToolRunner(
        ToolGateway(NavigateDriver(), _cfg()),
        run_dir=tmp_path,
        decide=decide,
        path_cache=cache,
    )
    result = await runner.navigate_to_module("搜索", case_id="fallback-nav")

    assert result.ok is True
    assert decisions == [True]
    assert cache.load(
        app_id="com.iqiyi.acg",
        module="搜索",
        start_package="com.iqiyi.acg",
        start_activity=".SearchActivity",
    ) == [{"tool": "back", "arguments": {}}]


@pytest.mark.asyncio
async def test_module_navigation_stops_repeated_action_loop(tmp_path):
    class StuckDriver(FakeDriver):
        page_source = '<hierarchy><node text="首页" resource-id="home"/></hierarchy>'

        def back(self):
            pass

    async def decide(_payload):
        return {"tool": "back", "arguments": {}}

    runner = AgentToolRunner(
        ToolGateway(StuckDriver(), _cfg()),
        run_dir=tmp_path,
        decide=decide,
        path_cache=NavigationPathCache(tmp_path / "paths.json"),
        max_calls=3,
    )
    with pytest.raises(AgentToolRunError, match="重复动作循环"):
        await runner.navigate_to_module("搜索", case_id="stuck-nav")


@pytest.mark.asyncio
async def test_agent_step_does_not_recover_unknown_external_start_state(tmp_path):
    class MemberDriver(FakeDriver):
        current_activity = ".MemberActivity"
        page_source = (
            '<hierarchy><node text="会员中心" resource-id="member_title" '
            'enabled="true" displayed="true"/></hierarchy>'
        )

    async def decide(_payload):
        return {"tool": "assert_text", "arguments": {"value": "会员中心"}}

    runner = AgentToolRunner(
        ToolGateway(MemberDriver(), _cfg()),
        run_dir=tmp_path,
        decide=decide,
        max_calls=1,
    )
    result = await runner.run_step(
        {
            "step": 4,
            "start_state": "external:帮助页",
            "intent": "确认帮助页可见",
            "expected_transition": "external:帮助页 -> external:帮助页",
            "postconditions": ["text_visible:会员中心"],
        },
        module="漫画阅读器",
        case_id="external-state",
    )

    assert result.ok is True


@pytest.mark.asyncio
async def test_agent_step_accepts_expected_transition_out_of_module(tmp_path):
    class TransitionDriver(FakeDriver):
        current_activity = ".SearchActivity"
        page_source = (
            '<hierarchy><node text="搜索" resource-id="search_title" '
            'enabled="true" displayed="true"/></hierarchy>'
        )

        def back(self):
            self.current_activity = ".MemberActivity"
            self.page_source = (
                '<hierarchy><node text="会员中心" resource-id="member_title" '
                'enabled="true" displayed="true"/></hierarchy>'
            )

    async def decide(_payload):
        return {"tool": "back", "arguments": {}}

    runner = AgentToolRunner(
        ToolGateway(TransitionDriver(), _cfg()),
        run_dir=tmp_path,
        decide=decide,
        max_calls=1,
    )
    result = await runner.run_step(
        {
            "step": 3,
            "start_state": "search_main",
            "intent": "跳转会员页",
            "expected_transition": "search_main -> external:会员页",
            "postconditions": ["page_changed"],
        },
        module="搜索",
        case_id="external-transition",
    )

    assert result.ok is True
