"""步骤级留痕：日志 + 截图 + page_source 快照（全过程可视化）。

产物布局（run_dir 下）：
  <case_id>/steps.jsonl              每步一行 JSON
  <case_id>/screenshots/<n>_{before,after}.png
  <case_id>/source/<n>.xml
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StepRecorder:
    def __init__(
        self,
        driver,
        run_dir: Path,
        case_id: str,
        *,
        redact_keys: list[str] | None = None,
        screenshot_each_step: bool = True,
        dump_source_each_step: bool = True,
    ) -> None:
        self.driver = driver
        self.case_id = case_id
        self.case_dir = run_dir / case_id
        self.shot_dir = self.case_dir / "screenshots"
        self.src_dir = self.case_dir / "source"
        self.steps_log = self.case_dir / "steps.jsonl"
        self.redact_keys = [k.lower() for k in (redact_keys or [])]
        self.screenshot_each_step = screenshot_each_step
        self.dump_source_each_step = dump_source_each_step
        for d in (self.case_dir, self.shot_dir, self.src_dir):
            d.mkdir(parents=True, exist_ok=True)

    def capture_screenshot(self, step_no: int, phase: str) -> str:
        if not self.screenshot_each_step or self.driver is None:
            return ""
        path = self.shot_dir / f"{step_no:02d}_{phase}.png"
        try:
            self.driver.get_screenshot_as_file(str(path))
            return str(path)
        except Exception:
            return ""

    def capture_source(self, step_no: int) -> str:
        if not self.dump_source_each_step or self.driver is None:
            return ""
        path = self.src_dir / f"{step_no:02d}.xml"
        try:
            src = self.driver.page_source or ""
            path.write_text(src, encoding="utf-8")
            return str(path)
        except Exception:
            return ""

    def _redact(self, text: str) -> str:
        if not text:
            return text
        low = text.lower()
        for k in self.redact_keys:
            if k and k in low:
                return "***"
        return text

    def write_step(self, record: dict[str, Any]) -> None:
        safe = dict(record)
        # 输入值按敏感字段名打码（description 命中敏感词也打码）
        if any(k in (safe.get("description", "").lower()) for k in self.redact_keys):
            safe["value"] = "***"
        with self.steps_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(safe, ensure_ascii=False) + "\n")
