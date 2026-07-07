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
    return templates.TemplateResponse("pages/dashboard.html", {
        "request": request, "active_page": "dashboard"
    })


@web_router.get("/projects", response_class=HTMLResponse)
async def projects_page(request: Request):
    return templates.TemplateResponse("pages/projects.html", {
        "request": request, "active_page": "projects"
    })


@web_router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail_page(request: Request, project_id: str):
    return templates.TemplateResponse("pages/project_detail.html", {
        "request": request, "active_page": "projects", "project_id": project_id
    })


@web_router.get("/pipelines/{pipeline_id}", response_class=HTMLResponse)
async def pipeline_page(request: Request, pipeline_id: str):
    return templates.TemplateResponse("pages/pipeline.html", {
        "request": request, "active_page": "projects", "pipeline_id": pipeline_id
    })


@web_router.get("/projects/{project_id}/test-cases", response_class=HTMLResponse)
async def test_cases_page(request: Request, project_id: str):
    return templates.TemplateResponse("pages/test_cases.html", {
        "request": request, "active_page": "projects", "project_id": project_id
    })


@web_router.get("/executions/{execution_id}", response_class=HTMLResponse)
async def execution_page(request: Request, execution_id: str):
    return templates.TemplateResponse("pages/executions.html", {
        "request": request, "active_page": "projects", "execution_id": execution_id
    })


@web_router.get("/projects/{project_id}/defects", response_class=HTMLResponse)
async def defects_page(request: Request, project_id: str):
    return templates.TemplateResponse("pages/defects.html", {
        "request": request, "active_page": "projects", "project_id": project_id
    })


@web_router.get("/projects/{project_id}/tools", response_class=HTMLResponse)
async def stage_tools_page(request: Request, project_id: str):
    return templates.TemplateResponse("pages/stage_tools.html", {
        "request": request, "active_page": "projects", "project_id": project_id
    })


@web_router.get("/projects/{project_id}/reports", response_class=HTMLResponse)
async def reports_page(request: Request, project_id: str):
    return templates.TemplateResponse("pages/reports.html", {
        "request": request, "active_page": "projects", "project_id": project_id
    })


@web_router.get("/projects/{project_id}/settings", response_class=HTMLResponse)
async def settings_page(request: Request, project_id: str):
    return templates.TemplateResponse("pages/settings.html", {
        "request": request, "active_page": "projects", "project_id": project_id
    })

@web_router.get("/projects/{project_id}/schedules", response_class=HTMLResponse)
async def schedules_page(request: Request, project_id: str):
    return templates.TemplateResponse("pages/schedules.html", {
        "request": request, "active_page": "projects", "project_id": project_id
    })

@web_router.get("/projects/{project_id}/regression", response_class=HTMLResponse)
async def regression_page(request: Request, project_id: str):
    return templates.TemplateResponse("pages/regression.html", {
        "request": request, "active_page": "projects", "project_id": project_id
    })
@web_router.get("/requirements", response_class=HTMLResponse)
async def requirements_page(request: Request):
    return templates.TemplateResponse("pages/require_module.html", {
        "request": request, "active_page": "requirements"
    })

@web_router.get("/case-library", response_class=HTMLResponse)
async def case_library_page(request: Request):
    return templates.TemplateResponse("pages/case_library.html", {
        "request": request, "active_page": "case_library"
    })

@web_router.get("/review", response_class=HTMLResponse)
async def review_module_page(request: Request):
    return templates.TemplateResponse("pages/review_module.html", {
        "request": request, "active_page": "review"
    })
@web_router.get("/requirement-analysis", response_class=HTMLResponse)
async def requirement_analysis_page(request: Request):
    return templates.TemplateResponse("pages/requirement_analysis.html", {
        "request": request, "active_page": "requirement_analysis"
    })


@web_router.get("/skills", response_class=HTMLResponse)
async def skills_page(request: Request):
    return templates.TemplateResponse("pages/skills.html", {
        "request": request, "active_page": "skills"
    })

@web_router.get("/execution", response_class=HTMLResponse)
async def execution_module_page(request: Request):
    return templates.TemplateResponse("pages/execution_module.html", {
        "request": request, "active_page": "execution"
    })

