"""需求文档净化 — 检测并剥离 ChatGPT 污染、重复正文。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ChatGPT / AI 代写对话常见残留
POLLUTION_PATTERNS: list[tuple[str, str]] = [
    (r"将需求文档转换成\s*word", "检测到「转换成 Word」类 AI 对话残留"),
    (r"我没办法直接生成.*\.docx", "检测到「无法生成 docx」类 AI 对话残留"),
    (r"粘贴到\s*Microsoft\s*Word", "检测到 Word 粘贴引导类 AI 对话残留"),
    (r"标准\s*Word\s*排版格式", "检测到 AI 排版说明残留"),
]


@dataclass
class DocumentSanitizeReport:
    original_chars: int = 0
    sanitized_chars: int = 0
    contamination_found: bool = False
    warnings: list[str] = field(default_factory=list)
    blocked: bool = False
    block_reason: str = ""


def detect_contamination(text: str) -> list[str]:
    """返回命中的污染类型说明（空列表表示未检出）。"""
    hits: list[str] = []
    for pattern, label in POLLUTION_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            hits.append(label)
    return hits


def _find_main_title(text: str) -> str | None:
    m = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    return m.group(1).strip() if m else None


def _count_main_title(text: str, title: str) -> int:
    esc = re.escape(title)
    return len(re.findall(rf"^#\s+{esc}\s*$", text, flags=re.MULTILINE))


def sanitize_requirement_markdown(text: str) -> tuple[str, DocumentSanitizeReport]:
    """净化需求 Markdown，必要时截断污染段并去重。"""
    report = DocumentSanitizeReport(original_chars=len(text))
    cleaned = text.strip()
    warnings = detect_contamination(cleaned)
    if warnings:
        report.contamination_found = True
        report.warnings.extend(warnings)

    # 1) 截断首个 AI 对话污染点及其后内容（常见为「前半截断稿 + 对话 + ChatGPT 扩写全文」）
    cut_pos = len(cleaned)
    for pattern, label in POLLUTION_PATTERNS:
        m = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if m and m.start() < cut_pos:
            cut_pos = m.start()
    if cut_pos < len(cleaned):
        cleaned = cleaned[:cut_pos].rstrip()
        report.warnings.append(
            f"已在字符 {cut_pos} 处截断 AI 污染段（原长 {report.original_chars}）"
        )

    # 2) 若主标题重复出现，仅保留首份正文（去掉 ChatGPT 粘贴的第二份 PRD）
    title = _find_main_title(cleaned)
    if title and _count_main_title(cleaned, title) > 1:
        parts = re.split(rf"(?=^#\s+{re.escape(title)}\s*$)", cleaned, flags=re.MULTILINE)
        parts = [p for p in parts if p.strip()]
        if len(parts) > 1:
            cleaned = parts[0].rstrip()
            report.contamination_found = True
            report.warnings.append("检测到重复 PRD 标题，已丢弃后续重复正文")

    # 3) 截断后若仍含污染关键词，标记为阻断
    residual = detect_contamination(cleaned)
    if residual:
        report.blocked = True
        report.block_reason = (
            "文档仍含 AI 对话/扩写污染，请删除 Word 中 ChatGPT 对话与粘贴的第二份正文后重新上传。"
            + " 详情：" + "；".join(residual)
        )

    report.sanitized_chars = len(cleaned)
    return cleaned, report


def validate_upload_document(text: str) -> DocumentSanitizeReport:
    """上传前校验：检出污染或重复正文则阻断。"""
    report = DocumentSanitizeReport(original_chars=len(text))
    hits = detect_contamination(text)
    if hits:
        report.contamination_found = True
        report.warnings.extend(hits)
        report.blocked = True
        report.block_reason = (
            "需求文档含 AI 对话或 ChatGPT 扩写污染，无法保证分析范围正确。"
            " 请删除 Word 中的对话记录与第二份粘贴正文，仅保留真实 PRD 后重新上传。"
            f"（{ '；'.join(hits)}）"
        )
        return report

    title = _find_main_title(text)
    if title and _count_main_title(text, title) > 1:
        report.contamination_found = True
        report.blocked = True
        report.block_reason = (
            f"检测到重复 PRD 标题「{title}」，通常由 ChatGPT 粘贴第二份正文导致。"
            " 请只保留一份真实需求正文后重新上传。"
        )
    return report
