"""Stage 6: Test execution — routes approved cases to platform executors.

Fetches approved test cases from the pipeline context, groups them by
(platform_type, test_type) via ExecutionRouter, dispatches to executors
via ExecutionService, and auto-creates defects for failures via DefectAnalyzer.

Performance/security tests are no longer skipped — they generate scripts/plans
and record "generated" status.
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.models import TestCase
from src.executor.types import StepAction, StepResult
from src.llm.caller import llm_call
from src.llm.types import LLMRequest
from src.pipeline.stages.base import AbstractStage, StageInput, StageOutput
from src.services.execution_router import ExecutionRouter
from src.services.execution_service import ExecutionService
from src.services.defect_analyzer import DefectAnalyzer
from src.utils.logging_config import get_logger
from src.utils.stage_logger import get_stage_logger
from src.ws.manager import ws_manager

logger = get_logger(__name__)

# ── Legacy compatibility constants (used by existing unit tests) ──
EXECUTOR_MAP = {
    "ui": "web",
    "api": "api",
    "compatibility": "compatibility",
}
SKIP_TEST_TYPES = {"performance", "security"}


class ExecutionStage(AbstractStage):
    """Execute approved test cases via the appropriate platform executor.

    Uses ExecutionService (shared with standalone API) for the execution loop,
    and DefectAnalyzer for post-execution failure analysis + auto defect creation.
    """

    stage_name = "execution"

    @classmethod
    def required_context_fields(cls) -> list[str]:
        return ["approved_test_case_ids"]

    @classmethod
    def produced_context_fields(cls) -> list[str]:
        return ["execution_ids"]

    def __init__(self, db_session: AsyncSession):
        self._db = db_session

    async def execute(self, stage_input: StageInput) -> StageOutput:
        context = stage_input.context
        slog = get_stage_logger(stage_input.pipeline_id, self.stage_name)
        slog.info(f"========== 用例执行阶段开始 ==========")

        # ── Fetch approved test cases ──
        approved_ids = context.approved_test_case_ids or []
        if approved_ids:
            result = await self._db.execute(
                select(TestCase).where(TestCase.id.in_(approved_ids))
            )
            test_cases = list(result.scalars().all())
        else:
            # Fallback: query by pipeline + status
            result = await self._db.execute(
                select(TestCase).where(
                    TestCase.pipeline_id == context.pipeline_id,
                    TestCase.status == "approved",
                )
            )
            test_cases = list(result.scalars().all())

        if not test_cases:
            slog.info("没有已审批的用例需要执行")
            return StageOutput(
                stage_name=self.stage_name,
                status="completed",
                data={"message": "No approved test cases to execute"},
            )

        # ── Log routing information ──
        route_counts: dict[str, int] = {}
        for tc in test_cases:
            route = ExecutionRouter.route(tc)
            route_counts[route.subtype_label] = route_counts.get(route.subtype_label, 0) + 1

        slog.info(
            f"待执行用例: {len(test_cases)}条, "
            f"路由分布: {route_counts}"
        )

        # ── Build progress callback → WebSocket ──
        pipeline_id = stage_input.pipeline_id
        target_url = context.project_config.get("target_url", "")
        api_base_url = context.project_config.get("api_base_url", "")

        async def progress_callback(data: dict):
            exec_id = data.get("execution_id", "")
            if exec_id:
                await ws_manager.broadcast_execution_progress(exec_id, data)
            # Also broadcast at pipeline level for pipeline page
            data["pipeline_id"] = pipeline_id
            await ws_manager.broadcast(pipeline_id, {
                "type": "execution_update",
                **data,
            })

        # ── Execute via shared ExecutionService ──
        service = ExecutionService(self._db, progress_callback=progress_callback)
        try:
            summary = await service.execute_cases(
                test_cases=test_cases,
                pipeline_id=pipeline_id,
                project_id=context.project_id,
                target_url=target_url,
                api_base_url=api_base_url,
            )
        except Exception as exc:
            logger.error("execution_service_failed", pipeline_id=pipeline_id, error=str(exc))
            slog.error(f"执行服务异常: {exc}")
            return StageOutput(
                stage_name=self.stage_name,
                status="failed",
                error=str(exc),
                data={
                    "total_cases": len(test_cases),
                    "error": str(exc),
                },
            )

        # ── Auto-analyze failures → create defects ──
        analyzer = DefectAnalyzer(self._db)
        all_defects = []
        for eid in summary.execution_ids:
            try:
                defects = await analyzer.analyze_execution(eid)
                all_defects.extend(defects)
            except Exception as exc:
                logger.error("defect_analysis_failed", execution_id=eid, error=str(exc))

        slog.info(
            f"自动缺陷分析: 发现 {len(all_defects)} 个缺陷"
        )

        # ── Store execution IDs in context ──
        context.execution_ids = summary.execution_ids

        # ── Notify completion ──
        for eid in summary.execution_ids:
            await ws_manager.broadcast_execution_complete(eid, {
                "execution_id": eid,
                "total_cases": summary.total_cases,
                "passed": summary.passed,
                "failed": summary.failed,
                "error": summary.error,
                "generated": summary.generated,
                "defects_created": len(all_defects),
            })

        # ── Build result message ──
        result_msg_parts = [
            f"执行完成: {summary.total_cases} 条用例",
            f"通过: {summary.passed}, 失败: {summary.failed}, 错误: {summary.error}",
        ]
        if summary.generated > 0:
            result_msg_parts.append(f"脚本生成: {summary.generated} 条")
        if all_defects:
            result_msg_parts.append(f"自动提交缺陷: {len(all_defects)} 个")

        slog.info(f"========== 用例执行阶段完成: {'; '.join(result_msg_parts)} ==========")
        logger.info(
            "execution_stage_done",
            pipeline_id=pipeline_id,
            passed=summary.passed,
            failed=summary.failed,
            error=summary.error,
            generated=summary.generated,
            defects=len(all_defects),
        )

        return StageOutput(
            stage_name=self.stage_name,
            status="completed",
            data={
                "executions_created": len(summary.execution_ids),
                "total_cases": summary.total_cases,
                "passed": summary.passed,
                "failed": summary.failed,
                "error": summary.error,
                "generated": summary.generated,
                "defects_created": len(all_defects),
                "skipped": summary.skipped_info if summary.skipped_info else None,
                "route_distribution": route_counts,
            },
        )

    # ═══════════════════════════════════════════════════════════════
    # Legacy helpers (kept for tests / backwards compatibility)
    # ═══════════════════════════════════════════════════════════════

    async def _translate_steps(self, test_case: TestCase, target_url: str) -> list[StepAction]:
        """NL → structured steps (kept for unit tests).

        The current runtime path uses ExecutionService._translate_steps(); this wrapper
        exists to preserve previous behavior and tests that patch llm_call at
        src.pipeline.stages.execution.llm_call.
        """
        if not getattr(test_case, "steps", None):
            return []

        test_type = getattr(test_case, "test_type", "ui") or "ui"
        subtype = "api" if test_type == "api" else "web_ui"
        system_prompt = ExecutionService._get_translation_prompt(subtype)

        user_prompt = (
            f"目标 URL: {target_url}\n"
            f"用例标题: {getattr(test_case, 'title', '')}\n"
            f"用例描述: {getattr(test_case, 'description', '')}\n"
            f"操作步骤:\n{json.dumps(getattr(test_case, 'steps', []), ensure_ascii=False, indent=2)}\n\n"
            "请翻译为结构化步骤数组。"
        )

        response = await llm_call(LLMRequest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            task_tag="step_translation",
            complexity="medium",
            expect_json=True,
            max_tokens=4096,
        ))

        parsed = response.parsed_json or []
        if isinstance(parsed, dict):
            parsed = parsed.get("steps", [parsed])

        actions: list[StepAction] = []
        for item in parsed:
            actions.append(StepAction(
                step_number=item.get("step", len(actions) + 1),
                action_type=item.get("action_type", ""),
                target=item.get("target"),
                value=item.get("value"),
                timeout_ms=item.get("timeout_ms", 30000),
            ))
        return actions

    @staticmethod
    def _summarize(step_results: list[StepResult]) -> tuple[str, str | None]:
        """Determine pass/fail/error from step results (kept for unit tests)."""
        failed_steps = [r for r in step_results if r.status == "failed"]
        error_steps = [r for r in step_results if r.status == "error"]

        if error_steps:
            return "error", error_steps[0].error_message
        if failed_steps:
            return "failed", (
                f"{len(failed_steps)} step(s) failed: "
                + "; ".join(r.error_message or f"step {r.step_number}" for r in failed_steps)
            )
        return "passed", None
