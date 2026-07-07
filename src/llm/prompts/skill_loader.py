"""Load Claude Code SKILL.md files, parse YAML frontmatter, return body text.

Pattern mirrors templates.py load_prompt() — loads from filesystem,
caches nothing (files are small enough to re-read on demand).

Usage:
    from src.llm.prompts.skill_loader import load_skill

    skill = load_skill("requirement-analyzer")
    if skill:
        system_prompt = skill.body.replace("{platform_type}", "web")
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SKILLS_DIR = PROJECT_ROOT / ".agents" / "skills"


@dataclass
class SkillMetadata:
    """Parsed skill metadata and body text."""
    name: str
    description: str
    body: str


def load_skill(skill_name: str) -> SkillMetadata | None:
    """Load a skill from .agents/skills/<skill_name>/SKILL.md.

    Returns None if the SKILL.md file does not exist.
    """
    skill_path = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_path.exists():
        return None
    raw = skill_path.read_text(encoding="utf-8")
    return _parse_skill_md(raw)


def _parse_skill_md(raw: str) -> SkillMetadata:
    """Parse SKILL.md content with YAML frontmatter delimited by ---."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", raw, re.DOTALL)
    if not match:
        return SkillMetadata(name="", description="", body=raw.strip())
    frontmatter = yaml.safe_load(match.group(1)) or {}
    body = match.group(2).strip()
    return SkillMetadata(
        name=frontmatter.get("name", ""),
        description=frontmatter.get("description", ""),
        body=body,
    )
