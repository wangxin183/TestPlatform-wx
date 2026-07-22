"""API v1 router — aggregates all v1 sub-routers."""

from fastapi import APIRouter

from src.api.v1.projects import router as projects_router
from src.api.v1.documents import router as documents_router
from src.api.v1.pipelines import router as pipelines_router
from src.api.v1.test_cases import router as test_cases_router
from src.api.v1.executions import router as executions_router
from src.api.v1.defects import router as defects_router
from src.api.v1.files import router as files_router
from src.api.v1.reports import router as reports_router
from src.api.v1.schedules import router as schedules_router
from src.api.v1.environments import router as environments_router
from src.api.v1.notifications import router as notifications_router
from src.api.v1.review_submissions import router as review_submissions_router
from src.api.v1.requirements import router as requirements_router
from src.api.v1.case_library import router as case_library_router
from src.api.v1.skills import router as skills_router
from src.api.v1.requirement_analysis import router as requirement_analysis_router
from src.api.v1.testcase_generation import router as testcase_generation_router
from src.api.v1.execution_runs import router as execution_runs_router

router = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health_check():
    return {"status": "ok", "version": "0.1.0"}


# Include all sub-routers
router.include_router(projects_router)
router.include_router(documents_router)
router.include_router(pipelines_router)
router.include_router(test_cases_router)
router.include_router(executions_router)
router.include_router(defects_router)
router.include_router(files_router)
router.include_router(reports_router)
router.include_router(schedules_router)
router.include_router(environments_router)
router.include_router(notifications_router)
router.include_router(review_submissions_router)
router.include_router(requirements_router)
router.include_router(case_library_router)
router.include_router(skills_router)
router.include_router(requirement_analysis_router)
router.include_router(testcase_generation_router)
router.include_router(execution_runs_router)