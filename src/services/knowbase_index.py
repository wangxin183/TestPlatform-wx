"""自包含知识库向量索引（不依赖 Obsidian 桌面端）。

功能：
- 遍历 vault 中的 Markdown，按标题/段落分块
- 对分块做 embedding（OpenAI 或本地 hash 向量）并写入 SQLite
- 按文件 mtime/hash 增量更新
- 提供余弦相似度 Top-K 检索

Usage:
    from src.services.knowbase_index import KnowledgeBaseIndex

    index = KnowledgeBaseIndex.from_settings()
    index.ensure_index(force=False)
    hits = index.search("漫画阅读器登录刷新失败", top_k=8)
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import httpx

from src.core.config import settings
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# 本地 hash embedding 维度（固定，无需外部模型）
LOCAL_EMBED_DIM = 384


@dataclass
class KnowledgeBlockHit:
    note_path: str
    heading: str
    content: str
    score: float


class KnowledgeBaseIndex:
    """Vault Markdown → SQLite 向量索引。"""

    def __init__(
        self,
        vault_path: Path,
        index_db_path: Path,
        embedding_provider: str = "openai",
        embedding_model: str = "text-embedding-3-small",
        embedding_api_base: str = "https://api.openai.com/v1",
        embedding_api_key_env: str = "OPENAI_API_KEY",
        max_block_chars: int = 1200,
        top_k: int = 8,
        min_score: float = 0.22,
    ) -> None:
        self.vault_path = Path(vault_path)
        self.index_db_path = Path(index_db_path)
        self.embedding_provider = (embedding_provider or "openai").strip().lower()
        self.embedding_model = embedding_model
        self.embedding_api_base = embedding_api_base.rstrip("/")
        self.embedding_api_key_env = embedding_api_key_env
        self.max_block_chars = max_block_chars
        self.default_top_k = top_k
        self.default_min_score = min_score
        self._openai_unavailable = False

    @classmethod
    def from_settings(cls) -> "KnowledgeBaseIndex":
        kb = settings.knowledge_base
        vault = Path(kb.vault_path) if kb.vault_path else Path("")
        db = Path(kb.index_db_path)
        if not db.is_absolute():
            db = Path(settings.resolve_path(str(db)))
        return cls(
            vault_path=vault,
            index_db_path=db,
            embedding_provider=kb.embedding_provider,
            embedding_model=kb.embedding_model,
            embedding_api_base=kb.embedding_api_base,
            embedding_api_key_env=kb.embedding_api_key_env,
            max_block_chars=kb.max_block_chars,
            top_k=kb.top_k,
            min_score=kb.min_score,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return bool(self.vault_path and self.vault_path.exists())

    def block_count(self) -> int:
        if not self.index_db_path.exists():
            return 0
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) FROM kb_blocks").fetchone()
                return int(row[0]) if row else 0
        except Exception:
            return 0

    def ensure_index(self, force: bool = False) -> int:
        """构建或增量更新索引，返回当前 block 数。"""
        if not self.is_available():
            logger.warning("knowbase_vault_missing", path=str(self.vault_path))
            return 0

        self.index_db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._init_schema(conn)
            if force:
                conn.execute("DELETE FROM kb_files")
                conn.execute("DELETE FROM kb_blocks")
                conn.commit()

            md_files = sorted(self.vault_path.rglob("*.md"))
            # 跳过隐藏目录与 Smart 插件目录
            md_files = [
                p for p in md_files
                if ".smart-env" not in p.parts and not any(part.startswith(".") for part in p.parts)
            ]

            existing = {
                row[0]: (row[1], row[2])
                for row in conn.execute("SELECT path, mtime, content_hash FROM kb_files").fetchall()
            }
            seen_paths: set[str] = set()

            for path in md_files:
                rel = str(path.relative_to(self.vault_path)).replace("\\", "/")
                seen_paths.add(rel)
                try:
                    raw = path.read_text(encoding="utf-8")
                except Exception as exc:
                    logger.warning("knowbase_read_failed", path=rel, error=str(exc))
                    continue

                mtime = path.stat().st_mtime
                content_hash = hashlib.sha1(raw.encode("utf-8")).hexdigest()
                prev = existing.get(rel)
                if prev and abs(prev[0] - mtime) < 0.001 and prev[1] == content_hash and not force:
                    continue

                blocks = self._chunk_markdown(raw)
                conn.execute("DELETE FROM kb_blocks WHERE note_path = ?", (rel,))
                for idx, (heading, content) in enumerate(blocks):
                    vector = self.embed_text(f"{heading}\n{content}" if heading else content)
                    if not vector:
                        continue
                    conn.execute(
                        """
                        INSERT INTO kb_blocks(note_path, heading, content, embedding, dim)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (rel, heading, content, self._pack_vector(vector), len(vector)),
                    )
                conn.execute(
                    """
                    INSERT INTO kb_files(path, mtime, content_hash)
                    VALUES (?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        mtime=excluded.mtime,
                        content_hash=excluded.content_hash
                    """,
                    (rel, mtime, content_hash),
                )
                conn.commit()
                logger.info(
                    "knowbase_file_indexed",
                    path=rel,
                    blocks=len(blocks),
                    provider=self._active_provider(),
                )

            # 清理已删除文件
            stale = [p for p in existing if p not in seen_paths]
            for p in stale:
                conn.execute("DELETE FROM kb_blocks WHERE note_path = ?", (p,))
                conn.execute("DELETE FROM kb_files WHERE path = ?", (p,))
            if stale:
                conn.commit()

            count = conn.execute("SELECT COUNT(*) FROM kb_blocks").fetchone()[0]
            logger.info("knowbase_index_ready", blocks=count, provider=self._active_provider())
            return int(count)

    def search(
        self,
        query: str,
        top_k: int | None = None,
        min_score: float | None = None,
    ) -> list[KnowledgeBlockHit]:
        top_k = top_k if top_k is not None else self.default_top_k
        min_score = min_score if min_score is not None else self.default_min_score
        if not query.strip() or not self.index_db_path.exists():
            return []

        q_vec = self.embed_text(query)
        if not q_vec:
            return []

        hits: list[KnowledgeBlockHit] = []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT note_path, heading, content, embedding FROM kb_blocks"
            ).fetchall()
            for note_path, heading, content, blob in rows:
                vec = self._unpack_vector(blob)
                if not vec or len(vec) != len(q_vec):
                    continue
                score = self._cosine(q_vec, vec)
                if score < min_score:
                    continue
                hits.append(
                    KnowledgeBlockHit(
                        note_path=note_path,
                        heading=heading or "",
                        content=content or "",
                        score=score,
                    )
                )

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def _active_provider(self) -> str:
        if self.embedding_provider == "openai" and not self._openai_unavailable:
            api_key = os.environ.get(self.embedding_api_key_env, "")
            if api_key:
                return "openai"
        return "local_hash"

    def embed_text(self, text: str) -> list[float]:
        text = (text or "").strip()
        if not text:
            return []
        if self._active_provider() == "openai":
            try:
                return self._embed_openai(text)
            except Exception as exc:
                logger.warning(
                    "knowbase_openai_embed_failed",
                    error=str(exc)[:300],
                    note="fallback to local_hash",
                )
                self._openai_unavailable = True
        return self._embed_local_hash(text)

    def _embed_openai(self, text: str) -> list[float]:
        api_key = os.environ.get(self.embedding_api_key_env, "")
        if not api_key:
            raise RuntimeError(f"{self.embedding_api_key_env} not set")

        # 控制单请求长度，避免超大块
        payload_text = text[:6000]
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{self.embedding_api_base}/embeddings",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.embedding_model,
                    "input": payload_text,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return list(data["data"][0]["embedding"])

    def _embed_local_hash(self, text: str) -> list[float]:
        """字符 n-gram hashed bag-of-features（零依赖兜底）。"""
        vec = [0.0] * LOCAL_EMBED_DIM
        cleaned = re.sub(r"\s+", " ", text.lower()).strip()
        if not cleaned:
            return vec

        # unigram / bigram / trigram hashing
        tokens: list[str] = []
        # 中文按字，英文按词
        for piece in re.findall(r"[\u4e00-\u9fff]|[a-z0-9_]+", cleaned):
            tokens.append(piece)
        # 加字符滑动窗口
        for n in (2, 3):
            for i in range(max(0, len(cleaned) - n + 1)):
                tokens.append(cleaned[i : i + n])

        for tok in tokens:
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            idx = h % LOCAL_EMBED_DIM
            sign = 1.0 if (h // LOCAL_EMBED_DIM) % 2 == 0 else -1.0
            vec[idx] += sign

        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    # ------------------------------------------------------------------
    # Chunking / storage helpers
    # ------------------------------------------------------------------

    def _chunk_markdown(self, raw: str) -> list[tuple[str, str]]:
        text = self._strip_frontmatter(raw).strip()
        if not text:
            return []

        parts = re.split(r"\n(?=#{1,3}\s)", text)
        blocks: list[tuple[str, str]] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            lines = part.split("\n", 1)
            if lines[0].lstrip().startswith("#"):
                heading = lines[0].lstrip("#").strip()
                body = lines[1].strip() if len(lines) > 1 else ""
            else:
                heading = ""
                body = part

            content = body if body else heading
            if not content:
                continue
            for chunk in self._split_long(content, self.max_block_chars):
                blocks.append((heading, chunk))

        if not blocks:
            for chunk in self._split_long(text, self.max_block_chars):
                blocks.append(("", chunk))
        return blocks

    @staticmethod
    def _split_long(text: str, max_chars: int) -> list[str]:
        if len(text) <= max_chars:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(len(text), start + max_chars)
            # 尽量在段落边界切开
            if end < len(text):
                cut = text.rfind("\n\n", start, end)
                if cut > start + max_chars // 3:
                    end = cut
            chunks.append(text[start:end].strip())
            start = end
        return [c for c in chunks if c]

    @staticmethod
    def _strip_frontmatter(raw: str) -> str:
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", raw, re.DOTALL)
        if match:
            return match.group(2).strip()
        return raw

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.index_db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS kb_files (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL,
                content_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS kb_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_path TEXT NOT NULL,
                heading TEXT,
                content TEXT NOT NULL,
                embedding BLOB NOT NULL,
                dim INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kb_blocks_path ON kb_blocks(note_path);
            """
        )
        conn.commit()

    @staticmethod
    def _pack_vector(vec: Iterable[float]) -> bytes:
        data = list(vec)
        return struct.pack(f"{len(data)}f", *data)

    @staticmethod
    def _unpack_vector(blob: bytes) -> list[float]:
        if not blob:
            return []
        n = len(blob) // 4
        return list(struct.unpack(f"{n}f", blob))

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for x, y in zip(a, b):
            dot += x * y
            na += x * x
            nb += y * y
        if na <= 0 or nb <= 0:
            return 0.0
        return dot / math.sqrt(na * nb)
