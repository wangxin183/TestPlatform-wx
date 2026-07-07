"""Stage 8: Regression extraction — LLM selects test cases for regression suite."""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.models import RegressionCase, TestCase
from src.llm.caller import llm_call
from src.llm.prompts.templates import load_prompt
from src.llm.types import LLMRequest
from src.pipeline.stages.base import AbstractStage, StageInput, StageOutput
from src.utils.logging_config import get_logger
from src.utils.stage_logger import get_stage_logger

logger = get_logger(__name__)


class RegressionStage(AbstractStage):
    stage_name = "regression"

    @classmethod
    def required_context_fields(cls) -> list[str]:
        return []

    @classmethod
    def produced_context_fields(cls) -> list[str]:
        return ["regression_case_ids"]

    def __init__(self, db_session: AsyncSession):
        self._db = db_session

    async def execute(self, stage_input: StageInput) -> StageOutput:
        context = stage_input.context
        slog = get_stage_logger(stage_input.pipeline_id, self.stage_name)
        slog.info(f"========== 回归用例选取阶段开始 ==========")

        # Fetch all test cases with their execution results
        result = await self._db.execute(
            select(TestCase).where(TestCase.project_id == context.project_id)
        )
        test_cases = result.scalars().all()
        slog.info(f"可选用例总数: {len(test_cases)}")

        if not test_cases:
            slog.info("没有可选的用例")
            return StageOutput(
                stage_name=self.stage_name,
                status="completed",
                data={"message": "No test cases to select regression from"},
            )

        # Build context for LLM
        cases_json = json.dumps(
            [
                {
                    "id": tc.id,
                    "title": tc.title,
                    "priority": tc.priority,
                    "test_type": tc.test_type,
                    "tags": tc.tags,
                    "steps": tc.steps,
                }
                for tc in test_cases
            ],
            ensure_ascii=False,
            indent=2,
        )

        prompt_template = load_prompt("regression_selection")
        user_prompt = prompt_template.replace("{test_cases_json}", cases_json)

        response = await llm_call(
            LLMRequest(
                system_prompt="你是回归测试套件管理专家，所有输出必须使用中文。",
                user_prompt=user_prompt,
                task_tag="regression_selection",
                complexity="low",
                expect_json=True,
                pipeline_id=stage_input.pipeline_id,
                stage_name=self.stage_name,
            )
        )

        regression_ids = []

        if response.parsed_json and isinstance(response.parsed_json, list):
            for item in response.parsed_json:
                reg = RegressionCase(
                    project_id=context.project_id,
                    pipeline_id=context.pipeline_id,
                    source_case_id=item.get("source_case_id"),
                    title=item.get("title", ""),
                    steps=item.get("steps", []),
                    priority=item.get("priority", "high"),
                    selection_reason=item.get("selection_reason", ""),
                )
                self._db.add(reg)
                regression_ids.append(item.get("source_case_id"))
        else:
            logger.error(
                "regression_parse_failed",
                pipeline_id=stage_input.pipeline_id,
                model=response.model,
                raw_content=response.content[:2000] if response.content else "",
                parse_type=type(response.parsed_json).__name__,
            )

        await self._db.commit()
        context.regression_case_ids = regression_ids
        
        slog.info(f"========== 回归用例选取阶段完成: 选取了{len(regression_ids)}条 ==========")

        return StageOutput(
            stage_name=self.stage_name,
            status="completed",
            data={
                "regression_cases_selected": len(regression_ids),
                "total_cases_considered": len(test_cases),
                "parse_failed": regression_ids == [] and len(test_cases) > 0,
            },
        )
