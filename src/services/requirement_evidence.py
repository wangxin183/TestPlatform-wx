"""需求分析结果校验 — FR 范围与 source_evidence 原文锚定。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


_HEADING_RE = re.compile(r"^#{2,6}\s+(.+?)\s*$", re.MULTILINE)
_HEADING_NUMBER_RE = re.compile(
    r"^(?:第?[一二三四五六七八九十百]+[、.．]\s*|\d+(?:\.\d+)*[、.．]?\s*)"
)
_EVIDENCE_QUOTE_RE = re.compile(
    r"原文摘录[：:]\s*(.+)$",
)


def extract_requirement_modules(doc_md: str) -> list[str]:
    """从全文任意层级模块标题提取模块名，不依赖固定章节编号。"""
    modules: list[str] = []
    for raw_heading in _HEADING_RE.findall(doc_md):
        heading = _HEADING_NUMBER_RE.sub("", raw_heading.strip())
        if not heading.endswith("模块"):
            continue
        name = heading[: -len("模块")].strip()
        if name and name not in modules:
            modules.append(name)
    return modules


def extract_chapter3_modules(doc_md: str) -> list[str]:
    """兼容旧调用；现已改为动态解析全文模块标题。"""
    return extract_requirement_modules(doc_md)


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
    rejected_nfr: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"允许模块={len(self.allowed_modules)}, "
            f"驳回FR={len(self.rejected_fr)}, "
            f"驳回NFR={len(self.rejected_nfr)}, "
            f"错误={len(self.errors)}"
        )


def validate_analysis_scope(
    doc_md: str,
    analysis_json: dict,
) -> AnalysisScopeReport:
    """校验 FR 模块范围 + 原文依据，剔除越界/无锚点项。"""
    allowed = extract_requirement_modules(doc_md)
    report = AnalysisScopeReport(ok=True, allowed_modules=allowed)
    fr_list = analysis_json.get("functional_requirements") or []

    for fr in fr_list:
        if not isinstance(fr, dict):
            continue
        fr_id = fr.get("id", "?")
        module = fr.get("module", "")
        if not str(module or "").strip():
            report.rejected_fr.append(fr)
            report.errors.append(f"{fr_id} 缺少 module")
            continue
        if allowed and not module_matches_fr(module, allowed):
            report.rejected_fr.append(fr)
            report.errors.append(
                f"{fr_id} 模块「{module}」不在文档动态解析范围：{allowed}"
            )
            continue
        ok, reason = evidence_quotes_in_doc(fr, doc_md)
        if not ok:
            report.rejected_fr.append(fr)
            report.errors.append(f"{fr_id} {reason}")

    for nfr in analysis_json.get("non_functional_requirements") or []:
        if not isinstance(nfr, dict):
            continue
        nfr_id = nfr.get("id", "?")
        ok, reason = evidence_quotes_in_doc(nfr, doc_md)
        if not ok:
            report.rejected_nfr.append(nfr)
            report.errors.append(f"{nfr_id} {reason}")

    if report.rejected_fr or report.rejected_nfr:
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
    rejected_nfr_ids = {nfr.get("id") for nfr in report.rejected_nfr}
    out = dict(analysis_json)
    out["functional_requirements"] = [
        fr
        for fr in (analysis_json.get("functional_requirements") or [])
        if isinstance(fr, dict) and fr.get("id") not in rejected_ids
    ]
    out["non_functional_requirements"] = [
        nfr
        for nfr in (analysis_json.get("non_functional_requirements") or [])
        if isinstance(nfr, dict) and nfr.get("id") not in rejected_nfr_ids
    ]
    notes = out.get("analysis_notes")
    if not isinstance(notes, dict):
        notes = {}
        out["analysis_notes"] = notes
    missing = notes.get("missing_aspects")
    if not isinstance(missing, list):
        missing = []
    removed = [
        *sorted(rejected_ids - {None}),
        *sorted(rejected_nfr_ids - {None}),
    ]
    if removed:
        missing.append(
            "以下需求因模块越界或缺少可验证原文依据被平台剔除："
            + ", ".join(removed)
        )
    notes["missing_aspects"] = missing
    return out, report
