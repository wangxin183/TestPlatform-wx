"""Requirement analysis agent — loads the requirement-analyzer skill,
generates a test plan via LLM, saves it, and logs usage for iteration.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from src.llm.agents.base import AgentContext, AgentOutput, BaseAgent
from src.llm.prompts.templates import load_prompt
from src.llm.caller import truncate_prompt
from src.llm.types import LLMRequest
from src.utils.file_storage import save
from src.utils.logging_config import get_logger
from src.utils.stage_logger import get_stage_logger

logger = get_logger(__name__)


class RequirementAgent(BaseAgent):
    """Analyze requirements and generate a comprehensive test plan.

    Flow:
    1. Load persistent skill (.agents/skills/requirement-analyzer/SKILL.md)
       → fall back to LLM-generated skill (with 1 retry)
    2. Call LLM with the skill as system_prompt to generate test plan
       (with 1 retry on failure/empty)
    3. Save test plan to storage/reports/{project_id}/Testplan_{timestamp}.md
    4. Log usage record for skill iteration analysis
    """

    skill_name = "requirement-analyzer"

    async def run(self, ctx: AgentContext) -> AgentOutput:
        slog = get_stage_logger(ctx.pipeline_id, "analysis")
        slog.info(f"========== 需求分析阶段开始 ==========")
        slog.info(f"pipeline_id: {ctx.pipeline_id}")
        slog.info(f"project_id: {ctx.project_id}")
        slog.info(f"platform_type: {ctx.platform_type or '(未指定)'}")
        slog.info(f"custom_prompt: {bool(ctx.custom_prompt)}")
        slog.info(f"parsed_requirements 数量: {len(ctx.extra.get('parsed_requirements', []))}")

        logger.info(
            "requirement_agent_start",
            pipeline_id=ctx.pipeline_id,
            project_id=ctx.project_id,
            platform_type=ctx.platform_type,
            custom_prompt=bool(ctx.custom_prompt),
        )

        # Step 1: Load or create skill
        slog.info("Step 1: 加载或创建分析 Skill...")
        try:
            skill_prompt = await self._load_or_create_skill(ctx)
            slog.info(f"Skill 已加载, 长度: {len(skill_prompt)} 字符")
        except Exception as exc:
            slog.error(f"Skill 加载失败: {exc}")
            logger.error("agent_skill_failed", pipeline_id=ctx.pipeline_id, error=str(exc))
            return AgentOutput(
                success=False,
                error=f"需求分析 Skill 生成失败: {str(exc)}",
                data={"failed_step": "skill_creation", "error_detail": str(exc)},
            )

        logger.info(
            "agent_skill_ready",
            pipeline_id=ctx.pipeline_id,
            skill_length=len(skill_prompt),
        )

        # Step 2: Generate test plan via LLM (with retry)
        slog.info("Step 2: 生成测试计划...")
        try:
            test_plan_md = await self._generate_testplan(ctx, skill_prompt)
            slog.info(f"测试计划生成成功, 长度: {len(test_plan_md)} 字符")
        except Exception as exc:
            slog.error(f"测试计划生成失败: {exc}")
            logger.error("agent_testplan_failed", pipeline_id=ctx.pipeline_id, error=str(exc))
            return AgentOutput(
                success=False,
                error=f"测试计划生成失败: {str(exc)}",
                data={"failed_step": "testplan_generation", "error_detail": str(exc)},
            )

        # Step 3: Save test plan
        slog.info("Step 3: 保存测试计划文件...")
        try:
            file_path = await self._save_testplan(ctx, test_plan_md)
            slog.info(f"测试计划已保存到: {file_path}")
        except Exception as exc:
            slog.error(f"测试计划保存失败: {exc}")
            logger.error("agent_save_failed", pipeline_id=ctx.pipeline_id, error=str(exc))
            return AgentOutput(
                success=False,
                error=f"测试计划文件保存失败: {str(exc)}",
                data={"failed_step": "file_save", "error_detail": str(exc)},
            )

        # Step 4: Log usage
        await self._log_usage(ctx, test_plan_md)

        logger.info(
            "requirement_agent_done",
            pipeline_id=ctx.pipeline_id,
            plan_length=len(test_plan_md),
            plan_file=file_path,
        )

        slog.info(f"========== 需求分析阶段完成 ==========")
        return AgentOutput(
            success=True,
            data={
                "skill_prompt": skill_prompt,
                "test_plan_md": test_plan_md,
                "test_plan_file": file_path,
                "plan_length": len(test_plan_md),
            },
        )

    # ═══════════════════════════════════════════════════════════════
    # Step 1: Load or create skill
    # ═══════════════════════════════════════════════════════════════

    async def _load_or_create_skill(self, ctx: AgentContext) -> str:
        """Load persistent skill, or create via LLM with 1 retry."""
        interpolations = {
            "platform_type": ctx.platform_type,
            "custom_prompt": ctx.custom_prompt or "无特殊要求",
        }
        skill_body = self._load_skill(**interpolations)
        if skill_body:
            return skill_body

        logger.info("agent_skill_not_found_creating", pipeline_id=ctx.pipeline_id)
        return await self._create_skill_with_retry(ctx)

    async def _create_skill_with_retry(self, ctx: AgentContext) -> str:
        """Generate a skill via LLM (skill-creator flow), with 1 retry."""
        skill_creator_template = load_prompt("skill_creator")

        parsed = ctx.extra.get("parsed_requirements", [])
        doc_summary = json.dumps(parsed[:3], ensure_ascii=False, indent=2) if parsed else "无"

        user_prompt = (
            skill_creator_template
            .replace("{platform_type}", ctx.platform_type)
            .replace("{doc_summary}", doc_summary)
            .replace("{custom_prompt}", ctx.custom_prompt or "无特殊要求")
        )

        response = await self._retry_llm(
            LLMRequest(
                system_prompt="你是资深测试架构专家和提示词工程师，所有输出必须使用中文。",
                user_prompt=user_prompt,
                task_tag="analysis",
                complexity="high",
                expect_json=False,
                temperature=0.0,
                max_tokens=16384,
                pipeline_id=ctx.pipeline_id,
                stage_name="analysis",
            ),
            max_retries=1,
            error_prefix="Skill 创建",
        )

        skill_text = response.content or ""
        if not skill_text:
            raise RuntimeError("LLM returned empty skill content")

        logger.info(
            "agent_skill_created",
            pipeline_id=ctx.pipeline_id,
            skill_length=len(skill_text),
            model=response.model,
        )
        return skill_text

    # ═══════════════════════════════════════════════════════════════
    # Step 2: Generate test plan (with retry)
    # ═══════════════════════════════════════════════════════════════

    async def _generate_testplan(self, ctx: AgentContext, skill_prompt: str) -> str:
        """Call LLM with skill to generate test plan, with 1 retry."""
        parsed = ctx.extra.get("parsed_requirements", [])
        requirements_json = json.dumps(parsed, ensure_ascii=False, indent=2)

        user_prompt = (
            "请根据以下需求文档内容，进行深度分析并生成测试计划。\n\n"
            f"## 需求文档内容\n\n```json\n{requirements_json}\n```\n\n"
            "请输出完整的测试计划（Markdown 格式）。"
        )
        if ctx.custom_prompt:
            user_prompt = f"额外要求：{ctx.custom_prompt}\n\n{user_prompt}"

        logger.info(
            "agent_testplan_llm_start",
            pipeline_id=ctx.pipeline_id,
            skill_length=len(skill_prompt),
            input_length=len(user_prompt),
        )

        response = await self._retry_llm(
            LLMRequest(
                system_prompt=skill_prompt,
                user_prompt=truncate_prompt(user_prompt, 30000),
                task_tag="analysis",
                complexity="high",
                expect_json=False,
                temperature=0.0,
                max_tokens=16384,
                pipeline_id=ctx.pipeline_id,
                stage_name="analysis",
            ),
            max_retries=1,
            error_prefix="LLM 调用",
        )

        test_plan = response.content or ""
        if not test_plan:
            raise RuntimeError("LLM returned empty test plan")

        logger.info(
            "agent_testplan_llm_done",
            pipeline_id=ctx.pipeline_id,
            plan_length=len(test_plan),
            model=response.model,
            latency_ms=response.latency_ms,
        )
        return test_plan

    # ═══════════════════════════════════════════════════════════════
    # Step 3: Save test plan
    # ═══════════════════════════════════════════════════════════════

    async def _save_testplan(self, ctx: AgentContext, test_plan_md: str) -> str:
        """Save test plan to storage/reports/{project_id}/Testplan_{timestamp}.md."""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"Testplan_{timestamp}.md"
        relative_path = f"reports/{ctx.project_id}/{filename}"

        await save(relative_path, test_plan_md.encode("utf-8"))
        logger.info(
            "agent_testplan_saved",
            pipeline_id=ctx.pipeline_id,
            path=relative_path,
            file_size=len(test_plan_md),
        )
        return relative_path

    # ═══════════════════════════════════════════════════════════════
    # Step 4: Log usage for skill iteration
    # ═══════════════════════════════════════════════════════════════

    async def _log_usage(self, ctx: AgentContext, test_plan_md: str) -> None:
        """Append a lightweight usage record for skill iteration analysis."""
        log_dir = "storage/skill-iterations"
        os.makedirs(log_dir, exist_ok=True)

        auto_flags: dict[str, bool] = {}
        if len(test_plan_md) < 200:
            auto_flags["too_short"] = True
        if "## 3. 功能测试点" not in test_plan_md:
            auto_flags["missing_functional_section"] = True
        if "## 6. 风险识别" not in test_plan_md:
            auto_flags["missing_risk_section"] = True

        record = json.dumps({
            "timestamp": datetime.now().isoformat(),
            "pipeline_id": ctx.pipeline_id,
            "platform_type": ctx.platform_type,
            "plan_length": len(test_plan_md),
            "plan_file": f"reports/{ctx.project_id}/Testplan_latest.md",
            "auto_flags": auto_flags,
        }, ensure_ascii=False)

        log_path = os.path.join(log_dir, "iterations.jsonl")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(record + "\n")

        logger.info(
            "agent_usage_logged",
            pipeline_id=ctx.pipeline_id,
            plan_length=len(test_plan_md),
            flags=auto_flags,
        )
