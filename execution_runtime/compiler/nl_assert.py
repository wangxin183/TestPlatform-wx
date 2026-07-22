"""NL 期望结果中的正/负向文案语义。

只认「」等引号包裹的文案；不维护产品 UI 白名单，不在无引号时猜测文案。
"""

from __future__ import annotations

import re

# 引号前短窗口内出现这些，视为「不应出现」
_ABSENT_WINDOW_RE = re.compile(
    r"(?:"
    r"不出现|不展示|不显示|不可见|不见|"
    r"不应(?:该)?(?:出现|展示|显示|可见)?|"
    r"不能(?:出现|展示|显示|可见)?|"
    r"不要(?:出现|展示|显示|可见)?|"
    r"未(?:出现|展示|显示|可见)|"
    r"没有(?:出现|展示|显示|可见)?|"
    r"无需(?:出现|展示|显示|可见)?|"
    r"不得(?:出现|展示|显示|可见)?|"
    r"禁止(?:出现|展示|显示|可见)?|"
    r"勿(?:出现|展示|显示)?"
    r")$"
)


def quoted_values(text: str) -> list[str]:
    return [
        value.strip()
        for value in re.findall(r"[「『\"']([^」』\"']+)[」』\"']", text or "")
        if value.strip()
    ]


def absent_quoted_values(expected: str) -> list[str]:
    """从 expected 提取「不应出现」的引号文案（去重保序）。"""
    text = expected or ""
    found: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"[「『\"']([^」』\"']+)[」』\"']", text):
        value = match.group(1).strip()
        if not value or value in seen:
            continue
        window = text[max(0, match.start() - 16) : match.start()]
        compact = re.sub(r"\s+", "", window)
        if _ABSENT_WINDOW_RE.search(compact):
            found.append(value)
            seen.add(value)
    return found


def visible_quoted_values(expected: str) -> list[str]:
    """正向可见文案 = 全部引号文案 − 负向文案。"""
    absent = set(absent_quoted_values(expected))
    return [value for value in quoted_values(expected) if value not in absent]


def objective_text_signals(expected: str) -> tuple[list[str], list[str]]:
    """仅从引号文案提取 (visible, absent)。"""
    return list(visible_quoted_values(expected)), list(absent_quoted_values(expected))
