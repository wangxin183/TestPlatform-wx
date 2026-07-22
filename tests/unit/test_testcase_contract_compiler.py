"""模块化 NL/DSL 双轨准备逻辑单测。"""

from __future__ import annotations

from execution_runtime.config import RuntimeConfig, TargetApp
from src.services.testcase_contract_compiler import (
    prepare_executable_case,
    repair_step_contract_states,
    score_assertion_quality,
)


def _cfg() -> RuntimeConfig:
    return RuntimeConfig(
        target_app=TargetApp(
            name="爱奇艺叭嗒",
            platform="android",
            bundle_id="com.iqiyi.acg",
        )
    )


def test_prepare_deterministic_case_builds_contracts_and_dsl():
    prepared = prepare_executable_case(
        {
            "case_id": "c1",
            "title": "搜索指定漫画",
            "module": "搜索",
            "steps": [
                {
                    "step": 1,
                    "action": "在搜索框输入「航海王」",
                    "expected": "显示「航海王」搜索结果",
                },
                {
                    "step": 2,
                    "action": "确认「航海王」可见",
                    "expected": "页面显示「航海王」",
                },
            ],
        },
        _cfg(),
    )
    assert prepared["compile_status"] == "ok"
    assert prepared["execution_mode"] == "hybrid"
    assert prepared["exec_script"]["steps"]
    assert len(prepared["step_contracts"]) == 2
    assert prepared["step_contracts"][0]["start_state"] == "search_main"


def test_prepare_ambiguous_target_requires_agent():
    prepared = prepare_executable_case(
        {
            "case_id": "c2",
            "title": "进入任一漫画",
            "module": "漫画详情页",
            "steps": [
                {
                    "step": 1,
                    "action": "点击任一漫画 card",
                    "expected": "页面显示「开始阅读」",
                }
            ],
        },
        _cfg(),
    )
    assert prepared["compile_status"] == "agent_required"
    assert prepared["execution_mode"] == "agent"
    assert prepared["step_contracts"][0]["target"]["description"] == "任一漫画 card"
    assert prepared["compile_errors"][0]["code"] == "AMBIGUOUS_TARGET"


def test_prepare_unknown_module_is_failed():
    prepared = prepare_executable_case(
        {
            "case_id": "c3",
            "title": "未知功能",
            "module": "",
            "steps": [{"step": 1, "action": "点击按钮", "expected": "显示结果"}],
        },
        _cfg(),
    )
    assert prepared["compile_status"] == "failed"
    assert prepared["exec_script"] is None
    assert prepared["compile_errors"][0]["code"] == "MODULE_REQUIRED"


def test_prepare_subjective_only_expected_is_failed():
    prepared = prepare_executable_case(
        {
            "case_id": "c4",
            "title": "阅读流畅性",
            "module": "漫画阅读器",
            "steps": [
                {
                    "step": 1,
                    "action": "向左滑动阅读区域",
                    "expected": "翻页流畅无明显卡顿",
                }
            ],
        },
        _cfg(),
    )
    assert prepared["compile_status"] == "failed"
    assert any(e["code"] == "UNVERIFIABLE_EXPECTED" for e in prepared["compile_errors"])


def test_prepare_cross_page_transition_carries_destination_to_next_step():
    case = {
        "case_id": "exe-13",
        "title": "会员页跳转",
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

    prepared = prepare_executable_case(case, _cfg())
    first, second = prepared["step_contracts"]

    assert first["start_state"] == "reader_main"
    assert first["expected_transition"] == "reader_main -> external:会员页"
    assert "page_changed" in first["postconditions"]
    assert second["start_state"] == "external:会员页"
    assert second["expected_transition"] == "external:会员页 -> external:会员页"


def test_repair_existing_same_state_contracts_for_cross_page_case():
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

    repaired = repair_step_contract_states(case, stale)

    assert repaired[0]["expected_transition"] == "reader_main -> external:会员页"
    assert repaired[1]["start_state"] == "external:会员页"


def test_negative_expected_compiles_to_text_absent_not_visible():
    """EXE-0018：不出现「续费会员」不得编成 text_visible / assert_text。"""
    prepared = prepare_executable_case(
        {
            "case_id": "neg-1",
            "title": "非会员不展示续费会员",
            "module": "漫画阅读器",
            "precondition_spec": {
                "login_state": "logged_in",
                "user_type": "non_member",
                "entry_context": "comic.member_free",
            },
            "steps": [
                {
                    "step": 1,
                    "action": "确认会员条按钮文案",
                    "expected": "会员条按钮文案显示「开通会员」",
                },
                {
                    "step": 2,
                    "action": "检查会员条按钮文案",
                    "expected": "会员条区域不出现「续费会员」文案",
                },
            ],
        },
        _cfg(),
    )
    assert prepared["compile_status"] in {"ok", "agent_required"}
    assert prepared["step_contracts"][0]["postconditions"] == ["text_visible:开通会员"]
    assert prepared["step_contracts"][1]["postconditions"] == ["text_absent:续费会员"]
    asserts = [
        step
        for step in prepared["exec_script"]["steps"]
        if step.get("action") in {"assert_text", "assert_text_absent"}
    ]
    assert any(
        s["action"] == "assert_text" and s["value"] == "开通会员" for s in asserts
    )
    assert any(
        s["action"] == "assert_text_absent" and s["value"] == "续费会员"
        for s in asserts
    )
    assert not any(
        s["action"] == "assert_text" and s["value"] == "续费会员" for s in asserts
    )


def test_prepare_unquoted_ui_text_stays_weak():
    """无「」时不再靠白名单抽 text_visible，仅弱断言或失败。"""
    prepared = prepare_executable_case(
        {
            "case_id": "c-known",
            "title": "追更按钮显示",
            "module": "漫画阅读器",
            "precondition_spec": {
                "login_state": "logged_in",
                "user_type": "non_member",
                "entry_context": "comic.member_free",
            },
            "steps": [
                {
                    "step": 1,
                    "action": "确认追更按钮",
                    "expected": "右下角出现追更按钮",
                }
            ],
        },
        _cfg(),
    )
    posts = prepared["step_contracts"][0]["postconditions"]
    assert "text_visible:追更" not in posts
    asserts = [
        step
        for step in (prepared.get("exec_script") or {}).get("steps") or []
        if step.get("action") == "assert_text" and step.get("value") == "追更"
    ]
    assert not asserts


def test_repair_rewrites_stale_positive_postcondition_for_absent_text():
    case = {
        "module": "漫画阅读器",
        "steps": [
            {
                "step": 1,
                "action": "检查会员条按钮文案",
                "expected": "会员条区域不出现「续费会员」文案",
            }
        ],
    }
    stale = [
        {
            "step": 1,
            "start_state": "reader_main",
            "expected_transition": "reader_main -> reader_main",
            "postconditions": ["text_visible:续费会员"],
        }
    ]
    repaired = repair_step_contract_states(case, stale)
    assert repaired[0]["postconditions"] == ["text_absent:续费会员"]


def test_score_skips_action_only_steps_without_postconditions() -> None:
    """中间 tap/wait 空断言不得把整案拖成 none。"""
    quality = score_assertion_quality(
        [
            {
                "action_kind": "assert",
                "start_state": "reader_main",
                "expected_transition": "reader_main -> reader_main",
                "postconditions": ["text_visible:追更"],
            },
            {
                "action_kind": "tap",
                "start_state": "reader_main",
                "expected_transition": "reader_main -> reader_main",
                "postconditions": [],
            },
            {
                "action_kind": "wait",
                "start_state": "reader_main",
                "expected_transition": "reader_main -> reader_main",
                "postconditions": [],
            },
            {
                "action_kind": "assert",
                "start_state": "reader_main",
                "expected_transition": "reader_main -> reader_main",
                "postconditions": ["text_absent:追更"],
            },
        ]
    )
    assert quality == "strong"


def test_prepare_follow_toast_case_not_failed_by_middle_wait() -> None:
    prepared = prepare_executable_case(
        {
            "case_id": "tp021",
            "title": "漫画阅读器-追更成功toast与按钮消失",
            "module": "漫画阅读器",
            "steps": [
                {
                    "step": 1,
                    "action": "确认右下角「追更」文案可见",
                    "expected": "阅读器右下角可见「追更」",
                },
                {
                    "step": 2,
                    "action": "点击右下角「追更」按钮",
                    "expected": "触发追更操作且无界面崩溃",
                },
                {
                    "step": 3,
                    "action": "等待",
                    "expected": "等待 2 秒至 toast 展示完成",
                },
                {
                    "step": 4,
                    "action": "检查 toast 提示",
                    "expected": "屏幕出现 toast 浮层且展示非空提示文案",
                },
                {
                    "step": 5,
                    "action": "确认右下角「追更」文案不可见",
                    "expected": "阅读器右下角不出现「追更」",
                },
            ],
        },
        _cfg(),
    )
    assert prepared["assertion_quality"] != "none"
    assert prepared["compile_status"] in {"ok", "agent_required"}
    assert not any(
        e.get("code") == "ASSERTION_QUALITY_LOW"
        for e in (prepared.get("compile_errors") or [])
    )


def test_weak_assertion_still_marks_failed_without_hardcoded_suggestion() -> None:
    prepared = prepare_executable_case(
        {
            "case_id": "weak1",
            "title": "弱断言",
            "module": "漫画阅读器",
            "steps": [
                {
                    "step": 1,
                    "action": "点击简介区域「展开」入口",
                    "expected": "自屏幕下方弹出简介弹窗，弹窗内可见完整简介正文",
                }
            ],
        },
        _cfg(),
    )
    assert prepared["compile_status"] == "failed"
    assert prepared["compile_errors"]
    err = prepared["compile_errors"][0]
    assert err.get("code") == "WEAK_ASSERTION"
    # 规则编译不再内置 suggestion；由 compile_advisor 异步填充
    assert not err.get("suggestion")
