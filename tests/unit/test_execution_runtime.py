"""execution_runtime 单元测试（不依赖真机 Appium 会话）。"""

from __future__ import annotations

import json

import pytest

from execution_runtime.compiler.local_compiler import compile_case_local
from execution_runtime.config import RuntimeConfig, TargetApp
from execution_runtime.dsl.models import ExecScript, Locator, Step
from execution_runtime.env.precheck import run_precheck
from execution_runtime.runner import _repair_stored_contracts, _sort_cases_by_module
from execution_runtime.session.module_session import (
    ModuleSessionCoordinator,
    prepare_module_session,
)


def test_exec_script_from_dict():
    data = {
        "case_id": "abc",
        "title": "t",
        "steps": [
            {"action": "launch_app", "description": "启动"},
            {"action": "screenshot", "description": "留证"},
        ],
    }
    script = ExecScript.from_dict(data)
    assert script.case_id == "abc"
    assert len(script.steps) == 2


def test_step_requires_locator_for_tap():
    with pytest.raises(ValueError):
        Step(action="tap", description="x")


def test_local_compiler_swipe_case():
    cfg = RuntimeConfig(target_app=TargetApp(name="爱奇艺叭嗒", bundle_id="com.iqiyi.acg"))
    case = {
        "case_id": "c1",
        "title": "滚动测试",
        "steps": [
            {
                "step": 1,
                "action": "快速向上滑动屏幕，连续滚动至章节末尾",
                "expected": "滚动过程流畅无明显卡顿",
            }
        ],
    }
    script = compile_case_local(case, cfg)
    assert script.steps[0].action == "launch_app"
    assert any(s.action == "swipe" for s in script.steps)
    assert any(s.action == "screenshot" for s in script.steps)


def test_local_compiler_right_swipe_not_left_when_sidebar_mentioned():
    """「向右滑动」+「左侧页码」不得因「左」字误判为 left（EXE-0009）。"""
    cfg = RuntimeConfig(target_app=TargetApp(platform="android", bundle_id="com.iqiyi.acg"))
    case = {
        "case_id": "exe9",
        "title": "左右翻页滑动切换页面",
        "steps": [
            {
                "step": 1,
                "action": "在阅读区域向右滑动切换到上一页",
                "expected": "页面切换流畅，左侧内容更新",
            },
            {
                "step": 2,
                "action": "查看页码指示器数值（若界面存在）",
                "expected": "页码指示器数值与当前展示页码一致，翻页后实时同步更新",
            },
        ],
    }
    script = compile_case_local(case, cfg)
    swipe = next(s for s in script.steps if s.action == "swipe")
    assert swipe.direction == "right"
    assert not any(s.action == "assert_text" for s in script.steps)
    assert any(s.action == "screenshot" for s in script.steps)


def test_local_compiler_quoted_assert_text():
    cfg = RuntimeConfig(target_app=TargetApp(platform="android", bundle_id="com.iqiyi.acg"))
    case = {
        "case_id": "c2",
        "title": "登录提示",
        "steps": [
            {
                "step": 1,
                "action": "确认错误提示可见",
                "expected": "界面显示「账号或密码错误」",
            }
        ],
    }
    script = compile_case_local(case, cfg)
    asserts = [s for s in script.steps if s.action == "assert_text"]
    assert len(asserts) == 1
    assert asserts[0].value == "账号或密码错误"


def test_local_compiler_negative_assert_text_absent():
    cfg = RuntimeConfig(target_app=TargetApp(platform="android", bundle_id="com.iqiyi.acg"))
    case = {
        "case_id": "c2b",
        "title": "不展示续费",
        "steps": [
            {
                "step": 1,
                "action": "检查会员条按钮文案",
                "expected": "会员条区域不出现「续费会员」文案",
            }
        ],
    }
    script = compile_case_local(case, cfg)
    absent = [s for s in script.steps if s.action == "assert_text_absent"]
    positive = [
        s for s in script.steps if s.action == "assert_text" and s.value == "续费会员"
    ]
    assert len(absent) == 1
    assert absent[0].value == "续费会员"
    assert not positive


def test_local_compiler_unquoted_ui_text_no_whitelist():
    cfg = RuntimeConfig(target_app=TargetApp(platform="android", bundle_id="com.iqiyi.acg"))
    case = {
        "case_id": "c2c",
        "title": "追更显示",
        "steps": [
            {
                "step": 1,
                "action": "确认追更按钮",
                "expected": "右下角出现追更按钮",
            }
        ],
    }
    script = compile_case_local(case, cfg)
    asserts = [s for s in script.steps if s.action == "assert_text" and s.value == "追更"]
    assert asserts == []


def test_precheck_loads():
    cfg = RuntimeConfig()
    result = run_precheck(cfg, auto_repair=False)
    assert result.items
    assert isinstance(result.as_dict(), dict)


def test_precheck_android():
    from execution_runtime.config import Device, TargetApp

    cfg = RuntimeConfig(
        target_app=TargetApp(
            name="爱奇艺叭嗒",
            platform="android",
            bundle_id="com.iqiyi.acg",
        ),
        device=Device(
            udid="emulator-5554",
            device_name="emu",
            platform_version="16",
            automation_name="UiAutomator2",
        ),
    )
    result = run_precheck(cfg, auto_repair=False)
    keys = {i.key for i in result.items}
    assert "uiautomator2_driver" in keys
    assert "adb_cli" in keys
    assert "android_device" in keys


def test_android_capabilities():
    from execution_runtime.config import Device, TargetApp
    from execution_runtime.engine.appium_driver import build_android_capabilities

    cfg = RuntimeConfig(
        target_app=TargetApp(
            platform="android",
            bundle_id="com.iqiyi.acg",
            app_activity=".MainActivity",
        ),
        device=Device(
            udid="emulator-5554",
            automation_name="UiAutomator2",
            platform_version="16",
        ),
    )
    caps = build_android_capabilities(cfg)
    assert caps["platformName"] == "Android"
    assert caps["appium:appPackage"] == "com.iqiyi.acg"
    assert caps["appium:appActivity"] == ".MainActivity"


def test_locator_model():
    loc = Locator(type="name", value="登录")
    assert loc.type == "name"


def test_sort_cases_groups_same_module_stably():
    cases = [
        {"case_id": "3", "module": "搜索"},
        {"case_id": "1", "module": "漫画阅读器"},
        {"case_id": "2", "module": "搜索"},
        {"case_id": "4", "module": ""},
    ]
    assert [c["case_id"] for c in _sort_cases_by_module(cases)] == ["3", "2", "1", "4"]


def test_module_session_reuses_same_module_and_switches():
    coordinator = ModuleSessionCoordinator()
    first = coordinator.plan("漫画阅读器")
    same = coordinator.plan("漫画阅读器")
    switched = coordinator.plan("搜索")
    assert first.run_setup is True
    assert first.reuse_session is False
    assert same.run_setup is False
    assert same.reuse_session is True
    assert switched.run_setup is True
    assert switched.module_changed is True


def test_exec_script_keeps_module_contract_fields():
    script = ExecScript.from_dict(
        {
            "case_id": "c-module",
            "module": "漫画阅读器",
            "execution_mode": "hybrid",
            "module_setup": [
                {
                    "action": "tap",
                    "description": "进入漫画 tab",
                    "locator": {"type": "text", "value": "漫画"},
                }
            ],
            "step_contracts": [
                {
                    "step": 1,
                    "start_state": "reader_main",
                    "intent": "向左滑动",
                    "postconditions": ["page_content_changed"],
                }
            ],
            "steps": [{"action": "swipe", "direction": "left"}],
        }
    )
    assert script.module == "漫画阅读器"
    assert script.execution_mode == "hybrid"
    assert script.module_setup[0].action == "tap"
    assert script.step_contracts[0]["start_state"] == "reader_main"


def test_hybrid_module_session_uses_smart_navigation_before_static_setup(
    tmp_path,
    monkeypatch,
):
    import execution_runtime.session.module_session as session_module
    from execution_runtime.agent_tool_runner import AgentStepResult

    deterministic_calls = []

    class CountingExecutor:
        def __init__(self, *_args):
            pass

        def execute(self, _step):
            deterministic_calls.append(True)

    class SuccessfulNavigator:
        def __init__(self, *_args, **_kwargs):
            pass

        async def navigate_to_module(self, module, *, case_id):
            assert module == "搜索"
            assert case_id == "smart-nav"
            return AgentStepResult(ok=True, call_count=1, last_tool="tap")

    class Driver:
        current_package = "com.iqiyi.acg"
        current_activity = ".MainActivity"
        page_source = '<hierarchy><node text="首页" resource-id="home"/></hierarchy>'

        def get_screenshot_as_png(self):
            return b"home"

        def activate_app(self, _bundle_id):
            pass

    monkeypatch.setattr(session_module, "StepExecutor", CountingExecutor)
    monkeypatch.setattr(session_module, "AgentToolRunner", SuccessfulNavigator)
    script = ExecScript.from_dict(
        {
            "case_id": "smart-nav",
            "module": "搜索",
            "execution_mode": "hybrid",
            "module_setup": [
                {
                    "action": "tap",
                    "locator": {"type": "text", "value": "旧入口"},
                }
            ],
            "steps": [{"action": "screenshot"}],
        }
    )
    cfg = RuntimeConfig(
        target_app=TargetApp(platform="android", bundle_id="com.iqiyi.acg")
    )

    prepare_module_session(
        Driver(),
        cfg,
        script,
        ModuleSessionCoordinator(),
        tmp_path,
    )

    assert deterministic_calls == []


def test_runtime_repairs_stale_cross_page_contract_before_execution():
    case = {
        "module": "漫画阅读器",
        "steps": [
            {
                "step": 1,
                "action": "点击会员条「开通会员」按钮",
                "expected": "页面从漫画阅读器跳转至会员页",
            },
            {
                "step": 2,
                "action": "确认会员页主内容区域可见",
                "expected": "会员页主内容区域可见且页面加载完成",
            },
        ],
    }
    stale = [
        {
            "step": 1,
            "start_state": "reader_main",
            "expected_transition": "reader_main -> reader_main",
            "postconditions": ["expected_state_visible"],
        },
        {
            "step": 2,
            "start_state": "reader_main",
            "expected_transition": "reader_main -> reader_main",
            "postconditions": ["expected_state_visible"],
        },
    ]

    repaired = _repair_stored_contracts(case, stale)

    assert repaired[0]["expected_transition"] == "reader_main -> external:会员页"
    assert repaired[1]["start_state"] == "external:会员页"
