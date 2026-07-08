"""落盘 JSON 回收：agent 写文件代答时的兜底解析。"""

from __future__ import annotations

from pathlib import Path

from src.agent_runtime.cli_shared import extract_json, recover_json_from_workdir


def test_recover_json_from_workdir_reads_test_points(tmp_path: Path) -> None:
    payload = {
        "test_points": [
            {
                "id": "TP-001",
                "related_fr": "FR-001",
                "scenario": "登录主流程",
                "test_type": "ui",
                "priority": "P0",
                "positive_scenarios": ["ok"],
                "boundary_conditions": [],
                "negative_scenarios": [],
                "permission_scenarios": [],
            }
        ]
    }
    (tmp_path / "test_points_output.json").write_text(
        __import__("json").dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    raw = "已生成 1 条测试点，完整 JSON 已写入 `test_points_output.json`。"
    assert not extract_json(raw).success

    recovered = recover_json_from_workdir(
        tmp_path,
        raw_output=raw,
        preferred_names=["test_points_output.json"],
        require_key="test_points",
    )
    assert recovered.success
    assert recovered.extract_method.startswith("artifact_file:")
    assert len(recovered.data["test_points"]) == 1


def test_recover_unwraps_corrected_output_wrapper(tmp_path: Path) -> None:
    wrapped = {
        "diagnosis": {"root_cause": "stdout 无 JSON", "failure_category": "other"},
        "corrected_output": {"test_points": [{"id": "TP-001"}]},
    }
    (tmp_path / "self_heal_corrected_output_compact.json").write_text(
        __import__("json").dumps(wrapped, ensure_ascii=False),
        encoding="utf-8",
    )
    recovered = recover_json_from_workdir(
        tmp_path,
        raw_output="完整修正 JSON 已写入 self_heal_corrected_output_compact.json",
        preferred_names=["self_heal_corrected_output_compact.json"],
        require_key="test_points",
    )
    assert recovered.success
    assert "test_points" in recovered.data
    assert recovered.data["test_points"][0]["id"] == "TP-001"


def test_recover_ra0010_fixture() -> None:
    """用真实 RA-0010 落盘件验证恢复路径（目录存在时）。"""
    workdir = Path("storage/requirement_analyses/RA-0010")
    if not (workdir / "test_points_output.json").exists():
        return
    raw = (workdir / "testpoint_raw_output.txt").read_text(encoding="utf-8")
    recovered = recover_json_from_workdir(
        workdir,
        raw_output=raw,
        preferred_names=["test_points_output.json"],
        require_key="test_points",
    )
    assert recovered.success
    assert len(recovered.data["test_points"]) >= 50
