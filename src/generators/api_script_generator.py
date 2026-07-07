"""API test script generator — translates NL API test steps to runnable Python scripts.

Uses LLM to generate complete, standalone httpx + jsonschema test scripts.
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


class APIScriptGenerator:
    """Generate executable Python async test scripts from NL API test steps.

    Usage::

        gen = APIScriptGenerator()
        script_path = await gen.generate_and_save(test_case, "https://api.example.com", {})
    """

    async def generate_script(
        self,
        test_case: TestCase,
        base_url: str,
        headers: dict | None = None,
    ) -> str:
        """Generate a complete Python async test script and return it as a string."""
        prompt_template = load_prompt("api_script_generation")
        if not prompt_template:
            prompt_template = self._default_prompt()

        user_prompt = (
            prompt_template
            .replace("{test_case_title}", test_case.title)
            .replace("{test_case_description}", test_case.description or "")
            .replace("{test_case_steps}", json.dumps(test_case.steps, ensure_ascii=False, indent=2))
            .replace("{base_url}", base_url)
            .replace("{headers}", json.dumps(headers or {}, ensure_ascii=False))
        )

        try:
            response = await llm_call(LLMRequest(
                system_prompt="你是资深 API 测试工程师和 Python 开发专家，所有输出必须使用中文注释。",
                user_prompt=user_prompt,
                task_tag="api_script_generation",
                complexity="high",
                expect_json=False,
                max_tokens=8192,
            ))
            return response.content or ""
        except Exception as exc:
            logger.error("api_script_generation_failed", case_id=test_case.id, error=str(exc))
            raise

    async def generate_and_save(
        self,
        test_case: TestCase,
        base_url: str,
        headers: dict | None = None,
    ) -> str:
        """Generate a script and save it to storage/scripts/{project_id}/{case_id}.py.

        Returns the file path.
        """
        script = await self.generate_script(test_case, base_url, headers)

        project_dir = SCRIPTS_DIR / (test_case.project_id or "unknown")
        project_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{test_case.id}.py"
        filepath = project_dir / filename
        filepath.write_text(script, encoding="utf-8")

        logger.info(
            "api_script_saved",
            case_id=test_case.id,
            path=str(filepath),
            script_length=len(script),
        )

        return str(filepath)

    @staticmethod
    def _default_prompt() -> str:
        return (
            "根据以下测试用例生成一个完整的 Python 异步测试脚本。\n\n"
            "## 测试用例\n"
            "标题: {test_case_title}\n"
            "描述: {test_case_description}\n"
            "步骤: {test_case_steps}\n\n"
            "## 目标 API\n"
            "Base URL: {base_url}\n"
            "Headers: {headers}\n\n"
            "## 脚本要求\n"
            "- 使用 httpx.AsyncClient 进行 HTTP 请求\n"
            "- 使用 jsonschema 进行响应验证\n"
            "- 包含断言逻辑（状态码、响应体字段、JSON Schema）\n"
            "- 包含清晰的注释（中文）\n"
            "- 包含错误处理和日志输出\n"
            "- 可直接独立运行\n\n"
            "输出完整的 Python 代码，不要输出其他内容。"
        )
