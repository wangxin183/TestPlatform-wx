"""缺陷记录：执行中发现 bug（断言失败 / crash / 自愈无效）自动落盘。

第一阶段仅写 run_dir/defects.json；平台对接（P3）再读此文件落库到 Defect 表。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from execution_runtime.models.result import CaseResult, StepOutcome

# 断言失败 → medium；crash/broken → high
_SEVERITY_BY_OUTCOME = {
    StepOutcome.FAILED: "medium",
    StepOutcome.BROKEN: "high",
}


def build_defect(case: CaseResult) -> dict[str, Any]:
    failed_step = next(
        (s for s in case.steps if s.outcome in (StepOutcome.FAILED, StepOutcome.BROKEN)),
        None,
    )
    evidence = []
    if failed_step:
        for p in (failed_step.screenshot_after, failed_step.screenshot_before, failed_step.page_source):
            if p:
                evidence.append(p)
    return {
        "defect_id": f"DFT-{uuid.uuid4().hex[:8]}",
        "case_id": case.case_id,
        "title": f"[{case.title or case.case_id}] 执行失败",
        "test_point_id": case.test_point_id,
        "severity": _SEVERITY_BY_OUTCOME.get(case.outcome, "medium"),
        "failure_type": case.outcome.value,
        "reproduction_steps": [
            {
                "step": s.step_no,
                "action": s.action,
                "description": s.description,
                "expected": s.expected,
                "outcome": s.outcome.value,
            }
            for s in case.steps
        ],
        "expected": failed_step.expected if failed_step else "",
        "actual": case.error,
        "evidence_paths": evidence,
        "status": "open",
    }


def append_defect(run_dir: Path, defect: dict[str, Any]) -> None:
    path = run_dir / "defects.json"
    existing: list[dict[str, Any]] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []
    existing.append(defect)
    path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )
