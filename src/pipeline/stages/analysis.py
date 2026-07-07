"""Stage 3: Requirement analysis — delegates to RequirementAgent.

The agent loads the requirement-analyzer skill and generates a
comprehensive test plan via LLM (with retries). This stage is a thin
adapter that converts PipelineContext ↔ AgentContext/AgentOutput.
"""

from __future__ import annotations

from src.llm.agents.base import AgentContext
from src.llm.agents.requirement_agent import RequirementAgent
from src.pipeline.stages.base import AbstractStage, StageInput, StageOutput
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class AnalysisStage(AbstractStage):
    stage_name = "analysis"

    @classmethod
    def required_context_fields(cls) -> list[str]:
        return ["parsed_requirements"]

    @classmethod
    def produced_context_fields(cls) -> list[str]:
        return ["analysis_report", "test_plan_md", "test_plan_file", "performance_plan", "security_plan"]

    def __init__(self, db_session=None):
        self._db = db_session

    async def execute(self, stage_input: StageInput) -> StageOutput:
        context = stage_input.context
        pid = stage_input.pipeline_id
        project_id = stage_input.project_id

        if not context.parsed_requirements:
            logger.error("analysis_no_input", pipeline_id=pid)
            return StageOutput(
                stage_name=self.stage_name,
                status="failed",
                error="文档解析阶段未产生任何需求数据",
            )

        platform_type = context.project_config.get("platform_type", "")

        logger.info(
            "analysis_start",
            pipeline_id=pid,
            project_id=project_id,
            platform_type=platform_type,
            req_count=len(context.parsed_requirements),
            custom_prompt=bool(context.custom_prompt),
        )

        # Delegate all work to the agent
        agent = RequirementAgent()
        output = await agent.run(AgentContext(
            pipeline_id=pid,
            project_id=project_id,
            platform_type=platform_type,
            custom_prompt=context.custom_prompt,
            extra={
                "parsed_requirements": context.parsed_requirements,
            },
        ))

        if not output.success:
            logger.error(
                "analysis_agent_failed",
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
        test_plan_md = output.data.get("test_plan_md", "")
        test_plan_file = output.data.get("test_plan_file", "")
        skill_prompt = output.data.get("skill_prompt", "")

        context.test_plan_md = test_plan_md
        context.test_plan_file = test_plan_file
        context.analysis_report = {
            "test_plan_md": test_plan_md,
            "test_plan_file": test_plan_file,
            "requirements_count": len(
                context.parsed_requirements[0].get("functional_requirements", [])
                if context.parsed_requirements else []
            ),
        }
        context.performance_plan = {
            "content": output.data.get("performance_plan", ""),
        }
        context.security_plan = {
            "content": output.data.get("security_plan", ""),
        }

        logger.info(
            "analysis_done",
            pipeline_id=pid,
            plan_length=len(test_plan_md),
            plan_file=test_plan_file,
        )

        return StageOutput(
            stage_name=self.stage_name,
            status="completed",
            data={
                "skill_prompt": skill_prompt,
                "test_plan_md": test_plan_md,
                "test_plan_file": test_plan_file,
                "requirements_count": len(
                    context.parsed_requirements[0].get("functional_requirements", [])
                    if context.parsed_requirements else []
                ),
            },
        )
