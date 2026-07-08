#!/usr/bin/env python3
"""重建 / 增量更新知识库向量索引。

用法：
    python scripts/rebuild_knowbase_index.py
    python scripts/rebuild_knowbase_index.py --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.knowbase_index import KnowledgeBaseIndex


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild knowledge base vector index")
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制全量重建（忽略文件 hash）",
    )
    args = parser.parse_args()

    index = KnowledgeBaseIndex.from_settings()
    if not index.is_available():
        print(f"[ERROR] vault 不存在: {index.vault_path}")
        return 1

    print(f"vault: {index.vault_path}")
    print(f"index: {index.index_db_path}")
    print(f"provider: {index.embedding_provider} (runtime: {index._active_provider()})")
    count = index.ensure_index(force=args.force)
    print(f"done. blocks={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
