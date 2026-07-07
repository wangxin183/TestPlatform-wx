"""Execution service — reusable test execution logic.

Extracted from ExecutionStage so both the pipeline and the standalone
execution API can share the same execute→translate→dispatch→record loop.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.models.models import Execution, ExecutionResult, TestCase, TestSuite
from src.executor.registry import ExecutorRegistry
from src.executor.types import ExecutorConfig, StepAction, StepResult
from src.llm.caller import llm_call
from src.llm.prompts.templates import load_prompt
from src.llm.types import LLMRequest
from src.services.execution_router import ExecutionRouter, RouteResult
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# ── Progress callback type ──
ProgressCallback = Optional[Callable[[dict[str, Any]], Any]]


@dataclass
class ExecutionSummary:
    """Aggregated result of an execution run."""
    execution_ids: list[str] = field(default_factory=list)
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    error: int = 0
    generated: int = 0   # performance / security scripts generated
    skipped_info: dict[str, str] = field(default_factory=dict)


class ExecutionService:
    """Execute a batch of test cases, handling routing, translation, and recording.

    Usage::

        service = ExecutionService(db_session, progress_callback=my_callback)
        summary = await service.execute_cases(
            test_cases=cases,
            pipeline_id="...",
            project_id="...",
            target_url="https://...",
            api_base_url="https://...",
        )
    """

    def __init__(
        self,
        db_session: AsyncSession,
        progress_callback: ProgressCallback = None,
    ):
        self._db = db_session
        self._progress = progress_callback

    # ═══════════════════════════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════════════════════════

    async def execute_cases(
        self,
        test_cases: list[TestCase],
        pipeline_id: str = "",
        project_id: str = "",
        target_url: str = "",
        api_base_url: str = "",
    ) -> ExecutionSummary:
        """Execute a list of test cases and return an aggregated summary.

        Groups cases by their ExecutionRouter route, creates Execution
        records, translates NL steps, dispatches to executors, and
        records per-case results.
        """
        summary = ExecutionSummary(total_cases=len(test_cases))

        if not test_cases:
            return summary

        # ── Group cases by route ──
        grouped: dict[RouteResult, list[TestCase]] = {}
        for tc in test_cases:
            route = ExecutionRouter.route(tc)
            grouped.setdefault(route, []).append(tc)

        logger.info(
            "execution_service_start",
            pipeline_id=pipeline_id,
            total_cases=len(test_cases),
            groups=len(grouped),
            route_labels=[r.subtype_label for r in grouped],
        )

        platforms_config = settings.platforms_config.get("platforms", {})

        # ── Execute each group ──
        for route, cases in grouped.items():
            group_summary = await self._execute_group(
                route=route,
                cases=cases,
                pipeline_id=pipeline_id,
                project_id=project_id,
                target_url=target_url,
                api_base_url=api_base_url,
                platforms_config=platforms_config,
            )
            summary.execution_ids.extend(group_summary.execution_ids)
            summary.passed += group_summary.passed
            summary.failed += group_summary.failed
            summary.error += group_summary.error
            summary.generated += group_summary.generated
            summary.skipped_info.update(group_summary.skipped_info)

        await self._db.commit()

        logger.info(
            "execution_service_done",
            pipeline_id=pipeline_id,
            passed=summary.passed,
            failed=summary.failed,
            error=summary.error,
            generated=summary.generated,
        )

        return summary

    # ═══════════════════════════════════════════════════════════════
    # Group execution
    # ═══════════════════════════════════════════════════════════════

    async def _execute_group(
        self,
        route: RouteResult,
        cases: list[TestCase],
        pipeline_id: str,
        project_id: str,
        target_url: str,
        api_base_url: str,
        platforms_config: dict,
    ) -> ExecutionSummary:
        """Execute a group of test cases sharing the same RouteResult."""
        summary = ExecutionSummary(total_cases=len(cases))

        # ── Create TestSuite + Execution records ──
        suite = TestSuite(
            project_id=project_id or cases[0].project_id,
            pipeline_id=pipeline_id,
            name=f"Execution - {route.subtype_label}",
            description=f"Auto-generated suite for {route.subtype_label}",
            test_case_ids=[tc.id for tc in cases],
        )
        self._db.add(suite)
        await self._db.flush()

        execution = Execution(
            test_suite_id=suite.id,
            project_id=project_id or cases[0].project_id,
            pipeline_id=pipeline_id,
            executor_type=route.subtype_label,
            status="running",
            total_cases=len(cases),
            max_retries=2,
        )
        self._db.add(execution)
        await self._db.flush()

        # ── Get executor ──
        try:
            executor = ExecutorRegistry.get(route.executor_name)
        except ValueError as exc:
            logger.error("executor_not_found", executor_name=route.executor_name, error=str(exc))
            await self._mark_all_error(execution.id, cases, f"No executor: {exc}")
            execution.error_cases = len(cases)
            execution.status = "completed"
            await self._db.flush()
            summary.execution_ids.append(execution.id)
            summary.error += len(cases)
            return summary

        # ── Build config ──
        platform_type = cases[0].platform_type if cases else ""
        platform_cfg = platforms_config.get(platform_type, {})
        default_cfg = platform_cfg.get("default_config", {})

        executor_config = ExecutorConfig(
            platform_type=route.subtype_label,
            target_url=target_url,
            api_base_url=api_base_url,
            capabilities=default_cfg,
            timeout_seconds=default_cfg.get("timeout_seconds", 60),
            max_retries=2,
        )

        # ── Setup executor ──
        try:
            await executor.setup(executor_config)
        except Exception as exc:
            logger.error("executor_setup_failed", route=route.subtype_label, error=str(exc))
            await self._mark_all_error(execution.id, cases, f"Setup failed: {exc}")
            execution.error_cases = len(cases)
            execution.status = "completed"
            await self._db.flush()
            summary.execution_ids.append(execution.id)
            summary.error += len(cases)
            return summary

        # ── Handle performance/security: generate scripts instead of executing ──
        if route.subtype_label in ("performance", "security"):
            return await self._execute_generated_group(
                route=route,
                cases=cases,
                execution=execution,
                target_url=target_url,
            )

        # ── Execute each case ──
        passed = 0
        failed = 0
        error = 0
        generated = 0

        for idx, tc in enumerate(cases):
            await self._notify_progress(execution.id, {
                "type": "case_start",
                "case_title": tc.title,
                "case_id": tc.id,
                "current": idx + 1,
                "total": len(cases),
            })

            # ── NL → structured translation ──
            try:
                step_actions = await self._translate_steps(tc, target_url, route)
            except Exception as exc:
                logger.error("step_translation_failed", case_id=tc.id, error=str(exc))
                er = ExecutionResult(
                    execution_id=execution.id,
                    test_case_id=tc.id,
                    attempt=1,
                    status="error",
                    duration_ms=0,
                    error_message=f"Step translation failed: {exc}",
                    step_results=[],
                )
                self._db.add(er)
                error += 1
                await self._notify_progress(execution.id, {
                    "type": "case_result",
                    "case_title": tc.title,
                    "case_id": tc.id,
                    "status": "error",
                    "current": idx + 1,
                    "total": len(cases),
                })
                continue

            if not step_actions:
                er = ExecutionResult(
                    execution_id=execution.id,
                    test_case_id=tc.id,
                    attempt=1,
                    status="passed",
                    duration_ms=0,
                    step_results=[],
                )
                self._db.add(er)
                passed += 1
                continue

            # ── Execute steps ──
            t0 = time.monotonic()
            try:
                step_results = await self._execute_with_timeout(
                    executor, step_actions, route
                )
            except asyncio.TimeoutError:
                er = ExecutionResult(
                    execution_id=execution.id,
                    test_case_id=tc.id,
                    attempt=1,
                    status="error",
                    duration_ms=(time.monotonic() - t0) * 1000,
                    error_message=f"Execution timed out",
                    step_results=[],
                )
                self._db.add(er)
                error += 1
                continue
            except Exception as exc:
                er = ExecutionResult(
                    execution_id=execution.id,
                    test_case_id=tc.id,
                    attempt=1,
                    status="error",
                    duration_ms=(time.monotonic() - t0) * 1000,
                    error_message=str(exc),
                    step_results=[],
                )
                self._db.add(er)
                error += 1
                continue

            # ── Summarize ──
            status, error_message = self._summarize(step_results)
            er = ExecutionResult(
                execution_id=execution.id,
                test_case_id=tc.id,
                attempt=1,
                status=status,
                duration_ms=(time.monotonic() - t0) * 1000,
                error_message=error_message,
                step_results=[
                    {
                        "step": r.step_number,
                        "action": r.status,
                        "result": r.actual_result,
                        "error": r.error_message,
                        "screenshot_path": r.screenshot_path,
                        "browser_config": r.browser_config,
                    }
                    for r in step_results
                ],
            )
            self._db.add(er)

            if status == "passed":
                passed += 1
            elif status == "failed":
                failed += 1
            else:
                error += 1

            await self._notify_progress(execution.id, {
                "type": "case_result",
                "case_title": tc.title,
                "case_id": tc.id,
                "status": status,
                "current": idx + 1,
                "total": len(cases),
            })

        execution.passed_cases = passed
        execution.failed_cases = failed
        execution.error_cases = error
        execution.status = "completed"
        await self._db.flush()
        summary.execution_ids.append(execution.id)
        summary.passed += passed
        summary.failed += failed
        summary.error += error
        summary.generated += generated

        # ── Notify group complete ──
        await self._notify_progress(execution.id, {
            "type": "group_complete",
            "execution_id": execution.id,
            "passed": passed,
            "failed": failed,
            "error": error,
        })

        # ── Teardown ──
        try:
            await executor.teardown()
        except Exception:
            pass

        return summary

    # ═══════════════════════════════════════════════════════════════
    # Generated group (performance / security)
    # ═══════════════════════════════════════════════════════════════

    async def _execute_generated_group(
        self,
        route: RouteResult,
        cases: list[TestCase],
        execution: Execution,
        target_url: str,
    ) -> ExecutionSummary:
        """Handle performance/security test cases by generating scripts/plans.

        These test types are not directly executable — instead we generate
        artifacts (Locust scripts, test plans) and record "generated" status.
        """
        summary = ExecutionSummary(total_cases=len(cases))
        generated = 0
        error = 0

        for idx, tc in enumerate(cases):
            await self._notify_progress(execution.id, {
                "type": "case_start",
                "case_title": tc.title,
                "case_id": tc.id,
                "current": idx + 1,
                "total": len(cases),
            })

            try:
                if route.subtype_label == "performance":
                    # ── Generate performance plan + Locust script ──
                    from src.generators.performance_script_generator import PerformanceScriptGenerator
                    gen = PerformanceScriptGenerator()
                    plan_path, script_path = await gen.generate_and_save(tc, target_url)
                    script_paths = [plan_path, script_path]
                    result_note = f"性能测试方案: {plan_path}\nLocust 脚本: {script_path}"
                elif route.subtype_label == "security":
                    # ── Generate security test plan ──
                    script_paths = []
                    result_note = "安全测试方案已生成（需人工审查后执行）"
                else:
                    script_paths = []
                    result_note = f"方案已生成: {route.subtype_label}"

                er = ExecutionResult(
                    execution_id=execution.id,
                    test_case_id=tc.id,
                    attempt=1,
                    status="generated",
                    duration_ms=0,
                    generated_script_path=script_paths[0] if script_paths else None,
                    step_results=[{
                        "step": 1,
                        "action": "generated",
                        "result": result_note,
                        "error": None,
                        "screenshot_path": None,
                        "browser_config": None,
                    }],
                )
                self._db.add(er)
                generated += 1

                await self._notify_progress(execution.id, {
                    "type": "case_result",
                    "case_title": tc.title,
                    "case_id": tc.id,
                    "status": "generated",
                    "current": idx + 1,
                    "total": len(cases),
                })

            except Exception as exc:
                logger.error("generated_group_failed", case_id=tc.id, error=str(exc))
                er = ExecutionResult(
                    execution_id=execution.id,
                    test_case_id=tc.id,
                    attempt=1,
                    status="error",
                    duration_ms=0,
                    error_message=f"Generation failed: {exc}",
                    step_results=[],
                )
                self._db.add(er)
                error += 1

        execution.passed_cases = 0
        execution.failed_cases = 0
        execution.error_cases = error
        execution.status = "completed"
        await self._db.flush()
        summary.execution_ids.append(execution.id)
        summary.generated += generated
        summary.error += error

        await self._notify_progress(execution.id, {
            "type": "group_complete",
            "execution_id": execution.id,
            "passed": 0,
            "failed": 0,
            "error": error,
            "generated": generated,
        })

        return summary

    # ═══════════════════════════════════════════════════════════════
    # NL → Structured step translation
    # ═══════════════════════════════════════════════════════════════

    async def _translate_steps(
        self,
        test_case: TestCase,
        target_url: str,
        route: RouteResult,
    ) -> list[StepAction]:
        """Translate natural-language steps to structured StepAction via LLM.

        Selects the appropriate system prompt based on route.subtype_label.
        """
        if not test_case.steps:
            return []

        system_prompt = self._get_translation_prompt(route.subtype_label)

        user_prompt = (
            f"目标 URL: {target_url}\n"
            f"用例标题: {test_case.title}\n"
            f"用例描述: {test_case.description}\n"
            f"操作步骤:\n{json.dumps(test_case.steps, ensure_ascii=False, indent=2)}\n\n"
            "请翻译为结构化步骤数组。"
        )

        try:
            response = await llm_call(LLMRequest(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                task_tag="step_translation",
                complexity="medium",
                expect_json=True,
                max_tokens=4096,
            ))
        except Exception as exc:
            logger.error("translation_llm_failed", case_id=test_case.id, error=str(exc))
            raise

        actions: list[StepAction] = []
        parsed = response.parsed_json or []
        if isinstance(parsed, dict):
            parsed = parsed.get("steps", [parsed])

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
    def _get_translation_prompt(subtype_label: str) -> str:
        """Return the appropriate system prompt for step translation."""
        prompt_map = {
            "web_ui": "step_translation_web_ui",
            "app_ui": "step_translation_app_ui",
            "api": "step_translation_api",
            "compatibility": "step_translation_web_ui",
        }
        prompt_name = prompt_map.get(subtype_label, "step_translation_web_ui")
        prompt = load_prompt(prompt_name)
        if prompt:
            return prompt

        # ── Built-in fallbacks ──
        if subtype_label in ("app_ui",):
            return (
                "你是移动端 UI 自动化工程师。将自然语言操作的步骤翻译为 Appium 可执行的结构化指令。\n"
                "输出格式：[{\"step\": 1, \"action_type\": \"...\", \"target\": \"...\", \"value\": \"...\", \"timeout_ms\": 30000}]\n"
                "action_type 可选值：click（点击元素）、input（输入文本）、"
                "assert（断言元素存在/文本）、swipe（滑动）、wait（等待）、screenshot（截图）。\n"
                "target 优先使用 accessibility_id 或 XPath。对断言步骤优先用截图+OCR 验证。"
            )
        if subtype_label == "api":
            return (
                "你是接口测试工程师。将自然语言接口调用翻译为结构化指令。\n"
                "输出格式：[{\"step\": 1, \"action_type\": \"api_call\", \"target\": \"/path\", \"value\": \"GET|POST|PUT|DELETE\", \"timeout_ms\": 30000}]\n"
                "action_type 可选值：api_call（发送 HTTP 请求）、assert（断言响应状态码/JSON Schema）。\n"
                "value 为 HTTP 方法名（GET/POST/PUT/DELETE）或预期状态码（如 200）。"
            )
        if subtype_label in ("performance", "security"):
            return (
                "你是测试方案生成专家。将测试需求翻译为结构化方案步骤。\n"
                "输出格式：[{\"step\": 1, \"action_type\": \"generate\", \"target\": \"...\", \"value\": \"...\", \"timeout_ms\": 30000}]\n"
                "用于生成测试脚本或测试方案文档。"
            )
        # Default: web UI
        return (
            "你是 Web 自动化工程师。将自然语言操作的步骤翻译为 Playwright 可执行的结构化指令。\n"
            "输出格式：[{\"step\": 1, \"action_type\": \"...\", \"target\": \"...\", \"value\": \"...\", \"timeout_ms\": 30000}]\n"
            "action_type 可选值：navigate（页面跳转）、click（点击元素）、input（输入文本）、"
            "assert（断言元素存在/文本）、wait（等待）、scroll（滚动）、screenshot（截图）。\n"
            "target 使用 CSS 选择器或 XPath。对断言步骤使用 DOM + 截图 OCR 双重验证。"
        )

    # ═══════════════════════════════════════════════════════════════
    # Execution helpers
    # ═══════════════════════════════════════════════════════════════

    async def _execute_with_timeout(
        self, executor, actions: list[StepAction], route: RouteResult
    ) -> list[StepResult]:
        """Execute steps with a configurable timeout."""
        timeout = settings.pipeline.execution_timeout_minutes * 60
        try:
            return await asyncio.wait_for(
                executor.execute_steps(actions),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.error("execution_timeout", route=route.subtype_label, timeout=timeout)
            raise

    @staticmethod
    def _summarize(step_results: list[StepResult]) -> tuple[str, str | None]:
        """Determine pass/fail/error from step results."""
        failed_steps = [r for r in step_results if r.status == "failed"]
        error_steps = [r for r in step_results if r.status == "error"]

        if error_steps:
            return "error", error_steps[0].error_message
        elif failed_steps:
            return "failed", (
                f"{len(failed_steps)} step(s) failed: "
                + "; ".join(r.error_message or f"step {r.step_number}" for r in failed_steps)
            )
        return "passed", None

    async def _mark_all_error(
        self, execution_id: str, cases: list[TestCase], message: str
    ) -> None:
        """Create error ExecutionResult records for all cases in a group."""
        for tc in cases:
            er = ExecutionResult(
                execution_id=execution_id,
                test_case_id=tc.id,
                attempt=1,
                status="error",
                error_message=message,
                step_results=[],
            )
            self._db.add(er)

    async def _notify_progress(self, execution_id: str, data: dict) -> None:
        """Notify progress via callback if set."""
        if self._progress:
            data["execution_id"] = execution_id
            try:
                await self._progress(data)
            except Exception:
                pass
