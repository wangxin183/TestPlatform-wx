"""Web page router — RA / TCG / EXE / Skills 主线页面。"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

web_router = APIRouter(prefix="")


@web_router.get("/", response_class=HTMLResponse)
async def root_redirect():
    return RedirectResponse(url="/requirement-analysis", status_code=302)


@web_router.get("/requirement-analysis", response_class=HTMLResponse)
async def requirement_analysis_page(request: Request):
    return templates.TemplateResponse(
        request,
        "pages/requirement_analysis.html",
        {"active_page": "requirement_analysis"},
    )


@web_router.get("/testcase-generation", response_class=HTMLResponse)
async def testcase_generation_page(request: Request):
    return templates.TemplateResponse(
        request,
        "pages/testcase_generation.html",
        {"active_page": "testcase_generation"},
    )


@web_router.get("/app-execution", response_class=HTMLResponse)
async def app_execution_page(request: Request):
    return templates.TemplateResponse(
        request,
        "pages/app_execution.html",
        {"active_page": "app_execution"},
    )


@web_router.get("/skills", response_class=HTMLResponse)
async def skills_page(request: Request):
    return templates.TemplateResponse(
        request,
        "pages/skills.html",
        {"active_page": "skills"},
    )


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
