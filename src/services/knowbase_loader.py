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

MAX_NOTES_TO_LOAD = 3
MAX_NOTE_CONTENT_LENGTH = 1500
MAX_CHUNKS_PER_NOTE = 2
MAX_NOTES_IN_CONTEXT = 4
MAX_DEFECTS_TO_SHOW = 15
MAX_DEFECT_LINES = 12
# 用户指定模块时语义检索更聚焦，避免与关键词整篇笔记重复堆叠
USER_MODULE_SEMANTIC_TOP_K = 4
AUTO_SEMANTIC_TOP_K = 5


@dataclass
class KnowledgeContext:
    """按需加载的知识上下文"""
    loaded_notes: list[str] = field(default_factory=list)
    note_contents: dict[str, str] = field(default_factory=dict)
    defect_summary: str = ""
    total_defects: int = 0
    mentioned_modules: list[str] = field(default_factory=list)
    user_specified_modules: list[str] = field(default_factory=list)
    retrieval_mode: str = "none"  # semantic | keyword | mixed | none

    def to_prompt_text(self) -> str:
        """将加载的知识上下文组装为适合注入 Agent 的 Markdown 文本"""
        parts = []

        if self.note_contents:
            parts.append("## 知识库参考（来自 Obsidian）")
            parts.append(
                "以下是与本次需求相关的**历史产品笔记片段**，仅用于术语/交互习惯参考。"
                "**不得**据此新增 FR/NFR，不得把知识库内容当作需求来源。\n"
            )
            if self.user_specified_modules:
                parts.append(
                    f"> 用户指定模块：{'、'.join(self.user_specified_modules)}\n"
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
        user_modules: str = "",
    ) -> KnowledgeContext:
        """分析文档内容，按需构建知识上下文。

        Args:
            doc_content: PRD Markdown 正文
            platform_type: 目标平台（缺陷筛选）
            user_modules: 用户在前端填写的模块名，逗号/顿号分隔
        """
        ctx = KnowledgeContext()
        ctx.user_specified_modules = self._parse_user_modules(user_modules)
        detected = self._detect_modules(doc_content)
        ctx.mentioned_modules = self._merge_module_lists(
            ctx.user_specified_modules, detected
        )

        focus_modules = ctx.user_specified_modules or ctx.mentioned_modules[:8]
        allowed_paths: set[str] | None = None
        if ctx.user_specified_modules:
            resolved = self._resolve_note_paths(ctx.user_specified_modules)
            if resolved:
                allowed_paths = set(resolved)

        semantic_ok = False
        if ctx.user_specified_modules:
            # 用户指定模块：先在限定笔记内语义检索，失败再关键词整篇
            semantic_ok = self._try_semantic_retrieve(
                ctx,
                doc_content,
                focus_modules=focus_modules,
                allowed_note_paths=allowed_paths,
                top_k=USER_MODULE_SEMANTIC_TOP_K,
                max_context_chars=min(self._kb_settings.max_context_chars, 5000),
            )
            if not semantic_ok and self._kb_settings.keyword_fallback:
                self._keyword_retrieve(ctx, ctx.user_specified_modules)
                ctx.retrieval_mode = "keyword" if ctx.note_contents else "none"
            elif semantic_ok:
                ctx.retrieval_mode = "semantic"
            else:
                ctx.retrieval_mode = "none"
        else:
            # 未指定模块：语义优先，关键词兜底；整体更保守
            semantic_ok = self._try_semantic_retrieve(
                ctx,
                doc_content,
                focus_modules=focus_modules,
                allowed_note_paths=None,
                top_k=min(self._kb_settings.top_k, AUTO_SEMANTIC_TOP_K),
                max_context_chars=min(self._kb_settings.max_context_chars, 6000),
            )
            if semantic_ok:
                ctx.retrieval_mode = "semantic"
            elif self._kb_settings.keyword_fallback and ctx.mentioned_modules:
                self._keyword_retrieve(ctx, ctx.mentioned_modules[:MAX_NOTES_TO_LOAD])
                ctx.retrieval_mode = "keyword" if ctx.note_contents else "none"
            else:
                ctx.retrieval_mode = "none"

        logger.info(
            "knowledge_context_built",
            mode=ctx.retrieval_mode,
            user_modules=ctx.user_specified_modules,
            mentioned=ctx.mentioned_modules[:10],
            notes=len(ctx.note_contents),
        )

        # 缺陷摘要：优先用户指定模块，否则用合并后的模块列表
        defect_modules = ctx.user_specified_modules or ctx.mentioned_modules
        if defect_modules:
            ctx.total_defects, ctx.defect_summary = self._load_defects(
                defect_modules, platform_type
            )
            logger.info("knowledge_defects_loaded", total=ctx.total_defects)

        return ctx

    def _try_semantic_retrieve(
        self,
        ctx: KnowledgeContext,
        doc_content: str,
        *,
        focus_modules: list[str],
        allowed_note_paths: set[str] | None = None,
        top_k: int | None = None,
        max_context_chars: int | None = None,
    ) -> bool:
        index = self._get_index()
        if index is None or not index.is_available():
            return False

        try:
            if index.block_count() == 0:
                index.ensure_index(force=False)
            index.ensure_index(force=False)

            headings = re.findall(r"^#{1,3}\s+(.+)$", doc_content, flags=re.MULTILINE)
            query_parts: list[str] = []
            if focus_modules:
                query_parts.append("关键模块：" + "、".join(focus_modules[:8]))
            if headings:
                query_parts.append(
                    "章节：" + "；".join(h.strip() for h in headings[:10])
                )
            # 用户指定模块时用更短 PRD 摘要，避免 query 过长引入无关块
            doc_excerpt = doc_content[:600 if ctx.user_specified_modules else 1200]
            query_parts.append(doc_excerpt)
            query = "\n".join(query_parts)

            effective_top_k = top_k if top_k is not None else self._kb_settings.top_k
            hits = index.search(
                query,
                top_k=effective_top_k,
                min_score=self._kb_settings.min_score,
            )
            if not hits:
                return False

            if allowed_note_paths:
                scoped = [h for h in hits if h.note_path in allowed_note_paths]
                if scoped:
                    hits = scoped

            max_chars = max_context_chars or self._kb_settings.max_context_chars
            note_order: list[str] = []
            note_chunks: dict[str, list[str]] = {}
            note_chunk_count: dict[str, int] = {}
            seen_prefixes: set[str] = set()
            used = 0

            for hit in hits:
                if (
                    len(note_order) >= MAX_NOTES_IN_CONTEXT
                    and hit.note_path not in note_chunks
                ):
                    continue
                if note_chunk_count.get(hit.note_path, 0) >= MAX_CHUNKS_PER_NOTE:
                    continue

                snippet = hit.content.strip()
                if hit.heading:
                    snippet = f"**{hit.heading}**\n{snippet}"

                prefix = snippet[:100]
                if prefix in seen_prefixes:
                    continue
                seen_prefixes.add(prefix)

                if used + len(snippet) > max_chars and note_order:
                    break
                if hit.note_path not in note_chunks:
                    note_chunks[hit.note_path] = []
                    note_order.append(hit.note_path)
                note_chunks[hit.note_path].append(snippet)
                note_chunk_count[hit.note_path] = (
                    note_chunk_count.get(hit.note_path, 0) + 1
                )
                used += len(snippet)

            if not note_order:
                return False

            ctx.loaded_notes = note_order
            ctx.note_contents = {
                path: "\n\n".join(note_chunks[path]) for path in note_order
            }
            return True
        except Exception as exc:
            logger.warning("knowledge_semantic_failed", error=str(exc)[:300])
            return False

    def _keyword_retrieve(
        self, ctx: KnowledgeContext, module_names: list[str]
    ) -> None:
        if not module_names:
            return
        note_paths = self._resolve_note_paths(module_names)
        ctx.loaded_notes = note_paths[:MAX_NOTES_TO_LOAD]
        ctx.note_contents = self._load_notes(ctx.loaded_notes)

    # ---- 模块检测 ----

    @staticmethod
    def _parse_user_modules(user_modules: str) -> list[str]:
        """解析用户填写的模块名（逗号/顿号/分号分隔）。"""
        if not user_modules or not user_modules.strip():
            return []
        parts = re.split(r"[,，、;；]", user_modules)
        resolved: list[str] = []
        seen: set[str] = set()
        for raw in parts:
            token = raw.strip()
            if not token:
                continue
            keyword = KnowledgeBaseLoader._match_module_keyword(token)
            key = keyword or token
            norm = key.lower()
            if norm in seen:
                continue
            seen.add(norm)
            resolved.append(key)
        return resolved

    @staticmethod
    def _match_module_keyword(token: str) -> str | None:
        """将用户输入映射到 MODULE_NOTE_MAP 关键词（最长优先）。"""
        if token in MODULE_NOTE_MAP:
            return token
        token_lower = token.lower()
        for keyword in sorted(MODULE_NOTE_MAP.keys(), key=len, reverse=True):
            kw_lower = keyword.lower()
            if (
                kw_lower == token_lower
                or keyword in token
                or token in keyword
            ):
                return keyword
        return None

    @staticmethod
    def _merge_module_lists(
        user_modules: list[str], detected: list[str]
    ) -> list[str]:
        """用户指定模块优先，再追加 PRD 自动识别且未重复的模块。"""
        merged: list[str] = list(user_modules)
        seen = {m.lower() for m in merged}
        for name in detected:
            if name.lower() not in seen:
                merged.append(name)
                seen.add(name.lower())
        return merged

    def _detect_modules(self, doc_content: str) -> list[str]:
        found = set()
        content_lower = doc_content.lower()
        for keyword in MODULE_NOTE_MAP:
            if keyword in doc_content or keyword.lower() in content_lower:
                found.add(keyword)
        return sorted(found, key=lambda x: -len(x))

    def _resolve_note_paths(self, module_names: list[str]) -> list[str]:
        seen: set[str] = set()
        paths: list[str] = []
        for name in module_names:
            mapped = MODULE_NOTE_MAP.get(name, [])
            if mapped:
                for p in mapped:
                    if p not in seen:
                        seen.add(p)
                        paths.append(p)
            else:
                for p in self._find_notes_by_name(name):
                    if p not in seen:
                        seen.add(p)
                        paths.append(p)
        return paths

    def _find_notes_by_name(self, name: str) -> list[str]:
        """按文件名/路径模糊匹配 vault 中的笔记（用户输入未命中映射表时）。"""
        if not self.vault_path.exists():
            return []
        name_lower = name.lower()
        results: list[str] = []
        for md in sorted(self.vault_path.rglob("*.md")):
            rel = str(md.relative_to(self.vault_path))
            if name_lower in md.stem.lower() or name_lower in rel.lower():
                results.append(rel)
                if len(results) >= 2:
                    break
        return results

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

            summary_lines = [f"- {s[:100]}" for s in matched[:MAX_DEFECT_LINES]]
            return total, "\n".join(summary_lines)

        except Exception as exc:
            logger.error("knowledge_buglist_load_error", error=str(exc))
            return 0, ""
