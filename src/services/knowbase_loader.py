"""知识库按需加载器 — 语义检索优先，关键词匹配兜底。

按需加载原则：
- 优先使用自包含向量索引（顶层块级 Top-K）注入与需求语义相关的笔记片段
- embedding / 索引不可用时，降级到模块关键词匹配（历史行为）
- 历史缺陷数据仍按模块关键词匹配摘要

Usage:
    from src.services.knowbase_loader import KnowledgeBaseLoader

    loader = KnowledgeBaseLoader()
    context = loader.build_knowledge_context(
        doc_content="需求文档 Markdown 内容",
        platform_type="ios",
    )
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from src.core.config import settings
from src.services.knowbase_index import KnowledgeBaseIndex
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# ============================================================
# 兼容常量（关键词兜底仍使用；新配置以 settings.knowledge_base 为准）
# ============================================================

OBSIDIAN_VAULT_PATH = Path(
    "/Users/xiguawang/Documents/Codex/2026-06-15/iphone/obsidian_vault"
)

BUGLIST_PATH = Path("/Users/xiguawang/TestPlatform-wx/data/ACN_buglist.xlsx")

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

MAX_NOTES_TO_LOAD = 5
MAX_NOTE_CONTENT_LENGTH = 3000
MAX_DEFECTS_TO_SHOW = 50


@dataclass
class KnowledgeContext:
    """按需加载的知识上下文"""
    loaded_notes: list[str] = field(default_factory=list)
    note_contents: dict[str, str] = field(default_factory=dict)
    defect_summary: str = ""
    total_defects: int = 0
    mentioned_modules: list[str] = field(default_factory=list)
    retrieval_mode: str = "none"  # semantic | keyword | none

    def to_prompt_text(self) -> str:
        """将加载的知识上下文组装为适合注入 Agent 的 Markdown 文本"""
        parts = []

        if self.note_contents:
            mode_label = (
                "语义检索相关片段"
                if self.retrieval_mode == "semantic"
                else "关键词匹配模块笔记"
            )
            parts.append("## 知识库参考（来自 Obsidian）")
            parts.append(
                "以下是与本次需求相关的**历史产品笔记**，仅用于术语/交互习惯参考。"
                "**不得**据此新增 FR/NFR，不得把知识库内容当作需求来源。\n"
            )
            for note_path, content in self.note_contents.items():
                module_name = Path(note_path).stem
                parts.append(f"### {module_name}")
                parts.append(f"> 来源：`{note_path}`")
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


class KnowledgeBaseLoader:
    """按需加载 Obsidian 知识库和历史缺陷数据。"""

    def __init__(
        self,
        vault_path: Path | None = None,
        buglist_path: Path | None = None,
        index: KnowledgeBaseIndex | None = None,
    ):
        kb = settings.knowledge_base
        if vault_path is not None:
            self.vault_path = vault_path
        elif kb.vault_path:
            self.vault_path = Path(kb.vault_path)
        else:
            self.vault_path = OBSIDIAN_VAULT_PATH

        if buglist_path is not None:
            self.buglist_path = buglist_path
        elif kb.buglist_path:
            p = Path(kb.buglist_path)
            self.buglist_path = p if p.is_absolute() else Path(settings.resolve_path(str(p)))
        else:
            self.buglist_path = BUGLIST_PATH

        self._kb_settings = kb
        self._index = index

    def _get_index(self) -> KnowledgeBaseIndex | None:
        if not self._kb_settings.enabled:
            return None
        if self._index is None:
            try:
                self._index = KnowledgeBaseIndex.from_settings()
                # 允许运行时覆盖 vault（单元测试 / 显式构造）
                if self.vault_path and self.vault_path != self._index.vault_path:
                    self._index.vault_path = self.vault_path
            except Exception as exc:
                logger.warning("knowbase_index_init_failed", error=str(exc))
                return None
        return self._index

    def build_knowledge_context(
        self,
        doc_content: str,
        platform_type: str = "",
    ) -> KnowledgeContext:
        """分析文档内容，按需构建知识上下文。"""
        ctx = KnowledgeContext()
        ctx.mentioned_modules = self._detect_modules(doc_content)

        # 1) 语义检索优先
        if self._try_semantic_retrieve(ctx, doc_content):
            ctx.retrieval_mode = "semantic"
            logger.info(
                "knowledge_semantic_loaded",
                notes=len(ctx.loaded_notes),
                modules=ctx.mentioned_modules[:10],
            )
        elif self._kb_settings.keyword_fallback:
            # 2) 关键词兜底
            self._keyword_retrieve(ctx)
            ctx.retrieval_mode = "keyword" if ctx.note_contents else "none"
            logger.info(
                "knowledge_keyword_fallback",
                modules=ctx.mentioned_modules,
                loaded=len(ctx.note_contents),
            )
        else:
            logger.info("knowledge_no_context", reason="semantic_miss_and_fallback_disabled")

        # 3) 缺陷摘要（仍按模块词）
        if ctx.mentioned_modules:
            ctx.total_defects, ctx.defect_summary = self._load_defects(
                ctx.mentioned_modules, platform_type
            )
            logger.info("knowledge_defects_loaded", total=ctx.total_defects)

        return ctx

    def _try_semantic_retrieve(self, ctx: KnowledgeContext, doc_content: str) -> bool:
        index = self._get_index()
        if index is None or not index.is_available():
            return False

        try:
            if index.block_count() == 0:
                index.ensure_index(force=False)
            # 轻量增量：每次请求尝试一次（内部按 hash 跳过未变更文件）
            index.ensure_index(force=False)

            # 查询：文档前部 + 章节标题 + 已识别模块名（避免整篇超长，并提高相关性）
            headings = re.findall(r"^#{1,3}\s+(.+)$", doc_content, flags=re.MULTILINE)
            query_parts = [doc_content[:1800]]
            if headings:
                query_parts.append("章节：" + "；".join(h.strip() for h in headings[:20]))
            if ctx.mentioned_modules:
                query_parts.append("关键模块：" + "、".join(ctx.mentioned_modules[:12]))
            query = "\n".join(query_parts)

            hits = index.search(
                query,
                top_k=self._kb_settings.top_k,
                min_score=self._kb_settings.min_score,
            )
            if not hits:
                return False

            # 按笔记聚合，控制总上下文长度
            max_chars = self._kb_settings.max_context_chars
            used = 0
            note_order: list[str] = []
            note_chunks: dict[str, list[str]] = {}

            for hit in hits:
                snippet = hit.content.strip()
                if hit.heading:
                    snippet = f"**{hit.heading}**\n{snippet}"
                snippet = f"{snippet}\n\n（相关度 {hit.score:.2f}）"
                if used + len(snippet) > max_chars and note_order:
                    break
                if hit.note_path not in note_chunks:
                    note_chunks[hit.note_path] = []
                    note_order.append(hit.note_path)
                note_chunks[hit.note_path].append(snippet)
                used += len(snippet)

            ctx.loaded_notes = note_order
            ctx.note_contents = {
                path: "\n\n".join(note_chunks[path]) for path in note_order
            }
            return bool(ctx.note_contents)
        except Exception as exc:
            logger.warning("knowledge_semantic_failed", error=str(exc)[:300])
            return False

    def _keyword_retrieve(self, ctx: KnowledgeContext) -> None:
        if not ctx.mentioned_modules:
            return
        note_paths = self._resolve_note_paths(ctx.mentioned_modules)
        ctx.loaded_notes = note_paths[:MAX_NOTES_TO_LOAD]
        ctx.note_contents = self._load_notes(ctx.loaded_notes)

    # ---- 模块检测 ----

    def _detect_modules(self, doc_content: str) -> list[str]:
        found = set()
        content_lower = doc_content.lower()
        for keyword in MODULE_NOTE_MAP:
            if keyword in doc_content or keyword.lower() in content_lower:
                found.add(keyword)
        return sorted(found, key=lambda x: -len(x))

    def _resolve_note_paths(self, module_names: list[str]) -> list[str]:
        seen = set()
        paths = []
        for name in module_names:
            for p in MODULE_NOTE_MAP.get(name, []):
                if p not in seen:
                    seen.add(p)
                    paths.append(p)
        return paths

    def _load_notes(self, note_paths: list[str]) -> dict[str, str]:
        contents = {}
        for note_path in note_paths:
            full_path = self.vault_path / note_path
            try:
                if not full_path.exists():
                    logger.warning("knowledge_note_not_found", path=str(full_path))
                    continue
                raw = full_path.read_text(encoding="utf-8")
                raw = self._strip_frontmatter(raw)
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
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", raw, re.DOTALL)
        if match:
            return match.group(2).strip()
        return raw

    def _load_defects(
        self,
        module_names: list[str],
        platform_type: str = "",
    ) -> tuple[int, str]:
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

            rows = list(ws.iter_rows(values_only=True))
            if len(rows) <= 1:
                return 0, ""

            headers = [str(h).lower() if h else "" for h in rows[0]]
            summary_col = 1
            for i, h in enumerate(headers):
                if "摘要" in h or "summary" in h or "描述" in h:
                    summary_col = i
                    break

            matched = []
            for row in rows[1:]:
                summary = (
                    str(row[summary_col])
                    if len(row) > summary_col and row[summary_col]
                    else ""
                )
                if not summary:
                    continue
                for name in module_names:
                    if name.lower() in summary.lower():
                        matched.append(summary)
                        break
                if len(matched) >= MAX_DEFECTS_TO_SHOW:
                    break

            wb.close()
            total = len(matched)
            if total == 0:
                return 0, "（未找到与该模块相关的历史缺陷）"

            summary_lines = [f"- {s[:120]}" for s in matched[:30]]
            return total, "\n".join(summary_lines)

        except Exception as exc:
            logger.error("knowledge_buglist_load_error", error=str(exc))
            return 0, ""
