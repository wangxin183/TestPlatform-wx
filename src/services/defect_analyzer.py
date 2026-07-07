"""Defect analyzer — post-execution failure analysis + auto defect creation.

Uses LLM (failure_reasoning.txt prompt) to classify each failure as:
- 测试脚本缺陷 (test script issue, NOT a defect)
- 应用缺陷 (app bug → auto-create Defect)
- 环境问题 (environment issue, NOT a defect)
- 数据问题 (data issue, NOT a defect)
- 不稳定测试 (flaky test, NOT a defect)
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.models import Defect, ExecutionResult, TestCase
from src.llm.caller import llm_call
from src.llm.prompts.templates import load_prompt
from src.llm.types import LLMRequest
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class DefectAnalyzer:
    """Analyze failed execution results and auto-create Defect records.

    Usage::

        analyzer = DefectAnalyzer(db_session)
        defects = await analyzer.analyze_execution(execution_id)
    """

    def __init__(self, db_session: AsyncSession):
        self._db = db_session

    async def analyze_execution(self, execution_id: str) -> list[Defect]:
        """Analyze all failed/error results in an execution, create Defects where applicable.

        Returns the list of newly created Defect records.
        """
        # ── Fetch failed/error results ──
        result = await self._db.execute(
            select(ExecutionResult).where(
                ExecutionResult.execution_id == execution_id,
                ExecutionResult.status.in_(["failed", "error"]),
            )
        )
        failed_results = result.scalars().all()

        if not failed_results:
            logger.info("defect_analyzer_no_failures", execution_id=execution_id)
            return []

        logger.info(
            "defect_analyzer_start",
            execution_id=execution_id,
            failed_count=len(failed_results),
        )

        defects: list[Defect] = []

        for er in failed_results:
            try:
                defect = await self._analyze_single_result(er)
                if defect:
                    defects.append(defect)
            except Exception as exc:
                logger.error(
                    "defect_analysis_failed",
                    result_id=er.id,
                    error=str(exc),
                )

        if defects:
            await self._db.commit()
            logger.info(
                "defect_analyzer_done",
                execution_id=execution_id,
                defects_created=len(defects),
            )
        else:
            logger.info(
                "defect_analyzer_no_defects",
                execution_id=execution_id,
                analyzed=len(failed_results),
            )

        return defects

    async def analyze_single_result(self, result_id: str) -> Defect | None:
        """Analyze a single ExecutionResult and create a Defect if applicable."""
        result = await self._db.execute(
            select(ExecutionResult).where(ExecutionResult.id == result_id)
        )
        er = result.scalar_one_or_none()
        if not er:
            return None

        defect = await self._analyze_single_result(er)
        if defect:
            await self._db.commit()
        return defect

    # ═══════════════════════════════════════════════════════════════
    # Internal
    # ═══════════════════════════════════════════════════════════════

    async def _analyze_single_result(self, er: ExecutionResult) -> Defect | None:
        """Analyze one ExecutionResult via LLM + create Defect if is_defect=true."""
        # ── Fetch the associated test case ──
        tc_result = await self._db.execute(
            select(TestCase).where(TestCase.id == er.test_case_id)
        )
        test_case = tc_result.scalar_one_or_none()

        # ── Build prompt inputs ──
        test_case_json = json.dumps({
            "title": test_case.title if test_case else "",
            "description": test_case.description if test_case else "",
            "steps": test_case.steps if test_case else [],
            "test_type": test_case.test_type if test_case else "",
        }, ensure_ascii=False)

        execution_result_json = json.dumps({
            "status": er.status,
            "duration_ms": er.duration_ms,
            "step_results": er.step_results,
        }, ensure_ascii=False)

        error_message = er.error_message or ""

        # ── Load and fill prompt ──
        prompt_template = load_prompt("failure_reasoning")
        if not prompt_template:
            logger.error("defect_analyzer_no_prompt")
            return None

        user_prompt = (
            prompt_template
            .replace("{test_case_json}", test_case_json)
            .replace("{execution_result_json}", execution_result_json)
            .replace("{error_message}", error_message)
        )

        # ── Call LLM ──
        try:
            response = await llm_call(LLMRequest(
                system_prompt="你是测试失败分析专家，所有输出必须使用中文。",
                user_prompt=user_prompt,
                task_tag="failure_reasoning",
                complexity="medium",
                expect_json=True,
                max_tokens=2048,
            ))
        except Exception as exc:
            logger.error("defect_analysis_llm_failed", result_id=er.id, error=str(exc))
            return None

        analysis = response.parsed_json
        if not analysis:
            logger.error("defect_analysis_parse_failed", result_id=er.id)
            return None

        # ── Extract fields ──
        root_cause = analysis.get("root_cause", error_message[:200])
        category = analysis.get("category", "测试脚本缺陷")
        is_defect = analysis.get("is_defect", False)
        severity = analysis.get("severity", "中")
        suggested_fix = analysis.get("suggested_fix", "")
        confidence = analysis.get("confidence", 0.5)

        # ── Update ExecutionResult with failure reason ──
        er.failure_reason = f"[{category}] {root_cause}"
        self._db.add(er)

        logger.info(
            "defect_analysis_result",
            result_id=er.id,
            category=category,
            is_defect=is_defect,
            severity=severity,
            confidence=confidence,
        )

        # ── Create Defect only if it's an application defect ──
        if not is_defect or category != "应用缺陷":
            return None

        defect = Defect(
            execution_result_id=er.id,
            project_id=test_case.project_id if test_case else "",
            execution_id=er.execution_id,
            title=f"【自动发现】{root_cause[:80]}",
            description=(
                f"## 根因分析\n{root_cause}\n\n"
                f"## 分类\n{category}（置信度: {confidence:.0%}）\n\n"
                f"## 建议修复\n{suggested_fix}\n\n"
                f"## 错误详情\n{error_message}"
            ),
            severity=self._map_severity(severity),
            reproduction_steps=test_case.steps if test_case else [],
            evidence_paths=[er.screenshot_path] if er.screenshot_path else [],
            status="open",
        )
        self._db.add(defect)

        logger.info(
            "defect_auto_created",
            defect_title=defect.title,
            severity=defect.severity,
            result_id=er.id,
        )

        return defect

    @staticmethod
    def _map_severity(severity: str) -> str:
        """Normalize severity from LLM output to Defect.severity enum values."""
        mapping = {
            "严重": "critical",
            "高": "high",
            "中": "medium",
            "低": "low",
            "critical": "critical",
            "high": "high",
            "medium": "medium",
            "low": "low",
        }
        return mapping.get(severity, "medium")
