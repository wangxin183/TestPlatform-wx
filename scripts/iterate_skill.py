#!/usr/bin/env python3
"""Skill iteration helper — aggregates usage data and snapshots the current skill.

Usage:
    python scripts/iterate_skill.py

Reads storage/skill-iterations/iterations.jsonl, prints summary statistics,
backs up the current SKILL.md, and prints instructions for using the
skill-creator to improve the requirement-analyzer skill.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = PROJECT_ROOT / ".agents" / "skills" / "requirement-analyzer" / "SKILL.md"
LOG_PATH = PROJECT_ROOT / "storage" / "skill-iterations" / "iterations.jsonl"
SNAPSHOT_DIR = PROJECT_ROOT / "storage" / "skill-iterations" / "snapshots"


def main() -> None:
    print("=" * 60)
    print("  Skill 迭代工具 — requirement-analyzer")
    print("=" * 60)

    # ── Read usage log ──
    if not LOG_PATH.exists():
        print(f"\n⚠ 使用日志不存在: {LOG_PATH}")
        print("  运行流水线后会自动生成。")
        return

    lines = LOG_PATH.read_text(encoding="utf-8").strip().split("\n")
    records = [json.loads(line) for line in lines if line.strip()]

    if not records:
        print("\n⚠ 使用日志为空。")
        return

    print(f"\n📊 共 {len(records)} 次运行记录\n")

    # ── Compute stats ──
    plan_lengths = [r.get("plan_length", 0) for r in records]
    platforms = {}
    flags_total: dict[str, int] = {}
    for r in records:
        pt = r.get("platform_type", "unknown")
        platforms[pt] = platforms.get(pt, 0) + 1
        for flag, val in (r.get("auto_flags") or {}).items():
            if val:
                flags_total[flag] = flags_total.get(flag, 0) + 1

    avg_len = sum(plan_lengths) / len(plan_lengths) if plan_lengths else 0
    print(f"  平均测试计划长度: {avg_len:,.0f} 字符")
    print(f"  最短: {min(plan_lengths):,} / 最长: {max(plan_lengths):,}")
    print(f"  平台分布: {platforms}")

    if flags_total:
        print(f"\n  质量标记:")
        for flag, count in sorted(flags_total.items(), key=lambda x: -x[1]):
            pct = count / len(records) * 100
            print(f"    {flag}: {count} 次 ({pct:.0f}%)")

    # ── Snapshot current skill ──
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if SKILL_PATH.exists():
        date_str = datetime.now().strftime("%Y%m%d")
        # Find next version number
        existing = list(SNAPSHOT_DIR.glob(f"SKILL_{date_str}_v*.md"))
        version = len(existing) + 1
        snapshot_path = SNAPSHOT_DIR / f"SKILL_{date_str}_v{version}.md"
        shutil.copy2(SKILL_PATH, snapshot_path)
        print(f"\n💾 已备份当前 skill: {snapshot_path}")
    else:
        print(f"\n⚠ SKILL.md 未找到: {SKILL_PATH}")

    # ── Iteration guidance ──
    print(f"""
{"=" * 60}
  下一步：使用 skill-creator 改进 skill
{"=" * 60}

  1. 在 Claude Code 中运行:
     /skill-creator improve .agents/skills/requirement-analyzer/

  2. 根据上述统计信息调整 skill 指令:
     - 如果某些质量标记频繁出现，针对性地修改 SKILL.md
     - 如果测试计划长度普遍偏短，增加更详细的输出要求
     - 如果某个平台效果不好，补充平台特定的参考指南

  3. 改进后再次运行流水线验证效果

  快照目录: {SNAPSHOT_DIR}
""")


if __name__ == "__main__":
    main()
