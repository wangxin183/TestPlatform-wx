"""需求分析结果校验 — FR 范围与 source_evidence 原文锚定。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


_CH3_MODULE_RE = re.compile(
    r"^###\s+3\.(\d+)\s+(.+?)模块\s*$",
    re.MULTILINE,
)
_EVIDENCE_QUOTE_RE = re.compile(
    r"原文摘录[：:]\s*(.+)$",
)


def extract_chapter3_modules(doc_md: str) -> list[str]:
    """从第三章标题提取允许的功能模块名（不含「模块」后缀）。"""
    modules: list[str] = []
    for _num, name in _CH3_MODULE_RE.findall(doc_md):
        name = name.strip()
        if name and name not in modules:
            modules.append(name)
    return modules


def _normalize_module(name: str) -> str:
    n = (name or "").strip().replace("模块", "")
    return re.sub(r"\s+", "", n)


def module_matches_fr(fr_module: str, allowed_modules: list[str]) -> bool:
    fr_norm = _normalize_module(fr_module)
    if not fr_norm:
        return False
    for allowed in allowed_modules:
        a_norm = _normalize_module(allowed)
        if fr_norm == a_norm or fr_norm in a_norm or a_norm in fr_norm:
            return True
    return False


def evidence_quotes_in_doc(fr: dict, doc_md: str) -> tuple[bool, str]:
    """校验 FR 的 source_evidence 是否在原文中出现。"""
    evidences = fr.get("source_evidence") or []
    if not evidences:
        return False, "缺少 source_evidence"

    doc_compact = re.sub(r"\s+", "", doc_md)
    for raw in evidences:
        text = str(raw).strip()
        m = _EVIDENCE_QUOTE_RE.search(text)
        quote = m.group(1).strip() if m else text
        quote = quote.strip("\"'""''")
        if len(quote) < 6:
            continue
        quote_compact = re.sub(r"\s+", "", quote)
        if quote_compact and quote_compact in doc_compact:
            return True, ""
        if quote in doc_md:
            return True, ""
    return False, "source_evidence 摘录无法在原文中匹配"


@dataclass
class AnalysisScopeReport:
    ok: bool
    allowed_modules: list[str] = field(default_factory=list)
    rejected_fr: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"允许模块={len(self.allowed_modules)}, "
            f"驳回FR={len(self.rejected_fr)}, "
            f"错误={len(self.errors)}"
        )


def validate_analysis_scope(
    doc_md: str,
    analysis_json: dict,
) -> AnalysisScopeReport:
    """校验 FR 模块范围 + 原文依据，剔除越界/无锚点项。"""
    allowed = extract_chapter3_modules(doc_md)
    report = AnalysisScopeReport(ok=True, allowed_modules=allowed)
    fr_list = analysis_json.get("functional_requirements") or []

    if not allowed:
        report.ok = False
        report.errors.append(
            "未在文档第三章解析到「### 3.x xxx模块」标题，无法界定 FR 范围"
        )
        return report

    for fr in fr_list:
        if not isinstance(fr, dict):
            continue
        fr_id = fr.get("id", "?")
        module = fr.get("module", "")
        if not module_matches_fr(module, allowed):
            report.rejected_fr.append(fr)
            report.errors.append(
                f"{fr_id} 模块「{module}」不在文档第三章允许范围：{allowed}"
            )
            continue
        ok, reason = evidence_quotes_in_doc(fr, doc_md)
        if not ok:
            report.rejected_fr.append(fr)
            report.errors.append(f"{fr_id} {reason}")

    if report.rejected_fr:
        report.ok = False
    return report


def filter_analysis_to_scope(
    doc_md: str,
    analysis_json: dict,
) -> tuple[dict, AnalysisScopeReport]:
    """返回剔除越界 FR 后的 analysis_json 副本。"""
    report = validate_analysis_scope(doc_md, analysis_json)
    if report.ok:
        return analysis_json, report

    rejected_ids = {fr.get("id") for fr in report.rejected_fr}
    out = dict(analysis_json)
    out["functional_requirements"] = [
        fr
        for fr in (analysis_json.get("functional_requirements") or [])
        if isinstance(fr, dict) and fr.get("id") not in rejected_ids
    ]
    notes = out.get("analysis_notes")
    if not isinstance(notes, dict):
        notes = {}
        out["analysis_notes"] = notes
    missing = notes.get("missing_aspects")
    if not isinstance(missing, list):
        missing = []
    missing.append(
        "以下 FR 因模块越界或缺少可验证原文依据被平台剔除："
        + ", ".join(sorted(rejected_ids - {None}))
    )
    notes["missing_aspects"] = missing
    return out, report
