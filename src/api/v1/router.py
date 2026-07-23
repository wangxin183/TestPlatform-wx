"""API v1 router — 主线：RA / TCG / EXE / Skills。"""

from fastapi import APIRouter

from src.api.v1.execution_runs import router as execution_runs_router
from src.api.v1.requirement_analysis import router as requirement_analysis_router
from src.api.v1.skills import router as skills_router
from src.api.v1.testcase_generation import router as testcase_generation_router

router = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health_check():
    return {"status": "ok", "version": "0.1.0"}


router.include_router(skills_router)
router.include_router(requirement_analysis_router)
router.include_router(testcase_generation_router)
router.include_router(execution_runs_router)
