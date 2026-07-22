"""Test case generator agent — loads the test-case-generator skill,
generates test cases via LLM, saves them to DB, and logs usage.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from src.core.database import async_session_factory
from src.core.models.models import TestCase
from src.llm.agents.base import AgentContext, AgentOutput, BaseAgent
from src.llm.prompts.templates import load_prompt
from src.llm.caller import truncate_prompt
from src.llm.types import LLMRequest
from src.services.testcase_automation_lint import lint_case
from src.services.testcase_contract_compiler import prepare_executable_case
from src.services.testcase_module_catalog import module_catalog
from src.utils.logging_config import get_logger
from src.utils.stage_logger import get_stage_logger

logger = get_logger(__name__)

VALID_TEST_TYPES = {"ui", "api", "performance", "security", "compatibility"}


def _extract_json_from_text(text: str):
    """Try to extract a JSON array or object from LLM response text."""
    import re
    if not text:
        return None

    # Pattern 1: markdown code fence ```json ... ```
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if m:
        inner = m.group(1).strip()
        for attempt in range(2):
            try:
                return json.loads(inner)
            except json.JSONDecodeError:
                if attempt == 0:
                    inner = re.sub(r',\s*([}\]])', r'\1', inner)
                else:
                    break

    # Pattern 2: extract JSON array [...] with bracket matching
    result = _match_brackets(text, '[', ']')
    if result is not None:
        return result

    # Pattern 3: extract JSON object {...} with bracket matching
    result = _match_brackets(text, '{', '}')
    if result is not None:
        return result

    return None


def _match_brackets(text: str, open_ch: str, close_ch: str):
    """Find and parse the largest balanced bracket-delimited JSON in text."""
    import re
    start = text.find(open_ch)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    # Try removing trailing commas
                    fixed = re.sub(r',\s*([}\]])', r'\1', candidate)
                    try:
                        return json.loads(fixed)
                    except json.JSONDecodeError:
                        pass
                break
    return None

class TestCaseGeneratorAgent(BaseAgent):
    """Generate test cases from parsed requirements and analysis report.

    Flow:
    1. Load persistent skill (.agents/skills/test-case-generator/SKILL.md)
       → fall back to LLM-generated skill (with 2 retries)
    2. Call LLM with skill as system_prompt to generate test cases
       (with 2 retries on failure/empty)
    3. Parse JSON response, insert test cases into DB
    4. Log usage record for skill iteration analysis
    """

    skill_name = "test-case-generator"

    async def run(self, ctx: AgentContext) -> AgentOutput:
        slog = get_stage_logger(ctx.pipeline_id, "generation")
        slog.info(f"========== 用例生成阶段开始 ==========")
        slog.info(f"pipeline_id: {ctx.pipeline_id}")
        slog.info(f"project_id: {ctx.project_id}")
        slog.info(f"platform_type: {ctx.platform_type or '(未指定)'}")
        slog.info(f"custom_prompt: {bool(ctx.custom_prompt)}")
        slog.info(f"parsed_requirements 数量: {len(ctx.extra.get('parsed_requirements', []))}")
        slog.info(f"analysis_report 存在: {bool(ctx.extra.get('analysis_report'))}")

        logger.info(
            "testcase_generator_agent_start",
            pipeline_id=ctx.pipeline_id,
            project_id=ctx.project_id,
            platform_type=ctx.platform_type,
            custom_prompt=bool(ctx.custom_prompt),
        )

        # Step 1: Load or create skill
        try:
            skill_prompt = await self._load_or_create_skill(ctx)
        except Exception as exc:
            logger.error("agent_skill_failed", pipeline_id=ctx.pipeline_id, error=str(exc))
            return AgentOutput(
                success=False,
                error=f"用例生成 Skill 加载失败: {str(exc)}",
                data={"failed_step": "skill_creation", "error_detail": str(exc)},
            )

        logger.info(
            "agent_skill_ready",
            pipeline_id=ctx.pipeline_id,
            skill_length=len(skill_prompt),
        )
        slog.info(f"Skill 已加载, 长度: {len(skill_prompt)} 字符")

        # Step 2: Generate test cases via LLM (with retry)
        try:
            cases_data = await self._generate_testcases(ctx, skill_prompt)
        except Exception as exc:
            logger.error("agent_generation_failed", pipeline_id=ctx.pipeline_id, error=str(exc))
            slog.error(f"用例生成失败: {str(exc)}")
            return AgentOutput(
                success=False,
                error=f"用例生成失败: {str(exc)}",
                data={"failed_step": "testcase_generation", "error_detail": str(exc)},
            )

        # Step 3: Insert into DB
        try:
            inserted_count = await self._save_testcases(ctx, cases_data)
        except Exception as exc:
            logger.error("agent_save_failed", pipeline_id=ctx.pipeline_id, error=str(exc))
            return AgentOutput(
                success=False,
                error=f"用例入库失败: {str(exc)}",
                data={"failed_step": "db_insert", "error_detail": str(exc)},
            )

        # Step 4: Log usage
        await self._log_usage(ctx, cases_data)

        priorities = {}
        for c in cases_data:
            p = c.get("priority", "medium")
            priorities[p] = priorities.get(p, 0) + 1

        logger.info(
            "testcase_generator_agent_done",
            pipeline_id=ctx.pipeline_id,
            cases_generated=inserted_count,
            priorities=priorities,
        )
        slog.info(f"========== 用例生成阶段完成 ==========")
        slog.info(f"生成用例数: {inserted_count}, 优先级分布: {priorities}")

        return AgentOutput(
            success=True,
            data={
                "skill_prompt": skill_prompt,
                "test_cases": cases_data,
                "test_cases_generated": inserted_count,
                "priorities": priorities,
            },
        )

    # ═══════════════════════════════════════════════════════════════
    # Step 1: Load or create skill
    # ═══════════════════════════════════════════════════════════════

    async def _load_or_create_skill(self, ctx: AgentContext) -> str:
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
                system_prompt="你是资深测试用例设计专家和提示词工程师，所有输出必须使用中文。",
                user_prompt=user_prompt,
                task_tag="generation_skill",
                complexity="high",
                expect_json=False,
                max_tokens=8192,
                pipeline_id=ctx.pipeline_id,
                stage_name="generation",
            ),
            max_retries=0,
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
    # Step 2: Generate test cases via LLM
    # ═══════════════════════════════════════════════════════════════

    async def _generate_testcases(self, ctx: AgentContext, skill_prompt: str) -> list[dict]:
        slog = get_stage_logger(ctx.pipeline_id, "generation")
        parsed = ctx.extra.get("parsed_requirements", [])
        analysis = ctx.extra.get("analysis_report", {})

        # Count functional requirements for minimum-case validation
        min_expected_cases = 0
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    fr_list = item.get("functional_requirements", [])
                    if isinstance(fr_list, list):
                        min_expected_cases += len(fr_list)
        if min_expected_cases == 0:
            min_expected_cases = max(1, len(parsed))

        max_tokens = max(16384, min_expected_cases * 1024)  # ~1K tokens per case

        requirements_json = json.dumps(parsed, ensure_ascii=False, indent=2)
        analysis_json = json.dumps(analysis, ensure_ascii=False, indent=2)

        user_prompt = (
            "请根据以下需求文档和分析报告，生成全面的测试用例。\n"
            f"要求：至少生成 {min_expected_cases} 条测试用例，覆盖所有功能需求。\n\n"
            "## 需求文档\n\n"
            f"```json\n{requirements_json}\n```\n\n"
            "## 分析报告\n\n"
            f"```json\n{analysis_json}\n```\n\n"
            "请输出 JSON 数组格式的测试用例。"
        )
        if ctx.custom_prompt:
            user_prompt = f"额外要求：{ctx.custom_prompt}\n\n{user_prompt}"

        logger.info(
            "agent_generation_llm_start",
            pipeline_id=ctx.pipeline_id,
            skill_length=len(skill_prompt),
            input_length=len(user_prompt),
            min_expected_cases=min_expected_cases,
        )

        response = await self._retry_llm(
            LLMRequest(
                system_prompt=skill_prompt,
                user_prompt=truncate_prompt(user_prompt, 30000),
                task_tag="generation",
                complexity="high",
                expect_json=True,
                max_tokens=max_tokens,
                pipeline_id=ctx.pipeline_id,
                stage_name="generation",
            ),
            max_retries=0,
            error_prefix="LLM 用例生成",
        )

        cases_data = response.parsed_json

        # Fallback: try to extract JSON from raw content
        if cases_data is None:
            raw = response.content or ""
            slog.warning(f"LLM returned non-JSON content, trying extraction. Raw preview: {raw[:200]}")
            logger.warning(
                "agent_generation_parse_failed_trying_extraction",
                pipeline_id=ctx.pipeline_id,
                model=response.model,
                raw_preview=raw[:2000],
            )
            cases_data = _extract_json_from_text(raw)

        if cases_data is None:
            raw = response.content or ""
            logger.error(
                "agent_generation_parse_failed",
                pipeline_id=ctx.pipeline_id,
                model=response.model,
                raw_preview=raw[:2000],
            )
            raise RuntimeError(f"LLM 返回内容无法解析为 JSON。请检查 LLM API Key，或重试流水线。")

        # Empty list is valid JSON but means LLM generated no cases
        if isinstance(cases_data, list) and len(cases_data) == 0:
            raw = response.content or ""
            raise RuntimeError(f"LLM 返回空用例列表（可能因需求数据不足或上游阶段问题）。请检查文档解析结果是否正常。")
        if isinstance(cases_data, dict):
            cases_data = cases_data.get("test_cases", cases_data.get("cases", [cases_data]))

        if not isinstance(cases_data, list):
            raise RuntimeError(f"Unexpected response format: expected list, got {type(cases_data)}")

        if not cases_data:
            raise RuntimeError("LLM returned empty test case list")

        # Retry with stronger prompt if too few cases generated
        actual_count = len(cases_data)
        expected_min = max(3, min(10, min_expected_cases))
        if actual_count < expected_min:
            slog.warning(
                f"Generated only {actual_count} cases, expected at least {expected_min}. Retrying with stronger prompt..."
            )
            logger.warning(
                "agent_generation_too_few_cases",
                pipeline_id=ctx.pipeline_id,
                actual=actual_count,
                expected=expected_min,
            )
            stronger_user_prompt = (
                f"上一轮只生成了 {actual_count} 条测试用例，数量严重不足。\n\n"
                "必须为每条功能需求至少生成 1 条正常流程用例 + 1 条异常/边界用例。\n"
                f"目标：至少 {expected_min} 条用例，越多越好，覆盖所有功能点。\n\n"
                "请严格按照以下格式输出大量用例（JSON 数组），不要偷懒：\n\n"
                f"```json\n{requirements_json}\n```\n\n"
                "## 分析报告\n\n"
                f"```json\n{analysis_json}\n```"
            )
            retry_response = await self._retry_llm(
                LLMRequest(
                    system_prompt=skill_prompt,
                    user_prompt=truncate_prompt(stronger_user_prompt, 30000),
                    task_tag="generation",
                    complexity="high",
                    expect_json=True,
                    max_tokens=max_tokens,
                    pipeline_id=ctx.pipeline_id,
                    stage_name="generation",
                ),
                max_retries=0,
                error_prefix="LLM 用例生成（强提示重试）",
            )
            retry_data = retry_response.parsed_json
            if retry_data is None:
                raw = retry_response.content or ""
                retry_data = _extract_json_from_text(raw)
            if isinstance(retry_data, dict):
                retry_data = retry_data.get("test_cases", retry_data.get("cases", [retry_data]))
            if isinstance(retry_data, list) and len(retry_data) > actual_count:
                cases_data = retry_data
                slog.info(f"Retry succeeded: {len(cases_data)} cases generated")
            else:
                slog.warning(
                    f"Retry still produced only "
                    f"{len(retry_data) if isinstance(retry_data, list) else 0} cases, "
                    f"using original {actual_count} cases"
                )

        # Validate and normalize test_type
        for c in cases_data:
            if c.get("test_type") not in VALID_TEST_TYPES:
                c["test_type"] = "ui"

        logger.info(
            "agent_generation_llm_done",
            pipeline_id=ctx.pipeline_id,
            case_count=len(cases_data),
            model=response.model,
            latency_ms=response.latency_ms,
        )
        slog.info(f"LLM 调用成功, model={response.model}, 耗时={response.latency_ms}ms, 生成用例数={len(cases_data)}")
        return cases_data

    # ═══════════════════════════════════════════════════════════════
    # Step 3: Save test cases to DB
    # ═══════════════════════════════════════════════════════════════

    async def _save_testcases(self, ctx: AgentContext, cases_data: list[dict]) -> int:
        slog = get_stage_logger(ctx.pipeline_id, "generation")
        async with async_session_factory() as session:
            platform_type = ctx.platform_type

            # Idempotency baseline: if generation is re-run for the same pipeline,
            # deprecate previous active cases to avoid creating confusing duplicates.
            # (Full idempotency keying can be added later via schema + constraints.)
            from sqlalchemy import select as sa_select, update as sa_update

            existing = await session.execute(
                sa_select(TestCase.id).where(
                    TestCase.pipeline_id == ctx.pipeline_id,
                    TestCase.status != "deprecated",
                )
            )
            existing_ids = [r[0] for r in existing.all()]
            if existing_ids:
                await session.execute(
                    sa_update(TestCase)
                    .where(TestCase.pipeline_id == ctx.pipeline_id)
                    .where(TestCase.status != "deprecated")
                    .values(status="deprecated")
                )
                await session.commit()
                slog.info(f"检测到历史用例 {len(existing_ids)} 条，已标记为 deprecated，将写入新一批用例")

            for case_data in cases_data:
                module = module_catalog.resolve(
                    str(
                        case_data.get("module")
                        or case_data.get("title")
                        or case_data.get("description")
                        or ""
                    )
                )
                prepared = prepare_executable_case({**case_data, "module": module})
                tc = TestCase(
                    project_id=ctx.project_id,
                    pipeline_id=ctx.pipeline_id,
                    title=case_data.get("title", "Untitled"),
                    description=case_data.get("description", ""),
                    preconditions=case_data.get("preconditions", ""),
                    steps=case_data.get("steps", []),
                    priority=case_data.get("priority", "medium"),
                    test_type=case_data.get("test_type", "ui"),
                    tags=case_data.get("tags", []),
                    platform_type=case_data.get("platform_type", platform_type),
                    status="pending_review",
                    automation_level=lint_case(prepared)["level"],
                    module=prepared.get("module") or None,
                    exec_script=prepared.get("exec_script"),
                    compile_status=prepared.get("compile_status"),
                    compile_errors=prepared.get("compile_errors") or [],
                    execution_mode=prepared.get("execution_mode"),
                    step_contracts=prepared.get("step_contracts") or [],
                )
                session.add(tc)

            await session.commit()

        logger.info(
            "agent_testcases_saved",
            pipeline_id=ctx.pipeline_id,
            count=len(cases_data),
        )
        slog.info(f"用例已保存到数据库, 数量={len(cases_data)}")
        return len(cases_data)

    # ═══════════════════════════════════════════════════════════════
    # Step 4: Log usage for skill iteration
    # ═══════════════════════════════════════════════════════════════

    async def _log_usage(self, ctx: AgentContext, cases_data: list[dict]) -> None:
        log_dir = "storage/skill-iterations"
        os.makedirs(log_dir, exist_ok=True)

        auto_flags: dict[str, bool] = {}
        if len(cases_data) < 3:
            auto_flags["too_few_cases"] = True
        if all(c.get("priority") in (None, "medium") for c in cases_data):
            auto_flags["all_medium_priority"] = True
        steps_count = sum(len(c.get("steps", [])) for c in cases_data)
        if steps_count < len(cases_data):
            auto_flags["insufficient_steps"] = True

        record = json.dumps({
            "timestamp": datetime.now().isoformat(),
            "pipeline_id": ctx.pipeline_id,
            "platform_type": ctx.platform_type,
            "case_count": len(cases_data),
            "avg_steps": round(steps_count / len(cases_data), 1) if cases_data else 0,
            "auto_flags": auto_flags,
        }, ensure_ascii=False)

        log_path = os.path.join(log_dir, "iterations.jsonl")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(record + "\n")

        logger.info(
            "agent_usage_logged",
            pipeline_id=ctx.pipeline_id,
            case_count=len(cases_data),
            flags=auto_flags,
        )
