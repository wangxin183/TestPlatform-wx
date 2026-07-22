"""automation lint / automation_level 推断单测。"""

from __future__ import annotations

from src.services.testcase_automation_lint import (
    LEVEL_MANUAL,
    LEVEL_READY,
    LEVEL_SEMI,
    lint_case,
    resolve_automation_level,
)


def test_lint_ready_case():
    case = {
        "steps": [
            {"action": "点击「登录」按钮", "expected": "进入首页"},
            {"action": "确认首页标题可见", "expected": "显示「首页」"},
        ],
        "preconditions": "已安装 App",
    }
    result = lint_case(case)
    assert result["level"] == LEVEL_READY


def test_lint_entry_context_exempts_already_entered():
    case = {
        "steps": [
            {"action": "确认会员条可见", "expected": "显示「开通会员」"},
        ],
        "preconditions": "已进入漫画阅读器；已登录非会员",
        "precondition_spec": {
            "login_state": "logged_in",
            "user_type": "non_member",
            "entry_context": "comic.member_free",
        },
    }
    result = lint_case(case)
    assert result["level"] == LEVEL_READY
    assert not any("前置条件未步骤化" in w for w in result["warnings"])


def test_lint_manual_precondition_still_semi():
    case = {
        "steps": [{"action": "确认「开通会员」可见", "expected": "显示「开通会员」"}],
        "preconditions": "需人工准备已购单集",
        "precondition_spec": {
            "login_state": "logged_in",
            "user_type": "non_member",
            "entry_context": "comic.pay_per_episode",
        },
    }
    result = lint_case(case)
    assert result["level"] == LEVEL_SEMI


def test_lint_unquoted_subjective_counts_manual_hit():
    """无「」时主观词不再被白名单客观信号冲淡（单次 manual_hit 仍归 semi）。"""
    case = {
        "steps": [
            {
                "action": "确认追更按钮",
                "expected": "右下角出现追更按钮，状态与预期一致",
            }
        ],
        "preconditions": "已进入阅读器",
        "precondition_spec": {
            "login_state": "logged_in",
            "user_type": "non_member",
            "entry_context": "comic.member_free",
        },
    }
    result = lint_case(case)
    assert result["manual_hits"] >= 1
    assert any("主观 expected" in w for w in result["warnings"])
    assert result["level"] in (LEVEL_SEMI, LEVEL_MANUAL)


def test_lint_quoted_plus_subjective_is_semi():
    case = {
        "steps": [
            {
                "action": "确认追更按钮",
                "expected": "右下角出现「追更」按钮，状态与预期一致",
            }
        ],
        "preconditions": "已进入阅读器",
        "precondition_spec": {
            "login_state": "logged_in",
            "user_type": "non_member",
            "entry_context": "comic.member_free",
        },
    }
    result = lint_case(case)
    assert result["level"] == LEVEL_SEMI
    assert result["manual_hits"] == 0


def test_lint_observational_is_semi():
    case = {
        "steps": [
            {
                "action": "查看页码指示器（若界面存在）",
                "expected": "页码指示器数值与当前展示页码一致，翻页后实时同步更新",
            }
        ]
    }
    result = lint_case(case)
    assert result["level"] in (LEVEL_SEMI, LEVEL_MANUAL)
    assert result["warnings"]


def test_lint_manual_tag():
    case = {
        "steps": [{"action": "点击「A」", "expected": "显示「A」"}],
        "tags": ["manual"],
    }
    assert lint_case(case)["level"] == LEVEL_MANUAL


def test_resolve_prefers_stored_then_conservative():
    case = {
        "automation_level": "ready",
        "steps": [
            {
                "action": "查看（若存在）",
                "expected": "流畅无卡顿",
            }
        ],
    }
    # 声明 ready 但规则更严 → 取更保守
    assert resolve_automation_level(case) in (LEVEL_SEMI, LEVEL_MANUAL)
