"""跨执行任务复用的模块导航路径缓存。"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

_CACHE_LOCK = threading.Lock()


class NavigationPathCache:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(
        self,
        *,
        app_id: str,
        module: str,
        start_package: str,
        start_activity: str,
        app_version: str = "",
    ) -> list[dict[str, Any]]:
        key = self._key(app_id, module, start_package, start_activity, app_version)
        with _CACHE_LOCK:
            data = self._read()
            actions = (data.get("entries") or {}).get(key, {}).get("actions") or []
        if not isinstance(actions, list):
            return []
        return [
            {"tool": str(item["tool"]), "arguments": dict(item.get("arguments") or {})}
            for item in actions
            if isinstance(item, dict) and item.get("tool")
        ]

    def save(
        self,
        *,
        app_id: str,
        module: str,
        start_package: str,
        start_activity: str,
        actions: list[dict[str, Any]],
        app_version: str = "",
    ) -> None:
        if not actions:
            return
        key = self._key(app_id, module, start_package, start_activity, app_version)
        with _CACHE_LOCK:
            data = self._read()
            entries = data.setdefault("entries", {})
            entries[key] = {
                "app_id": app_id,
                "module": module,
                "start_package": start_package,
                "start_activity": start_activity,
                "app_version": app_version,
                "actions": actions,
            }
            self._write(data)

    def invalidate(
        self,
        *,
        app_id: str,
        module: str,
        start_package: str,
        start_activity: str,
        app_version: str = "",
    ) -> None:
        key = self._key(app_id, module, start_package, start_activity, app_version)
        with _CACHE_LOCK:
            data = self._read()
            entries = data.setdefault("entries", {})
            if entries.pop(key, None) is not None:
                self._write(data)

    @staticmethod
    def _key(
        app_id: str,
        module: str,
        start_package: str,
        start_activity: str,
        app_version: str,
    ) -> str:
        raw = json.dumps(
            [app_id, app_version, module, start_package, start_activity],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return {"version": 1, "entries": {}}
        if not isinstance(data, dict) or data.get("version") != 1:
            return {"version": 1, "entries": {}}
        if not isinstance(data.get("entries"), dict):
            data["entries"] = {}
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(self.path)
