"""Performance test script generator — generates Locust load test scripts + plans.

Uses LLM to analyze performance test requirements and produce:
1. A structured test plan (concurrency, duration, metrics)
2. A Locust Python script for load testing
"""

from __future__ import annotations

import json
from pathlib import Path

from src.core.config import settings
from src.core.models.models import TestCase
from src.llm.caller import llm_call
from src.llm.prompts.templates import load_prompt
from src.llm.types import LLMRequest
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

SCRIPTS_DIR = Path(settings.storage.root) / "scripts"


class PerformanceScriptGenerator:
    """Generate performance test plans and Locust scripts from NL test cases.

    Usage::

        gen = PerformanceScriptGenerator()
        plan, script_path = await gen.generate_and_save(test_case, "https://app.example.com")
    """

    async def generate_plan(self, test_case: TestCase, target_url: str) -> dict:
        """Generate a structured performance test plan as a dict."""
        prompt_template = load_prompt("performance_script_generation")
        if not prompt_template:
            prompt_template = self._default_plan_prompt()

        user_prompt = (
            prompt_template
            .replace("{test_case_title}", test_case.title)
            .replace("{test_case_description}", test_case.description or "")
            .replace("{test_case_steps}", json.dumps(test_case.steps, ensure_ascii=False, indent=2))
            .replace("{target_url}", target_url)
        )

        try:
            response = await llm_call(LLMRequest(
                system_prompt="你是资深性能测试工程师，所有输出必须使用中文。",
                user_prompt=user_prompt,
                task_tag="performance_plan",
                complexity="high",
                expect_json=True,
                max_tokens=4096,
            ))
            return response.parsed_json or {}
        except Exception as exc:
            logger.error("performance_plan_generation_failed", case_id=test_case.id, error=str(exc))
            raise

    async def generate_locust_script(
        self,
        test_case: TestCase,
        target_url: str,
    ) -> str:
        """Generate a complete Locust load test Python script."""
        prompt_template = load_prompt("performance_script_generation")

        user_prompt = (
            "请根据以下性能测试需求，生成一个完整的 Locust 性能测试脚本（Python）。\n\n"
            f"## 测试用例\n"
            f"标题: {test_case.title}\n"
            f"描述: {test_case.description}\n"
            f"步骤: {json.dumps(test_case.steps, ensure_ascii=False, indent=2)}\n\n"
            f"## 目标 URL\n{target_url}\n\n"
            "## 脚本要求\n"
            "- 继承 locust.HttpUser\n"
            "- 包含 @task 装饰的测试任务\n"
            "- 使用 wait_time 模拟用户思考时间\n"
            "- 包含断言（检查响应状态码和内容）\n"
            "- 包含清晰的注释（中文）\n"
            "- 可直接运行\n\n"
            "只输出 Python 代码，不要输出其他内容。"
        )

        try:
            response = await llm_call(LLMRequest(
                system_prompt="你是资深性能测试工程师和 Python 开发专家，精通 Locust 框架。所有注释使用中文。",
                user_prompt=user_prompt,
                task_tag="performance_script",
                complexity="high",
                expect_json=False,
                max_tokens=8192,
            ))
            return response.content or ""
        except Exception as exc:
            logger.error("performance_script_generation_failed", case_id=test_case.id, error=str(exc))
            raise

    async def generate_and_save(
        self,
        test_case: TestCase,
        target_url: str,
    ) -> tuple[str, str]:
        """Generate plan + Locust script, save both to storage.

        Returns (plan_json_path, script_py_path).
        """
        # ── Generate plan ──
        plan = await self.generate_plan(test_case, target_url)

        project_dir = SCRIPTS_DIR / (test_case.project_id or "unknown")
        project_dir.mkdir(parents=True, exist_ok=True)

        # ── Save plan JSON ──
        plan_path = project_dir / f"{test_case.id}_plan.json"
        plan_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # ── Generate and save Locust script ──
        script = await self.generate_locust_script(test_case, target_url)
        script_path = project_dir / f"{test_case.id}_locust.py"
        script_path.write_text(script, encoding="utf-8")

        logger.info(
            "performance_script_saved",
            case_id=test_case.id,
            plan_path=str(plan_path),
            script_path=str(script_path),
            script_length=len(script),
        )

        return str(plan_path), str(script_path)

    @staticmethod
    def _default_plan_prompt() -> str:
        return (
            "根据以下性能测试需求，生成结构化的性能测试方案。\n\n"
            "测试用例标题: {test_case_title}\n"
            "测试用例描述: {test_case_description}\n"
            "测试步骤: {test_case_steps}\n"
            "目标 URL: {target_url}\n\n"
            "输出 JSON 对象:\n"
            "{\n"
            '  "test_scenarios": [{"name": "...", "description": "...", "endpoint": "...", "method": "GET"}],\n'
            '  "concurrency": {"min_users": 10, "max_users": 100, "ramp_up_seconds": 60},\n'
            '  "duration": {"steady_state_seconds": 300, "cool_down_seconds": 30},\n'
            '  "metrics": ["response_time_p95", "error_rate", "throughput", "cpu_usage"],\n'
            '  "success_criteria": {"p95_response_time_ms": 500, "error_rate_pct": 1.0},\n'
            '  "risk_areas": ["..."],\n'
            '  "summary": "方案简述（中文）"\n'
            "}\n\n"
            "只输出 JSON 对象。"
        )
