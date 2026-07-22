"""pytest 执行层与 runner 之间的上下文契约。

runner 落盘 context.json（含 run_dir / config 覆盖 / 已编译脚本列表），
pytest 层通过环境变量 EXEC_RUNTIME_CONTEXT 读取。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENV_CONTEXT = "EXEC_RUNTIME_CONTEXT"


@dataclass
class ExecContext:
    run_id: str
    run_dir: Path
    config_overrides: dict[str, Any]
    script_files: list[Path]

    @classmethod
    def load(cls) -> "ExecContext":
        path = os.environ.get(ENV_CONTEXT)
        if not path:
            raise RuntimeError(f"环境变量 {ENV_CONTEXT} 未设置，无法定位执行上下文")
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        run_dir = Path(data["run_dir"])
        return cls(
            run_id=data.get("run_id", ""),
            run_dir=run_dir,
            config_overrides=data.get("config_overrides") or {},
            script_files=[run_dir / s for s in (data.get("scripts") or [])],
        )

    @staticmethod
    def write(path: Path, *, run_id: str, run_dir: Path,
              config_overrides: dict[str, Any], scripts: list[str]) -> None:
        path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "run_dir": str(run_dir),
                    "config_overrides": config_overrides,
                    "scripts": scripts,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
