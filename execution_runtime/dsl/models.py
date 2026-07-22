"""执行 DSL 数据模型（pydantic v2）。

编译层把「给人看」的 NL 用例翻译成这套「给机器执行」的结构化脚本，
执行引擎按此确定性执行，执行阶段不再调 LLM。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# 第一阶段 App UI 动作集
ACTIONS = (
    "launch_app",
    "terminate_app",
    "tap",
    "tap_xy",
    "input",
    "clear",
    "swipe",
    "scroll",
    "back",
    "wait",
    "assert_visible",
    "assert_text",
    "assert_text_absent",
    "screenshot",
)

# iOS / Android 定位策略（强 → 弱），ocr_text 为兜底
LOCATOR_TYPES = (
    "accessibility_id",
    "name",          # iOS name/label；Android textContains
    "predicate",     # iOS -ios predicate string
    "class_chain",   # iOS class chain；Android 部分动作降级 xpath
    "xpath",
    "id",            # Android resource-id
    "text",          # Android 精确文本
    "uiautomator",   # Android UiSelector 表达式
    "ocr_text",
)

ActionType = Literal[
    "launch_app", "terminate_app", "tap", "tap_xy", "input", "clear",
    "swipe", "scroll", "back", "wait",
    "assert_visible", "assert_text", "assert_text_absent", "screenshot",
]

LocatorType = Literal[
    "accessibility_id", "name", "predicate", "class_chain", "xpath",
    "id", "text", "uiautomator", "ocr_text",
]

Direction = Literal["up", "down", "left", "right"]

# 需要 locator 的动作
_NEEDS_LOCATOR = {"tap", "input", "clear", "assert_visible"}


class Locator(BaseModel):
    type: LocatorType
    value: str = Field(..., min_length=1)


class Step(BaseModel):
    action: ActionType
    # 原始 NL 步骤描述，仅用于报告展示与自愈上下文
    description: str = ""
    expected: str = ""

    locator: Optional[Locator] = None
    value: Optional[str] = None            # input 的输入值 / assert_text 的期望文本
    # 手势参数
    direction: Optional[Direction] = None
    ratio: float = 0.6                     # 滑动幅度占屏比例
    times: int = 1                         # swipe 重复次数上限
    # 等待
    until: Optional[Locator] = None        # wait / swipe until 出现该元素
    timeout: Optional[int] = None          # 秒；None 用全局 step_timeout
    # tap_xy 屏坐标（像素）
    x: Optional[int] = None
    y: Optional[int] = None

    @model_validator(mode="after")
    def _check_required(self) -> "Step":
        if self.action in _NEEDS_LOCATOR and self.locator is None:
            raise ValueError(f"动作 {self.action} 必须提供 locator")
        if self.action == "tap_xy" and (self.x is None or self.y is None):
            raise ValueError("动作 tap_xy 必须提供 x/y")
        if self.action == "input" and (self.value is None or self.value == ""):
            raise ValueError("动作 input 必须提供 value")
        if self.action == "assert_text" and (self.value is None or self.value == ""):
            raise ValueError("动作 assert_text 必须提供 value（期望文本）")
        if self.action == "assert_text_absent" and (
            self.value is None or self.value == ""
        ):
            raise ValueError("动作 assert_text_absent 必须提供 value（不应出现的文本）")
        return self


class ExecScript(BaseModel):
    case_id: str
    name: str = ""
    title: str = ""
    preconditions: str = ""
    test_point_id: str = ""
    module: str = ""
    execution_mode: Literal["deterministic", "hybrid", "agent"] = "deterministic"
    module_setup: list[Step] = Field(default_factory=list)
    step_contracts: list[dict[str, Any]] = Field(default_factory=list)
    precondition_spec: dict[str, Any] = Field(default_factory=dict)
    steps: list[Step] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_steps(self) -> "ExecScript":
        if not self.steps:
            raise ValueError("ExecScript.steps 不能为空")
        return self

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecScript":
        return cls.model_validate(data)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)
