"""Skills management API — CRUD + AI generation for SKILL.md files.

Reads/writes .agents/skills/<name>/SKILL.md directly on the filesystem.
No database model — the filesystem is the source of truth.
"""

from __future__ import annotations

import re
import os
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import JSONResponse

from src.llm.caller import llm_call
from src.llm.types import LLMRequest
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SKILLS_DIR = PROJECT_ROOT / ".agents" / "skills"

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _parse_frontmatter(raw: str) -> dict:
    """Parse YAML frontmatter from raw SKILL.md content."""
    match = re.match(r"^---\s*\n(.*?)\n---", raw, re.DOTALL)
    if not match:
        return {}
    return yaml.safe_load(match.group(1)) or {}


def _list_skills() -> list[dict]:
    """Scan .agents/skills/ and return metadata for all skills."""
    skills = []
    if not SKILLS_DIR.exists():
        return skills
    for entry in sorted(SKILLS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            raw = skill_md.read_text(encoding="utf-8")
            fm = _parse_frontmatter(raw)
            stat = skill_md.stat()
            skills.append({
                "name": fm.get("name", entry.name),
                "dir_name": entry.name,
                "description": str(fm.get("description", "")).replace("\n", " ").strip(),
                "size": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        except Exception as e:
            logger.warning("skill_list_read_error", skill_dir=entry.name, error=str(e))
    return skills


def _read_skill(skill_name: str) -> str | None:
    """Read the full content of a skill's SKILL.md file."""
    skill_md = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_md.exists():
        return None
    return skill_md.read_text(encoding="utf-8")


def _write_skill(skill_name: str, content: str):
    """Write content to a skill's SKILL.md file."""
    skill_dir = SKILLS_DIR / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(content, encoding="utf-8")


@router.get("")
async def list_skills():
    """List all skills with metadata."""
    skills = _list_skills()
    return {"success": True, "data": skills, "error": None}


@router.get("/{skill_name}")
async def get_skill(skill_name: str):
    """Get the full raw content of a skill's SKILL.md."""
    content = _read_skill(skill_name)
    if content is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    fm = _parse_frontmatter(content)
    return {
        "success": True,
        "data": {
            "name": fm.get("name", skill_name),
            "dir_name": skill_name,
            "description": fm.get("description", ""),
            "content": content,
        },
        "error": None,
    }


@router.put("/{skill_name}")
async def update_skill(skill_name: str, content: str = Form(...)):
    """Update a skill's SKILL.md content. Validates YAML frontmatter."""
    # Validate YAML frontmatter
    fm = _parse_frontmatter(content)
    if not fm.get("name"):
        raise HTTPException(
            status_code=422,
            detail="SKILL.md 必须包含 YAML frontmatter 且至少含有 name 字段",
        )

    _write_skill(skill_name, content)
    logger.info("skill_updated", skill_name=skill_name)
    return {"success": True, "data": None, "error": None}


@router.post("")
async def create_skill(
    name: str = Form(...),
    description: str = Form(""),
):
    """Create a new skill from a skeleton template."""
    # Validate name format
    if not NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="name 只能包含小写字母、数字和连字符（kebab-case），例如 my-skill",
        )

    # Check for duplicates
    existing = _read_skill(name)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{name}' 已存在",
        )

    # Generate skeleton content
    display_name = name.replace("-", " ").title()
    desc_block = description.replace("\n", "\n  ")
    skeleton = f"""---
name: {name}
description: >
  {desc_block}
---

# {display_name}

## 核心原则

待补充...

## 执行流程

待补充...

## 输入格式

待补充...

## 输出格式

待补充...
"""

    _write_skill(name, skeleton)
    logger.info("skill_created", skill_name=name)

    return {
        "success": True,
        "data": {
            "name": name,
            "dir_name": name,
            "description": description,
            "content": skeleton,
        },
        "error": None,
    }


@router.post("/generate")
async def generate_skill(description: str = Form(...)):
    """AI-generate a new skill from a natural language description."""
    logger.info("skill_generate_start", description=description)

    system_prompt = """你是一个 Skill 编写专家。请根据用户描述，生成一个 SKILL.md 文件。

要求：
1. 开头必须是 YAML frontmatter（--- 包裹），包含 name 和 description 字段
2. name 使用英文 kebab-case（小写字母+数字+连字符），长度不超过30个字符
3. description 使用中文，简洁描述 skill 用途
4. 正文必须包含以下章节：核心原则（## 核心原则）、执行流程（## 执行流程）、输入格式（## 输入格式）、输出格式（## 输出格式）
5. 风格参考专业的测试工程 skill，内容具体可执行，不要空洞的占位符
6. 输出 ONLY 完整的 SKILL.md 内容，不要有任何额外解释

请直接输出 SKILL.md 文件内容："""

    try:
        response = await llm_call(
            LLMRequest(
                system_prompt=system_prompt,
                user_prompt=f"用户描述：{description}",
                task_tag="skill_generation",
                complexity="medium",
                expect_json=False,
                max_tokens=4096,
                temperature=0.5,
            ),
            max_retries=1,
        )

        content = response.content.strip()
        # Remove markdown code fences if present
        content = re.sub(r"^```(?:markdown|md|yaml)?\s*\n?", "", content)
        content = re.sub(r"\n?```\s*$", "", content)

        # Extract name from frontmatter
        fm = _parse_frontmatter(content)
        skill_name = fm.get("name", "")
        if not skill_name or not NAME_RE.match(skill_name):
            raise HTTPException(
                status_code=422,
                detail=f"LLM 生成的 name '{skill_name}' 不合法（需要 kebab-case）",
            )

        # Check for existing skill
        if _read_skill(skill_name) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Skill '{skill_name}' 已存在，请换一个描述重新生成",
            )

        _write_skill(skill_name, content)
        logger.info("skill_generated", skill_name=skill_name)

        return {
            "success": True,
            "data": {
                "name": skill_name,
                "dir_name": skill_name,
                "description": str(fm.get("description", "")),
                "content": content,
            },
            "error": None,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("skill_generate_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"AI 生成失败：{str(e)}")
