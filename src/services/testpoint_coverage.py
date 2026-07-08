"""测试点覆盖率校验 — 检测截断尾部、FR/NFR 漏覆盖、数量不足。"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field


_TP_ID_RE = re.compile(r"^TP-(\d+)$", re.IGNORECASE)


def parse_tp_id_num(tp_id: str) -> int | None:
    if not tp_id:
        return None
    m = _TP_ID_RE.match(str(tp_id).strip())
    return int(m.group(1)) if m else None


def min_tp_per_fr(priority: str) -> int:
    return 4 if priority in ("P0", "P1") else 2


@dataclass
class TestPointCoverageReport:
    ok: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tp_count: int = 0
    fr_count: int = 0
    nfr_count: int = 0
    covered_fr: list[str] = field(default_factory=list)
    missing_fr: list[str] = field(default_factory=list)
    covered_nfr: list[str] = field(default_factory=list)
    missing_nfr: list[str] = field(default_factory=list)
    min_id: int | None = None
    max_id: int | None = None

    def summary(self) -> str:
        parts = [
            f"TP={self.tp_count}",
            f"FR覆盖={len(self.covered_fr)}/{self.fr_count}",
            f"NFR覆盖={len(self.covered_nfr)}/{self.nfr_count}",
        ]
        if self.min_id is not None:
            parts.append(f"ID范围=TP-{self.min_id:03d}~TP-{self.max_id:03d}")
        return ", ".join(parts)


def validate_testpoint_coverage(
    analysis_json: dict,
    *,
    require_full: bool = True,
    fr_ids: list[str] | None = None,
    nfr_ids: list[str] | None = None,
) -> TestPointCoverageReport:
    """校验 test_points 是否满足 SKILL 覆盖率规则。

    Args:
        analysis_json: 含 functional_requirements / non_functional_requirements / test_points
        require_full: True 时要求覆盖全部 FR/NFR；False 时仅校验 fr_ids/nfr_ids 子集
        fr_ids: 子集校验时的 FR id 列表
        nfr_ids: 子集校验时的 NFR id 列表
    """
    fr_list = analysis_json.get("functional_requirements") or []
    nfr_list = analysis_json.get("non_functional_requirements") or []
    tps = analysis_json.get("test_points") or []

    report = TestPointCoverageReport(
        tp_count=len(tps),
        fr_count=len(fr_list),
        nfr_count=len(nfr_list),
    )

    if not isinstance(tps, list):
        report.errors.append("test_points 不是数组")
        report.ok = False
        return report

    fr_map = {f.get("id"): f for f in fr_list if f.get("id")}
    nfr_ids_all = [n.get("id") for n in nfr_list if n.get("id")]
    target_fr = fr_ids if fr_ids is not None else list(fr_map.keys())
    target_nfr = nfr_ids if nfr_ids is not None else nfr_ids_all

    related_counts: Counter[str] = Counter()
    id_nums: list[int] = []
    for tp in tps:
        if not isinstance(tp, dict):
            report.errors.append("存在非对象的 test_point 条目")
            continue
        rid = str(tp.get("related_fr") or "").strip()
        if rid:
            related_counts[rid] += 1
        num = parse_tp_id_num(str(tp.get("id") or ""))
        if num is not None:
            id_nums.append(num)
        else:
            report.warnings.append(f"无法解析 TP id: {tp.get('id')}")

    if id_nums:
        report.min_id = min(id_nums)
        report.max_id = max(id_nums)
        if require_full and report.min_id > 1:
            report.errors.append(
                f"测试点 ID 未从 TP-001 起始（最小 TP-{report.min_id:03d}），"
                "疑似 Agent 输出截断，仅保留了尾部 JSON"
            )
        if require_full and len(id_nums) != len(set(id_nums)):
            report.errors.append("存在重复的测试点 ID")

    for fr_id in target_fr:
        fr = fr_map.get(fr_id)
        if not fr:
            continue
        cnt = related_counts.get(fr_id, 0)
        need = min_tp_per_fr(fr.get("priority", "P2"))
        if cnt < need:
            report.missing_fr.append(fr_id)
            report.errors.append(
                f"{fr_id} 仅有 {cnt} 条 TP，要求至少 {need} 条（优先级 {fr.get('priority', 'P2')}）"
            )
        else:
            report.covered_fr.append(fr_id)

    for nfr_id in target_nfr:
        cnt = related_counts.get(nfr_id, 0)
        if cnt < 1:
            report.missing_nfr.append(nfr_id)
            report.errors.append(f"{nfr_id} 缺少对应测试点")
        else:
            report.covered_nfr.append(nfr_id)

    if require_full and fr_list and nfr_list:
        total_req = len(fr_list) + len(nfr_list)
        if len(tps) <= total_req:
            report.errors.append(
                f"测试点总数 {len(tps)} 未大于 FR+NFR 数 {total_req}，覆盖率不足"
            )

    report.ok = len(report.errors) == 0
    return report


def renumber_test_points(test_points: list[dict]) -> list[dict]:
    """将 test_points 的 id 重排为 TP-001..TP-N（保持原顺序）。"""
    out: list[dict] = []
    for i, tp in enumerate(test_points, start=1):
        if not isinstance(tp, dict):
            continue
        item = dict(tp)
        item["id"] = f"TP-{i:03d}"
        out.append(item)
    return out


def split_fr_batches(fr_list: list[dict], batch_size: int = 4) -> list[list[dict]]:
    if not fr_list:
        return []
    batches: list[list[dict]] = []
    for i in range(0, len(fr_list), batch_size):
        batches.append(fr_list[i : i + batch_size])
    return batches
