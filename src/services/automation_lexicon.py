"""加载 config/automation_lexicon.yaml，供编译器与 lint 共用。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "automation_lexicon.yaml"


@lru_cache(maxsize=1)
def load_automation_lexicon(path: str | None = None) -> dict[str, Any]:
    """返回规范化词表；缺文件时返回空结构并打告警。"""
    target = Path(path) if path else _DEFAULT_PATH
    if not target.exists():
        logger.warning("automation_lexicon_missing", path=str(target))
        return {
            "subjective": (),
            "ambiguous": (),
            "conditional_obs": (),
            "vague_action": (),
            "action_verbs": {},
        }
    with open(target, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    action_verbs = raw.get("action_verbs") or {}
    return {
        "subjective": tuple(raw.get("subjective") or ()),
        "ambiguous": tuple(raw.get("ambiguous") or ()),
        "conditional_obs": tuple(raw.get("conditional_obs") or ()),
        "vague_action": tuple(raw.get("vague_action") or ()),
        "action_verbs": {
            str(kind): tuple(words or ())
            for kind, words in action_verbs.items()
            if kind
        },
    }


def get_subjective() -> tuple[str, ...]:
    return load_automation_lexicon()["subjective"]


def get_ambiguous() -> tuple[str, ...]:
    return load_automation_lexicon()["ambiguous"]


def get_conditional_obs() -> tuple[str, ...]:
    return load_automation_lexicon()["conditional_obs"]


def get_vague_action() -> tuple[str, ...]:
    return load_automation_lexicon()["vague_action"]


def get_action_verbs() -> dict[str, tuple[str, ...]]:
    return load_automation_lexicon()["action_verbs"]
