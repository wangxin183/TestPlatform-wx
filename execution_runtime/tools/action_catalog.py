"""确定性执行器与 Agent 共用的动作工具 Schema。"""

from __future__ import annotations

from typing import Any


def _param(required: bool, description: str) -> dict[str, Any]:
    return {"required": required, "description": description}


ACTION_CATALOG: dict[str, dict[str, Any]] = {
    "observe_page": {
        "description": "读取当前 Activity、UI 树元素和页面指纹",
        "read_only": True,
        "parameters": {},
    },
    "inspect_elements": {
        "description": "按文本或 resource-id 筛选当前页面元素",
        "read_only": True,
        "parameters": {"query": _param(True, "筛选关键词")},
    },
    "get_current_activity": {
        "description": "读取当前 package/activity",
        "read_only": True,
        "parameters": {},
    },
    "launch_app": {
        "description": "激活被测 App",
        "read_only": False,
        "parameters": {},
    },
    "terminate_app": {
        "description": "终止被测 App",
        "read_only": False,
        "parameters": {},
    },
    "tap": {
        "description": "点击唯一、可见且启用的元素",
        "read_only": False,
        "parameters": {"locator": _param(True, "DSL locator")},
    },
    "tap_xy": {
        "description": "按屏幕像素坐标点击（用于验证码等无控件目标）",
        "read_only": False,
        "parameters": {
            "x": _param(True, "屏幕 X 像素"),
            "y": _param(True, "屏幕 Y 像素"),
        },
    },
    "input": {
        "description": "向输入控件输入文本",
        "read_only": False,
        "parameters": {
            "locator": _param(True, "DSL locator"),
            "value": _param(True, "输入值"),
        },
    },
    "clear": {
        "description": "清空输入控件",
        "read_only": False,
        "parameters": {"locator": _param(True, "DSL locator")},
    },
    "swipe": {
        "description": "按明确方向滑动页面",
        "read_only": False,
        "parameters": {
            "direction": _param(True, "up/down/left/right"),
            "ratio": _param(False, "屏幕滑动比例"),
            "times": _param(False, "重复次数"),
        },
    },
    "scroll": {
        "description": "滚动页面直到目标出现或次数耗尽",
        "read_only": False,
        "parameters": {
            "direction": _param(True, "up/down/left/right"),
            "until": _param(False, "结束 locator"),
        },
    },
    "back": {
        "description": "返回上一页（单次）。卡在阅读器/弹层或定位失败时优先用 recover_page",
        "read_only": False,
        "parameters": {},
    },
    "recover_page": {
        "description": (
            "页面恢复工具：定位失败或页面卡住时调用。"
            "先回退最多 max_backs 次；若仍找不到 until，可杀掉 App 重新拉起。"
            "用例执行全程（含登录/搜索前置与 Agent 步骤）均可使用。"
        ),
        "read_only": False,
        "parameters": {
            "until": _param(
                False,
                "期望出现的 locator（如 {type:text,value:我的}）；省略则只回退",
            ),
            "max_backs": _param(False, "最多回退次数，默认 3"),
            "relaunch": _param(
                False,
                "回退仍失败时是否 terminate+launch；有 until 默认 true，无 until 默认 false",
            ),
            "timeout": _param(False, "每次探测 until 的超时秒数，默认 2"),
        },
    },
    "wait": {
        "description": "等待元素出现或等待指定秒数",
        "read_only": False,
        "parameters": {
            "until": _param(False, "等待 locator"),
            "timeout": _param(False, "超时秒数"),
        },
    },
    "assert_visible": {
        "description": "断言元素可见",
        "read_only": True,
        "parameters": {"locator": _param(True, "DSL locator")},
    },
    "assert_text": {
        "description": "断言页面或元素包含指定文本（正向可见）",
        "read_only": True,
        "parameters": {
            "value": _param(True, "期望文本"),
            "locator": _param(False, "可选 DSL locator"),
        },
    },
    "assert_text_absent": {
        "description": "断言页面不包含指定文本（负向：不出现/不展示）",
        "read_only": True,
        "parameters": {
            "value": _param(True, "不应出现的文本"),
            "locator": _param(False, "可选 DSL locator"),
        },
    },
    "screenshot": {
        "description": "记录当前页面截图",
        "read_only": True,
        "parameters": {},
    },
}

HIGH_RISK_KEYWORDS = ("支付", "购买", "删除", "发布", "发帖", "注销", "提交订单")


def tool_schemas_for_prompt() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": spec["description"],
            "read_only": spec["read_only"],
            "parameters": spec["parameters"],
        }
        for name, spec in ACTION_CATALOG.items()
    ]
