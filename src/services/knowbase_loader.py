"""知识库按需加载器 — 按需加载 Obsidian 笔记和历史缺陷数据。

按需加载原则：
- 只加载需求文档中明确提到的模块，不加载无关模块
- 先从文档内容中识别模块名，再加载对应的 Obsidian 笔记
- 历史缺陷数据按模块关键词匹配，只提取相关缺陷

Usage:
    from src.services.knowbase_loader import KnowledgeBaseLoader

    loader = KnowledgeBaseLoader()
    context = loader.build_knowledge_context(
        doc_content="需求文档 Markdown 内容",
        platform_type="ios",
    )
    # context 是一段 Markdown 文本，可直接注入 Agent 的 {knowledge_context}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# ============================================================
# 配置常量
# ============================================================

OBSIDIAN_VAULT_PATH = Path(
    "/Users/xiguawang/Documents/Codex/2026-06-15/iphone/obsidian_vault"
)

BUGLIST_PATH = Path("/Users/xiguawang/TestPlatform-wx/data/ACN_buglist.xlsx")

# Obsidian Vault 中已知的模块目录 → 对应的笔记路径映射
# key: 中文模块名（或拼音/英文别名）
# value: Vault 内的路径列表
MODULE_NOTE_MAP: dict[str, list[str]] = {
    "会员": ["会员体系/FUN会员.md"],
    "fun会员": ["会员体系/FUN会员.md"],
    "会员体系": ["会员体系/FUN会员.md"],
    "阅读器": ["阅读器/漫画阅读器.md"],
    "漫画阅读器": ["阅读器/漫画阅读器.md"],
    "书架": ["漫画书架/漫画书架.md"],
    "漫画书架": ["漫画书架/漫画书架.md"],
    "详情页": [
        "内容详情页/图文帖详情页.md",
        "内容详情页/视频帖子详情页.md",
        "内容详情页/长图帖详情页.md",
        "内容详情页/漫单详情页.md",
        "内容详情页/漫荒详情页.md",
    ],
    "漫画详情": ["漫画频道/漫画详情页.md"],
    "频道": ["漫画频道/漫画频道.md"],
    "社区": ["社区/社区.md", "社区/圈子/圈子.md"],
    "圈子": ["社区/圈子/圈子.md"],
    "个人中心": ["个人中心/我的.md", "个人中心/个人主页.md"],
    "我的": ["个人中心/我的.md"],
    "个人主页": ["个人中心/个人主页.md"],
    "动画": ["动画半播页/动画半播页.md"],
    "半播": ["动画半播页/动画半播页.md"],
    "短视频": ["短视频/短视频播放.md"],
    "发布": ["发布模块/小K秀.md"],
    "小k秀": ["发布模块/小K秀.md"],
    "搜索": ["搜索/搜索排行分类.md"],
    "排行": ["搜索/搜索排行分类.md"],
    "消息": ["消息/消息.md", "消息Push/Push通知.md"],
    "push": ["消息Push/Push通知.md"],
    "推送": ["消息Push/Push通知.md"],
    "追更": ["追更/追更.md"],
    "小程序": ["小程序/小程序.md"],
    "运营后台": ["运营后台/乐高平台.md"],
    "乐高": ["运营后台/乐高平台.md"],
    "安全": ["安全合规/安全整改.md", "安全合规/管控演练.md"],
    "管控": ["安全合规/管控演练.md"],
    "安装": ["通用功能/安装启动.md"],
    "启动": ["通用功能/安装启动.md"],
    "url schemes": ["通用功能/URL_Schemes.md"],
    "通用": ["通用功能/通用功能.md", "通用功能/安装启动.md", "通用功能/URL_Schemes.md"],
}

# 最大加载的笔记数量，防止上下文过长
MAX_NOTES_TO_LOAD = 5
MAX_NOTE_CONTENT_LENGTH = 3000  # 每篇笔记最大字符数
MAX_DEFECTS_TO_SHOW = 50  # 最多展示的缺陷数量


@dataclass
class KnowledgeContext:
    """按需加载的知识上下文"""
    loaded_notes: list[str] = field(default_factory=list)
    note_contents: dict[str, str] = field(default_factory=dict)
    defect_summary: str = ""
    total_defects: int = 0
    mentioned_modules: list[str] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """将加载的知识上下文组装为适合注入 Agent 的 Markdown 文本"""
        parts = []

        if self.note_contents:
            parts.append("## 知识库参考（来自 Obsidian）")
            parts.append("以下是与本次需求相关的已有功能模块信息：\n")
            for note_path, content in self.note_contents.items():
                module_name = Path(note_path).stem
                parts.append(f"### {module_name}")
                parts.append(content)
                parts.append("")
            parts.append("---\n")

        if self.defect_summary:
            parts.append("## 历史缺陷参考")
            parts.append(
                f"该模块共有 {self.total_defects} 条历史缺陷记录，"
                "以下是高频缺陷摘要：\n"
            )
            parts.append(self.defect_summary)
            parts.append("")

        if not parts:
            return "（未加载相关模块的知识库信息）"

        return "\n".join(parts)


# ============================================================
# KnowledgeBaseLoader
# ============================================================

class KnowledgeBaseLoader:
    """按需加载 Obsidian 知识库和历史缺陷数据。"""

    def __init__(self, vault_path: Path | None = None, buglist_path: Path | None = None):
        self.vault_path = vault_path or OBSIDIAN_VAULT_PATH
        self.buglist_path = buglist_path or BUGLIST_PATH

    def build_knowledge_context(
        self,
        doc_content: str,
        platform_type: str = "",
    ) -> KnowledgeContext:
        """分析文档内容，按需构建知识上下文。

        Args:
            doc_content: 需求文档的 Markdown 内容
            platform_type: 目标平台类型（用于过滤平台相关的缺陷）

        Returns:
            KnowledgeContext（包含加载的笔记内容和缺陷摘要）
        """
        ctx = KnowledgeContext()

        # 步骤 1：识别文档中提到的模块
        ctx.mentioned_modules = self._detect_modules(doc_content)
        logger.info(
            "knowledge_modules_detected",
            modules=ctx.mentioned_modules,
            doc_len=len(doc_content),
        )

        if not ctx.mentioned_modules:
            logger.info("knowledge_no_modules_detected")
            return ctx

        # 步骤 2：加载对应的 Obsidian 笔记
        note_paths = self._resolve_note_paths(ctx.mentioned_modules)
        ctx.loaded_notes = note_paths[:MAX_NOTES_TO_LOAD]
        ctx.note_contents = self._load_notes(ctx.loaded_notes)
        logger.info(
            "knowledge_notes_loaded",
            requested=len(note_paths),
            loaded=len(ctx.note_contents),
        )

        # 步骤 3：加载相关缺陷数据
        ctx.total_defects, ctx.defect_summary = self._load_defects(
            ctx.mentioned_modules, platform_type
        )
        logger.info(
            "knowledge_defects_loaded",
            total=ctx.total_defects,
        )

        return ctx

    # ---- 模块检测 ----

    def _detect_modules(self, doc_content: str) -> list[str]:
        """从文档内容中检测提到的模块名称。

        遍历 MODULE_NOTE_MAP 中的关键词，检查是否在文档中出现。
        返回去重后的模块名列表（按匹配长度降序排列，优先选择更具体的匹配）。
        """
        found = set()
        content_lower = doc_content.lower()

        for keyword in MODULE_NOTE_MAP:
            # 支持中文精确匹配和英文模糊匹配
            if keyword in doc_content or keyword.lower() in content_lower:
                found.add(keyword)

        # 按匹配长度降序排列（更具体的关键词优先）
        return sorted(found, key=lambda x: -len(x))

    # ---- 笔记加载 ----

    def _resolve_note_paths(self, module_names: list[str]) -> list[str]:
        """将模块名解析为 Obsidian Vault 中的笔记路径。

        去重并返回唯一的笔记路径列表。
        """
        seen = set()
        paths = []
        for name in module_names:
            note_paths = MODULE_NOTE_MAP.get(name, [])
            for p in note_paths:
                if p not in seen:
                    seen.add(p)
                    paths.append(p)
        return paths

    def _load_notes(self, note_paths: list[str]) -> dict[str, str]:
        """从 Obsidian Vault 中加载笔记内容。

        每篇笔记截断至 MAX_NOTE_CONTENT_LENGTH 字符，
        避免注入过长内容。

        Returns:
            dict：note_path → content（截断后的 Markdown 文本）
        """
        contents = {}
        for note_path in note_paths:
            full_path = self.vault_path / note_path
            try:
                if not full_path.exists():
                    logger.warning(
                        "knowledge_note_not_found",
                        path=str(full_path),
                    )
                    continue

                raw = full_path.read_text(encoding="utf-8")
                # 去掉 YAML frontmatter（--- ... ---）
                raw = self._strip_frontmatter(raw)
                # 截断
                if len(raw) > MAX_NOTE_CONTENT_LENGTH:
                    raw = raw[:MAX_NOTE_CONTENT_LENGTH] + "\n\n...（内容已截断）"

                contents[note_path] = raw
            except Exception as exc:
                logger.error(
                    "knowledge_note_load_error",
                    path=str(full_path),
                    error=str(exc),
                )

        return contents

    @staticmethod
    def _strip_frontmatter(raw: str) -> str:
        """去除 Markdown 的 YAML frontmatter（--- ... ---）。"""
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", raw, re.DOTALL)
        if match:
            return match.group(2).strip()
        return raw

    # ---- 缺陷加载 ----

    def _load_defects(
        self,
        module_names: list[str],
        platform_type: str = "",
    ) -> tuple[int, str]:
        """从历史缺陷 Excel 中按模块关键词加载缺陷摘要。

        Returns:
            (total_matching_defects, summary_text)
        """
        if not module_names:
            return 0, ""

        try:
            if not self.buglist_path.exists():
                logger.warning(
                    "knowledge_buglist_not_found",
                    path=str(self.buglist_path),
                )
                return 0, ""

            import openpyxl
            wb = openpyxl.load_workbook(self.buglist_path, read_only=True)
            ws = wb.active
            if not ws:
                return 0, ""

            # 读取所有行（跳过表头）
            rows = list(ws.iter_rows(values_only=True))
            if len(rows) <= 1:
                return 0, ""

            headers = [str(h).lower() if h else "" for h in rows[0]]
            summary_col = 1  # 默认第二列是摘要
            for i, h in enumerate(headers):
                if "摘要" in h or "summary" in h or "描述" in h:
                    summary_col = i
                    break

            # 匹配模块关键词
            matched = []
            for row in rows[1:]:
                summary = str(row[summary_col]) if len(row) > summary_col and row[summary_col] else ""
                if not summary:
                    continue

                for name in module_names:
                    # 模块名匹配（大小写不敏感）
                    if name.lower() in summary.lower():
                        matched.append(summary)
                        break

                if len(matched) >= MAX_DEFECTS_TO_SHOW:
                    break

            wb.close()

            total = len(matched)
            if total == 0:
                return 0, "（未找到与该模块相关的历史缺陷）"

            # 生成简要摘要（取前 30 条用于 Agent 上下文）
            summary_lines = [
                f"- {s[:120]}" for s in matched[:30]
            ]
            return total, "\n".join(summary_lines)

        except Exception as exc:
            logger.error(
                "knowledge_buglist_load_error",
                error=str(exc),
            )
            return 0, ""
