"""Stage 4: Test case generation — delegates to TestCaseGeneratorAgent.

The agent loads the test-case-generator skill and generates
comprehensive test cases via LLM (with retries). This stage is a thin
adapter that converts PipelineContext ↔ AgentContext/AgentOutput.
"""

from __future__ import annotations

from src.llm.agents.base import AgentContext
from src.llm.agents.testcase_generator_agent import TestCaseGeneratorAgent
from src.pipeline.stages.base import AbstractStage, StageInput, StageOutput
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class GenerationStage(AbstractStage):
    stage_name = "generation"

    @classmethod
    def required_context_fields(cls) -> list[str]:
        return ["parsed_requirements", "analysis_report"]

    @classmethod
    def produced_context_fields(cls) -> list[str]:
        return ["generated_test_cases"]

    def __init__(self, db_session=None):
        self._db = db_session

    async def execute(self, stage_input: StageInput) -> StageOutput:
        context = stage_input.context
        pid = stage_input.pipeline_id
        project_id = stage_input.project_id

        if not context.parsed_requirements:
            logger.error("generation_no_input", pipeline_id=pid)
            return StageOutput(
                stage_name=self.stage_name,
                status="failed",
                error="No parsed requirements for test case generation",
            )

        platform_type = context.project_config.get("platform_type", "")

        logger.info(
            "generation_start",
            pipeline_id=pid,
            project_id=project_id,
            platform_type=platform_type,
            req_count=len(context.parsed_requirements),
            has_analysis=bool(context.analysis_report),
            custom_prompt=bool(context.custom_prompt),
        )

        # Delegate all work to the agent
        agent = TestCaseGeneratorAgent()
        output = await agent.run(AgentContext(
            pipeline_id=pid,
            project_id=project_id,
            platform_type=platform_type,
            custom_prompt=context.custom_prompt,
            extra={
                "parsed_requirements": context.parsed_requirements,
                "analysis_report": context.analysis_report or {},
            },
        ))

        if not output.success:
            logger.error(
                "generation_agent_failed",
                pipeline_id=pid,
                error=output.error,
                failed_step=output.data.get("failed_step", "unknown"),
            )
            return StageOutput(
                stage_name=self.stage_name,
                status="failed",
                error=output.error,
                data=output.data,
            )

        # Populate context for downstream stages
        context.generated_test_cases = output.data.get("test_cases", [])

        logger.info(
            "generation_done",
            pipeline_id=pid,
            cases_generated=output.data.get("test_cases_generated", 0),
            priorities=output.data.get("priorities", {}),
        )

        return StageOutput(
            stage_name=self.stage_name,
            status="completed",
            data={
                "test_cases_generated": output.data.get("test_cases_generated", 0),
                "test_cases": output.data.get("test_cases", []),
                "priorities": output.data.get("priorities", {}),
            },
        )
