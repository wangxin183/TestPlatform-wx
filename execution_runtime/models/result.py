"""执行结果模型（纯 dataclass，便于序列化落盘 / 汇总）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class StepOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"      # 断言不通过 / 明确失败
    BROKEN = "broken"      # 执行异常（定位不到、session 断开等）
    SKIPPED = "skipped"


@dataclass
class StepRecord:
    step_no: int
    action: str
    description: str = ""
    expected: str = ""
    locator_type: str = ""
    locator_value: str = ""
    matched_by: str = ""            # 实际命中的定位策略
    outcome: StepOutcome = StepOutcome.PASSED
    error: str = ""
    started_at: str = ""
    ended_at: str = ""
    duration_ms: int = 0
    screenshot_before: str = ""
    screenshot_after: str = ""
    page_source: str = ""
    healed: bool = False
    heal_note: str = ""

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["outcome"] = self.outcome.value
        return d


@dataclass
class CaseResult:
    case_id: str
    title: str = ""
    test_point_id: str = ""
    outcome: StepOutcome = StepOutcome.PASSED
    error: str = ""
    started_at: str = ""
    ended_at: str = ""
    duration_ms: int = 0
    steps: list[StepRecord] = field(default_factory=list)
    healed_count: int = 0
    defect_id: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "test_point_id": self.test_point_id,
            "outcome": self.outcome.value,
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "healed_count": self.healed_count,
            "defect_id": self.defect_id,
            "steps": [s.as_dict() for s in self.steps],
        }
