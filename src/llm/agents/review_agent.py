"""Review agent — loads test-case-reviewer + review-scoring-standard skills,
scores test cases via LLM, saves results to DB, and logs usage.

Runs non-blocking: LLM failures don't block the review stage.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

from sqlalchemy import select, update

from src.core.database import async_session_factory
from src.core.models.models import TestCase
from src.llm.agents.base import AgentContext, AgentOutput, BaseAgent
from src.llm.prompts.skill_loader import load_skill
from src.llm.prompts.templates import load_prompt
from src.llm.caller import truncate_prompt
from src.llm.types import LLMRequest
from src.utils.logging_config import get_logger
from src.utils.stage_logger import get_stage_logger

logger = get_logger(__name__)

BATCH_SIZE = 10


def _parse_score_result(text: str) -> dict | None:
    """Parse a single score JSON from LLM text output."""
    if not text:
        return None
    # Try JSON directly
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try markdown code fence
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try { ... } extraction
    start = text.find('{')
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        pass
                    break
    return None


def _parse_batch_results(raw: str, expected_count: int) -> list[dict]:
    """Parse batch LLM output into per-case score results.
    
    LLM output format: each case result separated by '---'.
    """
    results = []
    parts = raw.split('---')
    for part in parts:
        result = _parse_score_result(part)
        if result:
            results.append(result)
        if len(results) >= expected_count:
            break
    return results


class ReviewAgent(BaseAgent):
    """Pre-score test cases via LLM using two composite skills.

    Flow:
    1. Load scoring standard skill (review-scoring-standard, pre-written)
    2. Load or create review skill (test-case-reviewer)
    3. Process cases in batches of BATCH_SIZE, call LLM per batch
    4. Save ai_score / ai_flags to DB
    5. Log usage record

    Non-blocking: if LLM fails, stage still completes with partial scores.
    """

    scoring_skill_name = "review-scoring-standard"
    review_skill_name = "test-case-reviewer"

    async def run(self, ctx: AgentContext) -> AgentOutput:
        slog = get_stage_logger(ctx.pipeline_id, "review")
        slog.info(f"========== AI 预审阶段开始 ==========")
        slog.info(f"pipeline_id: {ctx.pipeline_id}")

        test_cases = ctx.extra.get("test_cases", [])
        if not test_cases:
            slog.info("无用例需要评分，跳过 AI 预审")
            return AgentOutput(success=True, data={"scored_count": 0})

        slog.info(f"待评分用例数: {len(test_cases)}")

        # Step 1: Load scoring standard skill (pre-written, no fallback)
        scoring_skill = self._load_scoring_standard()
        if not scoring_skill:
            slog.warning("评分标准 skill 未找到，使用内置默认量规")
            scoring_skill = self._default_scoring_standard()
        slog.info(f"评分标准 Skill 已加载, 长度: {len(scoring_skill)} 字符")

        # Step 2: Load or create review skill
        try:
            review_skill = await self._load_or_create_review_skill(ctx)
            slog.info(f"评审流程 Skill 已加载, 长度: {len(review_skill)} 字符")
        except Exception as exc:
            slog.warning(f"评审 Skill 加载失败: {exc}，使用默认流程")
            review_skill = self._default_review_skill()

        # Step 3: Combine skills into system prompt
        combined_prompt = (
            "## 评审流程\n\n"
            + review_skill
            + "\n\n## 评分标准\n\n"
            + scoring_skill
        )
        slog.info(f"复合 Skill 总长度: {len(combined_prompt)} 字符")

        # Step 4: Score cases in batches
        all_results = []
        total_scored = 0
        for batch_start in range(0, len(test_cases), BATCH_SIZE):
            batch = test_cases[batch_start:batch_start + BATCH_SIZE]
            try:
                batch_results = await self._score_batch(ctx, combined_prompt, batch)
                all_results.extend(batch_results)
                total_scored += len(batch_results)
                slog.info(f"批次 {batch_start // BATCH_SIZE + 1}: 评分完成 {len(batch_results)} 条")
            except Exception as exc:
                slog.warning(f"批次 {batch_start // BATCH_SIZE + 1} 评分失败: {exc}，跳过")
                # Fill with null scores for failed batches
                for tc in batch:
                    all_results.append({"case_id": tc.get("id", ""), "score": None, "flags": []})

        # Step 5: Save scores to DB
        try:
            saved_count = await self._save_scores(ctx, test_cases, all_results)
            slog.info(f"评分结果已保存到数据库, 数量={saved_count}")
        except Exception as exc:
            slog.warning(f"评分保存失败: {exc}，但评审仍可继续")

        # Step 6: Log usage
        await self._log_usage(ctx, all_results)

        logger.info(
            "review_agent_done",
            pipeline_id=ctx.pipeline_id,
            total_cases=len(test_cases),
            scored=total_scored,
        )
        slog.info(f"========== AI 预审阶段完成 ========== 已评分: {total_scored}/{len(test_cases)}")

        return AgentOutput(
            success=True,
            data={
                "total_cases": len(test_cases),
                "scored_count": total_scored,
                "results": all_results,
            },
        )

    # ═══════════════════════════════════════════════════════════════
    # Skill loading
    # ═══════════════════════════════════════════════════════════════

    def _load_scoring_standard(self) -> str | None:
        """Load pre-written scoring standard skill. No LLM fallback."""
        skill = load_skill(self.scoring_skill_name)
        if not skill or not skill.body:
            return None
        logger.info(
            "agent_scoring_skill_loaded",
            skill_name=skill.name,
            body_length=len(skill.body),
        )
        return skill.body

    async def _load_or_create_review_skill(self, ctx: AgentContext) -> str:
        """Load review skill from disk, or create via LLM as fallback."""
        skill = load_skill(self.review_skill_name)
        if skill and skill.body:
            logger.info(
                "agent_review_skill_loaded",
                skill_name=skill.name,
                body_length=len(skill.body),
            )
            return skill.body

        logger.info("agent_review_skill_not_found_creating", pipeline_id=ctx.pipeline_id)
        return await self._create_review_skill(ctx)

    async def _create_review_skill(self, ctx: AgentContext) -> str:
        """Generate review skill via LLM."""
        skill_creator_template = load_prompt("skill_creator")

        user_prompt = (
            skill_creator_template
            .replace("{platform_type}", ctx.platform_type or "通用")
            .replace("{doc_summary}", "测试用例自动评审：逐条评分、风险标记、改进建议")
            .replace("{custom_prompt}", "评审 skill：输入测试用例 JSON，输出评分+flags+suggestion")
        )

        response = await self._retry_llm(
            LLMRequest(
                system_prompt="你是资深测试用例评审专家和提示词工程师，所有输出必须使用中文。",
                user_prompt=user_prompt,
                task_tag="generation",
                complexity="high",
                expect_json=False,
                max_tokens=4096,
                pipeline_id=ctx.pipeline_id,
                stage_name="review",
            ),
            max_retries=0,
            error_prefix="评审 Skill 创建",
        )

        skill_text = response.content or ""
        if not skill_text:
            raise RuntimeError("LLM returned empty review skill content")

        logger.info(
            "agent_review_skill_created",
            pipeline_id=ctx.pipeline_id,
            skill_length=len(skill_text),
            model=response.model,
        )
        return skill_text

    def _default_scoring_standard(self) -> str:
        """Fallback scoring standard when skill file is missing."""
        return """# 测试用例评分标准（内置默认）
对每条测试用例从以下维度打分（总分 100）：

1. 步骤完整性（30分）：steps数量 + expected 明确性
2. 描述清晰度（25分）：title 准确性 + description 详细度
3. 前置条件完整性（15分）：preconditions 具体性
4. 优先级合理性（15分）：priority 与影响范围匹配
5. 测试类型准确性（10分）：test_type 与操作性质一致
6. 平台适配度（5分）：平台特定操作体现

输出 JSON: {"score": 0-100, "dimensions": {...}, "flags": [...], "suggestion": "..."}"""

    def _default_review_skill(self) -> str:
        """Fallback review skill."""
        return """# 测试用例自动评审（默认流程）
逐条评分，严格按评分标准打分。输出 JSON 格式评分结果，用 --- 分隔。"""

    # ═══════════════════════════════════════════════════════════════
    # Step 4: Score batch
    # ═══════════════════════════════════════════════════════════════

    async def _score_batch(
        self, ctx: AgentContext, system_prompt: str, batch: list[dict]
    ) -> list[dict]:
        """Score a batch of test cases via LLM."""
        # Strip to only review-relevant fields
        review_items = []
        for tc in batch:
            review_items.append({
                "id": tc.get("id", ""),
                "title": tc.get("title", ""),
                "description": tc.get("description", ""),
                "preconditions": tc.get("preconditions", ""),
                "steps": tc.get("steps", []),
                "priority": tc.get("priority", "中"),
                "test_type": tc.get("test_type", "ui"),
                "platform_type": tc.get("platform_type", ""),
            })

        batch_json = json.dumps(review_items, ensure_ascii=False, indent=2)
        user_prompt = (
            f"请对以下 {len(review_items)} 条测试用例逐条评分（共 {len(review_items)} 条）：\n\n"
            f"```json\n{batch_json}\n```\n\n"
            "逐条输出 JSON 评分结果，每两条之间用 --- 分隔。"
        )

        logger.info(
            "agent_score_batch_start",
            pipeline_id=ctx.pipeline_id,
            batch_size=len(review_items),
        )

        response = await self._retry_llm(
            LLMRequest(
                system_prompt=system_prompt,
                user_prompt=truncate_prompt(user_prompt, 30000),
                task_tag="generation",
                complexity="medium",
                expect_json=False,
                max_tokens=4096 * len(review_items),
                pipeline_id=ctx.pipeline_id,
                stage_name="review",
            ),
            max_retries=0,
            error_prefix="AI 评分",
        )

        raw = response.content or ""
        results = _parse_batch_results(raw, len(review_items))

        # Map results back to case IDs
        mapped = []
        for i, item in enumerate(review_items):
            if i < len(results):
                results[i]["case_id"] = item["id"]
                mapped.append(results[i])
            else:
                mapped.append({"case_id": item["id"], "score": None, "flags": []})

        logger.info(
            "agent_score_batch_done",
            pipeline_id=ctx.pipeline_id,
            parsed=len(results),
            expected=len(review_items),
            model=response.model,
        )
        return mapped

    # ═══════════════════════════════════════════════════════════════
    # Step 5: Save scores to DB
    # ═══════════════════════════════════════════════════════════════

    async def _save_scores(
        self, ctx: AgentContext, test_cases: list[dict], results: list[dict]
    ) -> int:
        """Write ai_score and ai_flags to TestCase rows."""
        async with async_session_factory() as session:
            saved = 0
            for result in results:
                case_id = result.get("case_id", "")
                if not case_id or result.get("score") is None:
                    continue
                await session.execute(
                    update(TestCase)
                    .where(TestCase.id == case_id)
                    .values(
                        ai_score=result.get("score"),
                        ai_flags=result.get("flags", []),
                    )
                )
                saved += 1
            await session.commit()

        logger.info(
            "agent_scores_saved",
            pipeline_id=ctx.pipeline_id,
            count=saved,
        )
        return saved

    # ═══════════════════════════════════════════════════════════════
    # Step 6: Log usage
    # ═══════════════════════════════════════════════════════════════

    async def _log_usage(self, ctx: AgentContext, results: list[dict]) -> None:
        """Append usage record for skill iteration analysis."""
        log_dir = "storage/skill-iterations"
        os.makedirs(log_dir, exist_ok=True)

        scores = [r.get("score") for r in results if r.get("score") is not None]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0
        high_risk = sum(1 for r in results if "high_risk" in (r.get("flags") or []))

        record = json.dumps({
            "timestamp": datetime.now().isoformat(),
            "pipeline_id": ctx.pipeline_id,
            "total_cases": len(results),
            "scored_cases": len(scores),
            "avg_score": avg_score,
            "high_risk_count": high_risk,
        }, ensure_ascii=False)

        log_path = os.path.join(log_dir, "iterations.jsonl")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(record + "\n")

        logger.info(
            "agent_usage_logged",
            pipeline_id=ctx.pipeline_id,
            avg_score=avg_score,
            high_risk=high_risk,
        )
