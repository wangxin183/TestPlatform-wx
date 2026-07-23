"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.v1.router import router as v1_router
from src.core.config import settings
from src.core.database import engine
from src.core.models.base import Base
from src.scheduler.service import load_schedules, start_scheduler, stop_scheduler
from src.utils.auth_middleware import WriteAuthMiddleware
from src.utils.logging_config import get_logger
from src.utils.middleware import RequestLoggingMiddleware

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown events."""
    logger.info("app_starting", name=settings.app.name, version=settings.app.version)
    if settings.database.auto_create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        from src.utils.schema_patches import apply_schema_patches

        await apply_schema_patches(engine)
    start_scheduler()
    await load_schedules()
    yield
    stop_scheduler()
    logger.info("app_shutting_down")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app.name,
        version=settings.app.version,
        debug=settings.app.debug,
        lifespan=lifespan,
    )

    cors_origins = settings.server.cors_origins
    if not settings.app.debug:
        cors_origins = [o for o in cors_origins if o != "*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(WriteAuthMiddleware)
    app.add_middleware(RequestLoggingMiddleware)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(v1_router)

    from src.web.router import web_router

    app.include_router(web_router)

    return app


app = create_app()
