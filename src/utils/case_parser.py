"""Test case parsers for JSON, Excel, Markdown, XMind formats.
Strategy: code-first parsing, AI fallback on failure.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any

from src.llm.caller import llm_call
from src.llm.types import LLMRequest
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

VALID_TEST_TYPES = {"ui", "api", "performance", "security", "compatibility"}
VALID_PRIORITIES = {"严重", "高", "中", "低"}

FIELD_DEFAULTS = {
    "priority": "中",
    "test_type": "ui",
    "tags": [],
    "platform_type": "",
    "description": "",
    "preconditions": "",
}


def _normalize_case(case: dict) -> dict:
    """Fill missing fields with defaults."""
    for key, default in FIELD_DEFAULTS.items():
        if key not in case or case[key] is None:
            case[key] = default
    if case.get("test_type") not in VALID_TEST_TYPES:
        case["test_type"] = "ui"
    if case.get("priority") not in VALID_PRIORITIES:
        case["priority"] = "中"
    if "steps" not in case or not case["steps"]:
        case["steps"] = [{"step": 1, "action": "", "expected": ""}]
    # Normalize steps format
    normalized_steps = []
    for i, s in enumerate(case.get("steps", [])):
        if isinstance(s, dict):
            normalized_steps.append({
                "step": s.get("step", i + 1),
                "action": s.get("action", ""),
                "expected": s.get("expected", ""),
            })
        elif isinstance(s, str):
            normalized_steps.append({"step": i + 1, "action": s, "expected": ""})
    case["steps"] = normalized_steps
    case.setdefault("title", f"用例_{id(case)}")
    return case


# ═══════════════════════════════════════════════════════════════
# JSON Parser
# ═══════════════════════════════════════════════════════════════

def _parse_json(raw_content: bytes | str) -> tuple[list[dict], str]:
    """Parse JSON test cases. No AI fallback for JSON."""
    if isinstance(raw_content, bytes):
        raw_content = raw_content.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw_content)
    except json.JSONDecodeError as e:
        return [], f"JSON 语法错误: {e}"

    if isinstance(data, list):
        cases = data
    elif isinstance(data, dict) and "cases" in data:
        cases = data["cases"]
    elif isinstance(data, dict):
        cases = [data]
    else:
        return [], "JSON 格式不正确，需要一个数组或包含 cases 字段的对象"

    if not cases:
        return [], "JSON 中没有找到用例数据"

    return [_normalize_case(c) for c in cases if isinstance(c, dict)], ""


# ═══════════════════════════════════════════════════════════════
# Excel Parser
# ═══════════════════════════════════════════════════════════════

def _parse_excel(file_bytes: bytes) -> tuple[list[dict], str]:
    """Parse Excel test cases by fixed column names."""
    try:
        import openpyxl
    except ImportError:
        return [], "缺少 openpyxl 依赖，无法解析 Excel"

    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True)
        ws = wb.active
    except Exception as e:
        return [], f"Excel 文件读取失败: {e}"

    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 2:
        return [], "Excel 文件至少需要表头行和一条数据"

    headers = [str(h).strip().lower() if h else "" for h in rows[0]]
    col_map = {
        "title": None, "description": None, "preconditions": None,
        "steps": None, "priority": None, "test_type": None,
        "tags": None, "platform_type": None,
    }
    for i, h in enumerate(headers):
        if h in col_map:
            col_map[h] = i

    if col_map["title"] is None:
        return [], "Excel 缺少 'title' 列"

    cases = []
    for row in rows[1:]:
        if not row or not row[col_map["title"]]:
            continue
        case: dict[str, Any] = {"title": str(row[col_map["title"]]).strip()}
        for key in col_map:
            if key == "title" or col_map[key] is None:
                continue
            idx = col_map[key]
            val = row[idx] if idx < len(row) else None
            if val is None:
                continue
            val = str(val).strip()
            if key == "steps":
                case["steps"] = _parse_excel_steps(val)
            elif key == "tags":
                case["tags"] = [t.strip() for t in val.split(",") if t.strip()]
            else:
                case[key] = val
        cases.append(_normalize_case(case))

    if not cases:
        return [], "未能从 Excel 中解析出任何用例"

    expected = len(rows) - 1  # minus header
    actual = len(cases)
    if actual < expected:
        return cases, f"仅解析出 {actual}/{expected} 条用例"

    return cases, ""


def _parse_excel_steps(raw: str) -> list[dict]:
    """Parse steps like '1.action→expected;2.action2→expected2'."""
    steps = []
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        # Extract step number
        m = re.match(r'^(\d+)[.、:]\s*(.+)', part)
        if m:
            num = int(m.group(1))
            content = m.group(2)
        else:
            num = len(steps) + 1
            content = part

        if "→" in content:
            action, expected = content.split("→", 1)
        else:
            action, expected = content, ""

        steps.append({
            "step": num,
            "action": action.strip(),
            "expected": expected.strip(),
        })
    return steps


# ═══════════════════════════════════════════════════════════════
# Markdown Parser
# ═══════════════════════════════════════════════════════════════

def _parse_markdown(text: str) -> tuple[list[dict], str]:
    """Parse markdown test cases using template format."""
    # Split by ## headings (case titles) and --- separators
    cases = []
    # First try: split by ## heading
    sections = re.split(r'\n(?=## )', text)
    if len(sections) <= 1:
        # Try split by --- separator
        sections = re.split(r'\n---\s*\n', text)
        if len(sections) <= 1:
            return [], "Markdown 格式无法识别：找不到 ## 标题或 --- 分隔符"

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # Extract title from ## heading
        title_match = re.match(r'^##\s+(.+)', section)
        title = title_match.group(1).strip() if title_match else ""

        # If no ## heading, use first non-empty line as title
        if not title:
            first_line = section.split("\n")[0].strip()
            title = first_line.lstrip("#").strip()

        case: dict[str, Any] = {"title": title}

        # Extract key-value pairs: - **Key**: Value
        field_pattern = r'-\s*\*\*(.+?)\*\*\s*[：:]\s*(.+)'
        for m in re.finditer(field_pattern, section):
            key = m.group(1).strip()
            val = m.group(2).strip()

            field_map = {
                "优先级": "priority", "类型": "test_type",
                "前置条件": "preconditions", "描述": "description",
                "标签": "tags", "平台": "platform_type",
                "用例类型": "test_type", "priority": "priority",
                "test_type": "test_type", "tags": "tags",
            }
            mapped = field_map.get(key, key)
            if mapped == "tags":
                case[mapped] = [t.strip() for t in val.split(",") if t.strip()]
            else:
                case[mapped] = val

        # Extract steps from ### 步骤 section
        steps_match = re.search(r'###\s*步骤\s*\n(.+?)(?=\n##|\n---|\Z)', section, re.DOTALL)
        steps = []
        if steps_match:
            steps_text = steps_match.group(1)
            step_lines = re.findall(r'\d+[.、:]\s*(.+?)(?=\n\d+[.、:]|\n$|\Z)', steps_text, re.DOTALL)
            for i, line in enumerate(step_lines):
                line = line.strip()
                if "→" in line:
                    action, expected = line.split("→", 1)
                else:
                    action, expected = line, ""
                steps.append({"step": i + 1, "action": action.strip(), "expected": expected.strip()})
        if steps:
            case["steps"] = steps

        if title or steps:
            cases.append(_normalize_case(case))

    if not cases:
        return [], "未能从 Markdown 中解析出任何用例（需要 ## 标题 或 --- 分隔符）"

    return cases, ""


# ═══════════════════════════════════════════════════════════════
# XMind Parser
# ═══════════════════════════════════════════════════════════════

def _parse_xmind(file_bytes: bytes) -> tuple[list[dict], str]:
    """Parse XMind files (.xmind is a ZIP containing content.json)."""
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            # XMind 8+ uses content.json
            if "content.json" in zf.namelist():
                raw = zf.read("content.json")
            elif "content.xml" in zf.namelist():
                return [], "XMind 旧版 XML 格式暂不支持，请导出为新版 .xmind 格式"
            else:
                return [], "XMind 文件中未找到 content.json"
    except zipfile.BadZipFile:
        return [], "无效的 XMind 文件（不是有效的 ZIP 包）"
    except Exception as e:
        return [], f"XMind 文件读取失败: {e}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return [], f"XMind content.json 解析失败: {e}"

    if not isinstance(data, list) or not data:
        return [], "XMind 数据格式异常"

    sheet = data[0]
    root = sheet.get("rootTopic", {})
    children = root.get("children", {}).get("attached", [])
    if not children:
        return [], "XMind 中未找到用例节点（根节点下无子节点）"

    cases = []
    for child in children:
        case = _parse_xmind_node(child)
        if case and case.get("title"):
            cases.append(_normalize_case(case))

    if not cases:
        return [], "未能从 XMind 节点中映射到用例字段"

    return cases, ""


def _parse_xmind_node(node: dict) -> dict | None:
    """Recursively map an XMind node tree to a test case dict."""
    title = node.get("title", "").strip()

    # Check if this is a steps container
    if title in ("步骤", "Steps", "steps", "测试步骤"):
        node_children = node.get("children", {}).get("attached", [])
        steps = []
        for i, step_node in enumerate(node_children):
            step_title = step_node.get("title", "").strip()
            if "→" in step_title:
                action, expected = step_title.split("→", 1)
            else:
                action, expected = step_title, ""
            steps.append({"step": i + 1, "action": action.strip(), "expected": expected.strip()})
        return {"title": "", "steps": steps}

    case: dict[str, Any] = {"title": title}

    # Map known prefixes: "优先级:高", "类型:ui", etc.
    prefix_map = {
        "优先级": "priority", "类型": "test_type",
        "前置条件": "preconditions", "描述": "description",
        "标签": "tags", "平台": "platform_type",
        "用例类型": "test_type",
    }

    node_children = node.get("children", {}).get("attached", [])
    for sub in node_children:
        sub_title = sub.get("title", "").strip()

        # Check if sub node is a key:value pair
        for prefix, field in prefix_map.items():
            if sub_title.startswith(f"{prefix}:") or sub_title.startswith(f"{prefix}："):
                val = sub_title.split(":", 1)[-1].split("：", 1)[-1].strip()
                if field == "tags":
                    case[field] = [t.strip() for t in val.split(",") if t.strip()]
                else:
                    case[field] = val
                break
        else:
            # Check if sub node is a steps container
            if sub_title in ("步骤", "Steps", "steps", "测试步骤"):
                steps_result = _parse_xmind_node(sub)
                if steps_result and steps_result.get("steps"):
                    case["steps"] = steps_result["steps"]
            elif not sub_title.startswith(("优先级", "类型", "前置", "描述", "标签", "平台")):
                # Unrecognized — treat as description content
                existing_desc = case.get("description", "")
                case["description"] = (existing_desc + "\n" + sub_title).strip()

    return case if title else None


# ═══════════════════════════════════════════════════════════════
# Main Entry: Parse with AI Fallback
# ═══════════════════════════════════════════════════════════════

PARSERS = {
    "json": _parse_json,
    "excel": _parse_excel,
    "markdown": _parse_markdown,
    "xmind": _parse_xmind,
}


async def parse_cases(fmt: str, raw_content: bytes | str) -> tuple[list[dict], str, str]:
    """Parse test cases from raw content. Returns (cases, error, method).

    method is one of: 'code', 'ai_fallback'
    """
    fmt = fmt.lower()
    parser = PARSERS.get(fmt)

    if not parser:
        return [], f"不支持的格式: {fmt}", ""

    # Try code parsing first
    parsed, error = parser(raw_content)

    if not error and parsed:
        return parsed, "", "code"

    # JSON never falls back to AI
    if fmt == "json":
        return [], error, ""

    # AI fallback for Excel, Markdown, XMind
    logger.info("case_parser_fallback", format=fmt, error=error)
    try:
        ai_cases = await _ai_fallback(raw_content)
        if ai_cases:
            return ai_cases, "", "ai_fallback"
        return [], f"代码解析失败: {error}。AI 解析也未提取到用例。", ""
    except Exception as e:
        logger.error("case_parser_ai_fallback_failed", format=fmt, error=str(e))
        return [], f"代码解析失败: {error}。AI 解析异常: {str(e)}", ""


async def _ai_fallback(raw_content: bytes | str) -> list[dict]:
    """Use LLM to parse unstructured content into test cases."""
    if isinstance(raw_content, bytes):
        try:
            raw_content = raw_content.decode("utf-8")
        except UnicodeDecodeError:
            raw_content = raw_content.decode("utf-8", errors="replace")

    text = str(raw_content)[:8000]

    system_prompt = (
        "你是测试用例结构化专家。将用户提供的非标准格式测试用例内容，"
        "解析为 JSON 数组格式。每个用例包含: title, description, preconditions, "
        "steps[{step,action,expected}], priority, test_type, tags, platform_type。"
        "如果无法识别任何用例，返回空数组 []。只输出 JSON，不要输出其他内容。"
    )

    user_prompt = f"""请解析以下内容为测试用例 JSON 数组：

{text}
"""

    try:
        response = await llm_call(
            LLMRequest(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                task_tag="parsing",
                complexity="medium",
                expect_json=True,
                max_tokens=4096,
                stage_name="review",
            )
        )
    except Exception as e:
        logger.error("ai_fallback_llm_call_failed", error=str(e))
        return []

    if response.parsed_json:
        data = response.parsed_json
        if isinstance(data, list):
            cases = data
        elif isinstance(data, dict) and "cases" in data:
            cases = data["cases"]
        else:
            cases = []
        return [_normalize_case(c) for c in cases if isinstance(c, dict)]

    # Try to extract JSON from raw content
    raw = response.content or ""
    try:
        # Try markdown code fence
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
        if m:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                return [_normalize_case(c) for c in data if isinstance(c, dict)]
        # Try direct parse
        data = json.loads(raw)
        if isinstance(data, list):
            return [_normalize_case(c) for c in data if isinstance(c, dict)]
    except Exception:
        pass

    return []
