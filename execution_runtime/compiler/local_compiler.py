"""本地规则编译器：agent 不可用时的确定性兜底。

不替代 execution.compiler 主路径，仅在 agent_runtime 全部失败时启用，
保证 P0 能在真机上跑通基本流程（launch / swipe / screenshot / tap 等）。
"""

from __future__ import annotations

import re
from typing import Any

from execution_runtime.compiler.nl_assert import (
    objective_text_signals,
)
from execution_runtime.config import RuntimeConfig
from execution_runtime.dsl.models import Locator, Step, ExecScript


def _nl_step(step: dict[str, Any]) -> tuple[str, str]:
    action = str(step.get("action") or "").strip()
    expected = str(step.get("expected") or "").strip()
    return action, expected


def _is_swipe_action(text: str) -> bool:
    return any(k in text for k in ("滑动", "滚动", "上滑", "下滑", "左滑", "右滑", "swipe", "scroll"))


def _swipe_direction(text: str) -> str:
    """优先匹配「向左/向右」等明确手势，避免「左侧/右侧」误伤。"""
    if any(k in text for k in ("向上", "上滑", "往上", "上划")):
        return "up"
    if any(k in text for k in ("向下", "下滑", "往下", "下划")):
        return "down"
    # 明确翻页手势优先于「左侧/右侧」描述
    if any(k in text for k in ("向左", "往左", "左滑", "左划", "左翻")):
        return "left"
    if any(k in text for k in ("向右", "往右", "右滑", "右划", "右翻")):
        return "right"
    if "左侧" in text and "右侧" not in text and not any(
        k in text for k in ("向右", "往右", "右滑", "右划")
    ):
        # 「从左侧滑入」等仍可能含左，但无明确向右时才用左
        if any(k in text for k in ("从左", "左侧滑", "左侧划")):
            return "left"
    if "右侧" in text and not any(k in text for k in ("向左", "往左", "左滑", "左划")):
        if any(k in text for k in ("从右", "右侧滑", "右侧划")):
            return "right"
    # 单字兜底：仅当无「向右」类冲突时
    if "左" in text and "右" not in text:
        return "left"
    if "右" in text and "左" not in text:
        return "right"
    if "章节" in text or "末尾" in text or "到底" in text:
        return "up"
    return "up"


def _is_subjective(expected: str) -> bool:
    keys = (
        "流畅",
        "卡顿",
        "逐步加载",
        "无空白",
        "符合预期",
        "正常",
        "加载失败",
        "同步更新",
        "实时同步",
        "一致",
        "无明显",
        "无异常",
        "体验良好",
    )
    return any(k in expected for k in keys)


def _is_observational(action: str) -> bool:
    """查看/观察/若存在 → 截图留证，不作硬断言。"""
    if any(k in action for k in ("若界面存在", "若存在", "如果存在", "（若", "(若")):
        return True
    if any(k in action for k in ("查看", "观察", "留意", "目视")):
        # 「确认/验证 xxx 可见」仍可走断言路径
        if any(k in action for k in ("验证", "检查", "确认", "断言")):
            return False
        return True
    return False


def _extract_assert_value(expected: str) -> str | None:
    """从 expected 提取短可判定片段；整句 NL 不可用则返回 None。

    负向「不出现「x」」不走本函数；由 _compile_assert_steps 单独处理。
    """
    if not expected or _is_subjective(expected):
        return None
    # 仅正向文案（引号 + 已知 UI）
    visible, absent = objective_text_signals(expected)
    if visible:
        return visible[0]
    if absent:
        return None
    # 页码类格式提示
    m = re.search(r"(\d+\s*/\s*\d+\s*话?)", expected)
    if m:
        return re.sub(r"\s+", "", m.group(1))
    # 过长整句 NL 禁止直接 assert
    if len(expected) > 24 or "，" in expected or "。" in expected:
        return None
    # 短具体文案
    if re.search(r"[\u4e00-\u9fffA-Za-z0-9]", expected) and len(expected) <= 24:
        return expected
    return None


def _compile_assert_steps(action_nl: str, expected: str) -> list[Step]:
    """正向 assert_text；负向 assert_text_absent。"""
    steps: list[Step] = []
    visible, absent = objective_text_signals(expected)
    for value in absent:
        steps.append(
            Step(
                action="assert_text_absent",
                description=action_nl,
                expected=expected,
                value=value,
            )
        )
    if visible:
        for value in visible:
            steps.append(
                Step(
                    action="assert_text",
                    description=action_nl,
                    expected=expected,
                    value=value,
                )
            )
    elif not steps:
        assert_val = _extract_assert_value(expected)
        if assert_val:
            steps.append(
                Step(
                    action="assert_text",
                    description=action_nl,
                    expected=expected,
                    value=assert_val,
                )
            )
        else:
            steps.append(
                Step(action="screenshot", description=action_nl, expected=expected)
            )
    return steps


def _is_launch(text: str) -> bool:
    return any(k in text for k in ("打开", "启动", "launch", "进入 App", "进入app"))


def _is_tap(text: str) -> bool:
    return any(k in text for k in ("点击", "点按", "轻点", "选择", "tap"))


def _is_input(text: str) -> bool:
    return any(k in text for k in ("输入", "填写", "键入"))


def _is_assert(text: str, expected: str) -> bool:
    return any(k in text for k in ("验证", "检查", "确认", "断言")) or bool(expected)


def _extract_tap_target(text: str) -> str:
    m = re.search(r"[「『\"']([^」』\"']+)[」』\"']", text)
    if m:
        return m.group(1)
    for kw in ("点击", "点按", "选择"):
        if kw in text:
            tail = text.split(kw, 1)[-1].strip()
            tail = re.sub(r"(按钮|入口|图标|菜单|tab|Tab)$", "", tail).strip()
            if tail:
                return tail[:20]
    return ""


def _extract_input_value(text: str) -> str:
    m = re.search(r"(\d{11})", text)
    if m:
        return m.group(1)
    m = re.search(r"[「『\"']([^」』\"']+)[」』\"']", text)
    if m:
        return m.group(1)
    if "手机号" in text:
        return "13800138000"
    if "密码" in text:
        return "Test123456"
    return "test"


def compile_case_local(case: dict[str, Any], cfg: RuntimeConfig) -> ExecScript:
    """把 NL steps 编译为可执行 DSL（规则引擎，无 LLM）。"""
    case_id = str(case.get("case_id") or "")
    title = str(case.get("title") or "")
    preconditions = str(case.get("preconditions") or "")
    nl_steps = case.get("steps") or []

    dsl_steps: list[Step] = [
        Step(action="launch_app", description="启动被测 App", expected="App 进入前台"),
    ]

    for raw in nl_steps:
        action_nl, expected = _nl_step(raw)
        if not action_nl:
            continue

        if _is_launch(action_nl):
            continue  # 已有 launch_app

        if _is_swipe_action(action_nl):
            direction = _swipe_direction(action_nl)
            times = 30 if any(k in action_nl for k in ("连续", "快速", "章节", "末尾", "到底")) else 5
            dsl_steps.append(
                Step(
                    action="swipe",
                    description=action_nl,
                    expected=expected,
                    direction=direction,  # type: ignore[arg-type]
                    ratio=0.65,
                    times=times,
                )
            )
            if _is_subjective(expected) or not _extract_assert_value(expected):
                if expected:
                    dsl_steps.append(
                        Step(action="screenshot", description=f"留证: {action_nl}", expected=expected)
                    )
            continue

        if _is_tap(action_nl):
            target = _extract_tap_target(action_nl)
            if target:
                loc_type = "text" if (cfg.target_app.platform or "ios").lower() == "android" else "name"
                dsl_steps.append(
                    Step(
                        action="tap",
                        description=action_nl,
                        expected=expected,
                        locator=Locator(type=loc_type, value=target),
                    )
                )
            else:
                dsl_steps.append(
                    Step(action="screenshot", description=action_nl, expected=expected)
                )
            continue

        if _is_input(action_nl):
            val = _extract_input_value(action_nl)
            platform = (cfg.target_app.platform or "ios").lower()
            if platform == "android":
                loc = Locator(type="xpath", value="//android.widget.EditText[1]")
            else:
                loc = Locator(
                    type="class_chain",
                    value="**/XCUIElementTypeTextField[1]",
                )
            dsl_steps.append(
                Step(
                    action="input",
                    description=action_nl,
                    expected=expected,
                    locator=loc,
                    value=val,
                )
            )
            continue

        # 观察/条件句优先于「有 expected 就 assert」
        if _is_observational(action_nl):
            dsl_steps.append(
                Step(action="screenshot", description=action_nl, expected=expected)
            )
            continue

        if _is_assert(action_nl, expected):
            dsl_steps.extend(_compile_assert_steps(action_nl, expected))
            continue

        # 兜底：截图留证
        dsl_steps.append(
            Step(action="screenshot", description=action_nl, expected=expected)
        )

    if len(dsl_steps) <= 1:
        dsl_steps.append(
            Step(action="screenshot", description=title or "用例执行留证", expected="")
        )

    return ExecScript(
        case_id=case_id,
        name=case_id[:8],
        title=title,
        preconditions=preconditions,
        test_point_id=str(case.get("test_point_id") or ""),
        steps=dsl_steps,
    )
