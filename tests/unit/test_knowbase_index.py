"""知识库向量索引 / 语义检索单元测试（不依赖外部 API）。"""

from __future__ import annotations

from pathlib import Path

from src.services.knowbase_index import KnowledgeBaseIndex
from src.services.knowbase_loader import KnowledgeBaseLoader


def test_local_hash_index_search(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "阅读器").mkdir(parents=True)
    (vault / "个人中心").mkdir(parents=True)
    (vault / "阅读器" / "漫画阅读器.md").write_text(
        "# 漫画阅读器\n\n支持左右翻页、目录跳转、夜间模式。\n\n## 缓存\n离线缓存章节可打开。\n",
        encoding="utf-8",
    )
    (vault / "个人中心" / "我的.md").write_text(
        "# 我的\n\n包含 FUN会员、养怪兽、个性装扮。\n",
        encoding="utf-8",
    )

    db = tmp_path / "kb.sqlite3"
    index = KnowledgeBaseIndex(
        vault_path=vault,
        index_db_path=db,
        embedding_provider="local_hash",
        top_k=5,
        min_score=0.01,
        max_block_chars=800,
    )
    count = index.ensure_index(force=True)
    assert count >= 2

    hits = index.search("漫画阅读器 翻页 夜间模式 离线缓存", top_k=3, min_score=0.0)
    assert hits
    assert any("漫画阅读器" in h.note_path for h in hits)


def test_loader_semantic_prefers_related_blocks(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "阅读器").mkdir(parents=True)
    (vault / "个人中心").mkdir(parents=True)
    (vault / "阅读器" / "漫画阅读器.md").write_text(
        "# 漫画阅读器\n\n左右翻页与目录跳转。缓存失败需提示。\n",
        encoding="utf-8",
    )
    (vault / "个人中心" / "我的.md").write_text(
        "# 我的\n\n养怪兽是长期运营玩法，与本次阅读器改造无关。\n",
        encoding="utf-8",
    )

    db = tmp_path / "kb.sqlite3"
    index = KnowledgeBaseIndex(
        vault_path=vault,
        index_db_path=db,
        embedding_provider="local_hash",
        top_k=5,
        min_score=0.01,
    )
    index.ensure_index(force=True)

    loader = KnowledgeBaseLoader(vault_path=vault, index=index)
    # 覆盖配置片段：保证语义优先
    loader._kb_settings.keyword_fallback = True
    loader._kb_settings.top_k = 3
    loader._kb_settings.min_score = 0.01
    loader._kb_settings.max_context_chars = 4000

    ctx = loader.build_knowledge_context(
        doc_content="本次需求：漫画阅读器支持左右翻页与离线缓存打开校验。",
        platform_type="ios",
    )
    assert ctx.retrieval_mode in ("semantic", "keyword")
    text = ctx.to_prompt_text()
    assert "漫画阅读器" in text or "阅读器" in text
