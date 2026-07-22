"""execution_runtime 平台桥接服务单测。"""

from __future__ import annotations

from execution_runtime.compiler.compiler import _script_has_platform_mismatch
from execution_runtime.config import RuntimeConfig, TargetApp
from src.services.execution_runtime_service import (
    _EVENT_PROGRESS,
    _group_cases_by_module,
    _parse_runtime_log,
)


def test_event_progress_map():
    assert _EVENT_PROGRESS["run_completed"] == 100
    assert _EVENT_PROGRESS["pytest_start"] == 48


def test_parse_runtime_log_missing():
    prog, step, err = _parse_runtime_log("EXE-NOT-EXIST")
    assert prog == 0
    assert step == ""
    assert err == ""


def test_platform_mismatch_android():
    cfg = RuntimeConfig(target_app=TargetApp(platform="android"))
    data = {
        "steps": [
            {"action": "tap", "locator": {"type": "class_chain", "value": "**/XCUIElementTypeButton"}}
        ]
    }
    assert _script_has_platform_mismatch(data, cfg) is True


def test_group_cases_by_module_is_stable():
    cases = [
        {"case_id": "s1", "module": "搜索"},
        {"case_id": "r1", "module": "漫画阅读器"},
        {"case_id": "s2", "module": "搜索"},
        {"case_id": "x", "module": ""},
    ]
    assert [c["case_id"] for c in _group_cases_by_module(cases)] == [
        "s1",
        "s2",
        "r1",
        "x",
    ]
