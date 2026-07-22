"""ACN 被测应用模块目录、入口配方与页面状态定义。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CATALOG_PATH = REPO_ROOT / "config" / "acn_modules.yaml"


def canonicalize_module_name(value: str) -> str:
    """移除汇总 sheet 中的负责人后缀并规范空白。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return re.sub(r"（[^）]+）$", "", text).strip()


@dataclass(frozen=True)
class PageState:
    id: str
    package: str = ""
    activity: str = ""
    required_all: list[dict[str, Any]] = field(default_factory=list)
    required_any: list[dict[str, Any]] = field(default_factory=list)
    forbidden_any: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PageState":
        return cls(
            id=str(data.get("id") or ""),
            package=str(data.get("package") or ""),
            activity=str(data.get("activity") or ""),
            required_all=list(data.get("required_all") or []),
            required_any=list(data.get("required_any") or []),
            forbidden_any=list(data.get("forbidden_any") or []),
        )


@dataclass(frozen=True)
class ModuleDefinition:
    id: str
    name: str
    aliases: list[str]
    entry_nl: str
    entry_steps: list[dict[str, Any]]
    page_states: list[PageState]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModuleDefinition":
        return cls(
            id=str(data.get("id") or ""),
            name=canonicalize_module_name(str(data.get("name") or "")),
            aliases=[str(v) for v in data.get("aliases") or []],
            entry_nl=str(data.get("entry_nl") or ""),
            entry_steps=list(data.get("entry_steps") or []),
            page_states=[
                PageState.from_dict(v)
                for v in data.get("page_states") or []
                if isinstance(v, dict)
            ],
        )


class ACNModuleCatalog:
    """模块名白名单与自然语言映射。"""

    def __init__(self, modules: list[ModuleDefinition], *, app: dict[str, Any] | None = None):
        self.modules = modules
        self.app = app or {}
        self._by_name = {m.name: m for m in modules}
        self._by_id = {m.id: m for m in modules}

    @classmethod
    @lru_cache(maxsize=4)
    def load(cls, path: str | Path = DEFAULT_CATALOG_PATH) -> "ACNModuleCatalog":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        modules = [
            ModuleDefinition.from_dict(v)
            for v in raw.get("modules") or []
            if isinstance(v, dict)
        ]
        return cls(modules, app=dict(raw.get("app") or {}))

    @property
    def names(self) -> list[str]:
        return [m.name for m in self.modules]

    def get(self, name_or_id: str) -> ModuleDefinition | None:
        key = canonicalize_module_name(name_or_id)
        return self._by_name.get(key) or self._by_id.get(key)

    def resolve(self, text: str) -> str:
        """从模块名、别名或场景文本映射 canonical 模块名。"""
        value = canonicalize_module_name(text)
        if value in self._by_name:
            return value

        candidates: list[tuple[int, str]] = []
        for module in self.modules:
            terms = [module.name, *module.aliases]
            for term in terms:
                if term and term in value:
                    candidates.append((len(term), module.name))
        if not candidates:
            return ""

        # 更具体的长别名优先；「漫画阅读器」必须优先于「漫画」。
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def require(self, value: str) -> ModuleDefinition:
        module = self.get(value)
        if module is None:
            raise ValueError(f"未知模块: {value}")
        return module


module_catalog = ACNModuleCatalog.load()
