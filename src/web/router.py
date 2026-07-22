"""Web page router — serves HTML pages for the platform UI."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

web_router = APIRouter(prefix="")


@web_router.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse(request, "pages/dashboard.html", {
        "active_page": "dashboard"
    })


@web_router.get("/projects", response_class=HTMLResponse)
async def projects_page(request: Request):
    return templates.TemplateResponse(request, "pages/projects.html", {
        "active_page": "projects"
    })


@web_router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail_page(request: Request, project_id: str):
    return templates.TemplateResponse(request, "pages/project_detail.html", {
        "active_page": "projects", "project_id": project_id
    })


@web_router.get("/pipelines/{pipeline_id}", response_class=HTMLResponse)
async def pipeline_page(request: Request, pipeline_id: str):
    return templates.TemplateResponse(request, "pages/pipeline.html", {
        "active_page": "projects", "pipeline_id": pipeline_id
    })


@web_router.get("/projects/{project_id}/test-cases", response_class=HTMLResponse)
async def test_cases_page(request: Request, project_id: str):
    return templates.TemplateResponse(request, "pages/test_cases.html", {
        "active_page": "projects", "project_id": project_id
    })


@web_router.get("/executions/{execution_id}", response_class=HTMLResponse)
async def execution_page(request: Request, execution_id: str):
    return templates.TemplateResponse(request, "pages/executions.html", {
        "active_page": "projects", "execution_id": execution_id
    })


@web_router.get("/projects/{project_id}/defects", response_class=HTMLResponse)
async def defects_page(request: Request, project_id: str):
    return templates.TemplateResponse(request, "pages/defects.html", {
        "active_page": "projects", "project_id": project_id
    })


@web_router.get("/projects/{project_id}/tools", response_class=HTMLResponse)
async def stage_tools_page(request: Request, project_id: str):
    return templates.TemplateResponse(request, "pages/stage_tools.html", {
        "active_page": "projects", "project_id": project_id
    })


@web_router.get("/projects/{project_id}/reports", response_class=HTMLResponse)
async def reports_page(request: Request, project_id: str):
    return templates.TemplateResponse(request, "pages/reports.html", {
        "active_page": "projects", "project_id": project_id
    })


@web_router.get("/projects/{project_id}/settings", response_class=HTMLResponse)
async def settings_page(request: Request, project_id: str):
    return templates.TemplateResponse(request, "pages/settings.html", {
        "active_page": "projects", "project_id": project_id
    })


@web_router.get("/projects/{project_id}/schedules", response_class=HTMLResponse)
async def schedules_page(request: Request, project_id: str):
    return templates.TemplateResponse(request, "pages/schedules.html", {
        "active_page": "projects", "project_id": project_id
    })


@web_router.get("/projects/{project_id}/regression", response_class=HTMLResponse)
async def regression_page(request: Request, project_id: str):
    return templates.TemplateResponse(request, "pages/regression.html", {
        "active_page": "projects", "project_id": project_id
    })


@web_router.get("/requirements", response_class=HTMLResponse)
async def requirements_page(request: Request):
    return templates.TemplateResponse(request, "pages/require_module.html", {
        "active_page": "requirements"
    })


@web_router.get("/case-library", response_class=HTMLResponse)
async def case_library_page(request: Request):
    return templates.TemplateResponse(request, "pages/case_library.html", {
        "active_page": "case_library"
    })


@web_router.get("/review", response_class=HTMLResponse)
async def review_module_page(request: Request):
    return templates.TemplateResponse(request, "pages/review_module.html", {
        "active_page": "review"
    })


@web_router.get("/requirement-analysis", response_class=HTMLResponse)
async def requirement_analysis_page(request: Request):
    return templates.TemplateResponse(request, "pages/requirement_analysis.html", {
        "active_page": "requirement_analysis"
    })


@web_router.get("/testcase-generation", response_class=HTMLResponse)
async def testcase_generation_page(request: Request):
    return templates.TemplateResponse(request, "pages/testcase_generation.html", {
        "active_page": "testcase_generation"
    })


@web_router.get("/skills", response_class=HTMLResponse)
async def skills_page(request: Request):
    return templates.TemplateResponse(request, "pages/skills.html", {
        "active_page": "skills"
    })


@web_router.get("/app-execution", response_class=HTMLResponse)
async def app_execution_page(request: Request):
    return templates.TemplateResponse(request, "pages/app_execution.html", {
        "active_page": "app_execution"
    })


@web_router.get("/execution-runs/{run_id}/allure/{file_path:path}")
async def execution_run_allure(run_id: str, file_path: str = "index.html"):
    """提供 Allure 静态报告（只读 storage/execution_runs/{run_id}/allure-report/）。"""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    from src.utils.analysis_logger import EXE_STORAGE_BASE

    base = (EXE_STORAGE_BASE / run_id / "allure-report").resolve()
    target = (base / file_path).resolve()
    if not str(target).startswith(str(base)) or not target.exists():
        raise HTTPException(status_code=404, detail="报告不存在")
    return FileResponse(target)


@web_router.get("/execution", response_class=HTMLResponse)
async def execution_module_page(request: Request):
    return templates.TemplateResponse(request, "pages/execution_module.html", {
        "active_page": "execution"
    })
