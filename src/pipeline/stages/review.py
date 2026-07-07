"""Stage 5: Human review gate — runs AI pre-scoring via ReviewAgent, then blocks
pipeline until manually approved or rejected.
"""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.models import TestCase
from src.llm.agents.base import AgentContext
from src.llm.agents.review_agent import ReviewAgent
from src.pipeline.stages.base import AbstractStage, StageInput, StageOutput
from src.utils.logging_config import get_logger
from src.utils.stage_logger import get_stage_logger

logger = get_logger(__name__)


class ReviewStage(AbstractStage):
    """Review gate stage: AI pre-scores cases, then blocks for human review.

    Flow:
    1. Mark test cases as pending_review
    2. Delegate to ReviewAgent for AI pre-scoring (non-blocking)
    3. Enter review waiting state
    """

    stage_name = "review"

    @classmethod
    def required_context_fields(cls) -> list[str]:
        return ["generated_test_cases"]

    @classmethod
    def produced_context_fields(cls) -> list[str]:
        return ["approved_test_case_ids"]

    def __init__(self, db_session: AsyncSession):
        self._db = db_session

    async def execute(self, stage_input: StageInput) -> StageOutput:
        context = stage_input.context
        pid = stage_input.pipeline_id
        slog = get_stage_logger(pid, self.stage_name)
        slog.info(f"========== 人工评审阶段开始 ==========")
        slog.info(f"待评审用例: {len(context.generated_test_cases or [])}条")
        slog.info(f"评审反馈: {context.review_feedback or '无'}")

        # Mark test cases as pending_review (if not already)
        await self._db.execute(
            update(TestCase)
            .where(TestCase.pipeline_id == pid)
            .where(TestCase.status == "draft")
            .values(status="pending_review")
        )
        await self._db.commit()

        # Run AI pre-scoring via ReviewAgent (non-blocking)
        # Fetch test cases from DB to get proper IDs
        from sqlalchemy import select as sa_select
        result = await self._db.execute(
            sa_select(TestCase).where(TestCase.pipeline_id == pid)
        )
        db_cases = result.scalars().all()
        test_cases_for_review = [
            {
                "id": tc.id,
                "title": tc.title,
                "description": tc.description,
                "preconditions": tc.preconditions,
                "steps": tc.steps,
                "priority": tc.priority,
                "test_type": tc.test_type,
                "platform_type": tc.platform_type,
            }
            for tc in db_cases
        ]

        platform_type = context.project_config.get("platform_type", "")
        try:
            slog.info("启动 AI 预审评分...")
            agent = ReviewAgent()
            output = await agent.run(AgentContext(
                pipeline_id=pid,
                project_id=stage_input.project_id,
                platform_type=platform_type,
                extra={
                    "test_cases": test_cases_for_review,
                },
            ))
            scored = output.data.get("scored_count", 0)
            slog.info(f"AI 预审完成: 已评分 {scored} 条")
        except Exception as exc:
            slog.warning(f"AI 预审失败（非阻塞）: {exc}")
            logger.warning("review_ai_scoring_failed", pipeline_id=pid, error=str(exc))

        logger.info(
            "review_stage_entered",
            pipeline_id=pid,
            feedback=context.review_feedback,
        )

        slog.info("评审阶段已进入，等待人工评审...")

        return StageOutput(
            stage_name=self.stage_name,
            status="completed",
            data={
                "action": "review_requested",
                "feedback": context.review_feedback,
            },
        )
