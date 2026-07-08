"""测试点覆盖率校验单测 — 含 RA-0011 截断尾部场景。"""

from __future__ import annotations

from src.services.testpoint_coverage import (
    renumber_test_points,
    split_fr_batches,
    validate_testpoint_coverage,
)


def _sample_fr(fr_id: str, priority: str = "P2") -> dict:
    return {"id": fr_id, "title": fr_id, "priority": priority}


def _sample_nfr(nfr_id: str) -> dict:
    return {"id": nfr_id, "title": nfr_id}


def _tp(tp_id: str, related: str) -> dict:
    return {"id": tp_id, "title": tp_id, "related_fr": related}


def test_truncated_tail_tp083_fails_full_coverage():
    """RA-0011：仅 TP-083 起 15 条，应判定失败。"""
    fr_list = [_sample_fr(f"FR-{i:03d}", "P0" if i <= 5 else "P2") for i in range(1, 24)]
    nfr_list = [_sample_nfr(f"NFR-{i:03d}") for i in range(1, 12)]
    tps = [_tp(f"TP-{i:03d}", "FR-023") for i in range(83, 98)]
    for j, nfr in enumerate(nfr_list):
        tps[j]["related_fr"] = nfr["id"]

    analysis = {
        "functional_requirements": fr_list,
        "non_functional_requirements": nfr_list,
        "test_points": tps,
    }
    report = validate_testpoint_coverage(analysis, require_full=True)
    assert report.ok is False
    assert report.min_id == 83
    assert any("TP-001" in e or "TP-083" in e for e in report.errors)
    assert len(report.missing_fr) >= 20


def test_renumber_test_points():
    tps = [_tp("TP-083", "FR-001"), _tp("TP-084", "FR-002")]
    out = renumber_test_points(tps)
    assert [t["id"] for t in out] == ["TP-001", "TP-002"]


def test_batch_subset_passes_when_fr_covered():
    fr_batch = [_sample_fr("FR-001", "P0"), _sample_fr("FR-002", "P1")]
    tps = []
    for fr in fr_batch:
        for i in range(4):
            tps.append(_tp(f"TP-{len(tps)+1:03d}", fr["id"]))
    batch = {
        "functional_requirements": fr_batch,
        "non_functional_requirements": [],
        "test_points": tps,
    }
    report = validate_testpoint_coverage(
        batch,
        require_full=False,
        fr_ids=["FR-001", "FR-002"],
        nfr_ids=[],
    )
    assert report.ok is True


def test_split_fr_batches():
    fr_list = [_sample_fr(f"FR-{i:03d}") for i in range(1, 10)]
    batches = split_fr_batches(fr_list, 4)
    assert len(batches) == 3
    assert len(batches[0]) == 4
    assert len(batches[-1]) == 1
