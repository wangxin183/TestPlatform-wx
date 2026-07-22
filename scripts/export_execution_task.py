#!/usr/bin/env python3
"""从数据库导出 approved 用例为 execution_runtime task.json。

用法：
  python scripts/export_execution_task.py --run-id EXE-SMOKE-001 --out storage/execution_runs/EXE-SMOKE-001/task.json
  python scripts/export_execution_task.py --case-id 65929b71-a2f1-4927-b88d-51666a5c4c63 --limit 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from execution_runtime.config import load_config  # noqa: E402


async def _fetch_cases(case_ids: list[str] | None, limit: int) -> list[dict]:
    from sqlalchemy import select

    from src.core.database import async_session_factory
    from src.core.models.models import TestCase

    async with async_session_factory() as session:
        q = select(TestCase).where(TestCase.status == "approved")
        if case_ids:
            q = q.where(TestCase.id.in_(case_ids))
        q = q.order_by(TestCase.created_at.desc()).limit(limit)
        rows = (await session.execute(q)).scalars().all()
        out: list[dict] = []
        for tc in rows:
            steps = tc.steps
            if isinstance(steps, str):
                steps = json.loads(steps)
            out.append(
                {
                    "case_id": str(tc.id),
                    "title": tc.title or "",
                    "status": tc.status,
                    "preconditions": tc.preconditions or "",
                    "platform_type": tc.platform_type or "ios",
                    "test_point_id": tc.test_point_id or "",
                    "steps": steps or [],
                }
            )
        return out


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 execution_runtime task.json")
    parser.add_argument("--run-id", default="EXE-SMOKE-001")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()

    cfg = load_config()
    cases = asyncio.run(_fetch_cases(args.case_ids, args.limit))
    if not cases:
        print("无 approved 用例可导出", file=sys.stderr)
        return 1

    task = {
        "run_id": args.run_id,
        "app": {
            "platform": cfg.target_app.platform,
            "bundle_id": cfg.target_app.bundle_id,
            "app_activity": cfg.target_app.app_activity or None,
        },
        "device": {
            "udid": cfg.device.udid,
            "device_name": cfg.device.device_name,
            "platform_version": cfg.device.platform_version,
            "appium_url": cfg.device.appium_url,
            "automation_name": cfg.device.automation_name,
        },
        "cases": cases,
    }
    if cfg.target_app.platform == "ios" and cfg.device.wda_bundle_id:
        task["device"]["wda_bundle_id"] = cfg.device.wda_bundle_id
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已导出 {len(cases)} 条用例 → {args.out}")
    for c in cases:
        print(f"  - {c['case_id']}: {c['title'][:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
