"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.v1.router import router as v1_router
from src.core.config import settings
from src.core.models.base import Base
from src.utils.logging_config import get_logger
from src.utils.middleware import RequestLoggingMiddleware
from src.utils.auth_middleware import WriteAuthMiddleware
from src.scheduler.service import start_scheduler, stop_scheduler, load_schedules
from src.core.database import engine
from src.ws.manager import ws_manager

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

    # CORS
    cors_origins = settings.server.cors_origins
    if not settings.app.debug:
        # In non-debug, '*' is not allowed for safety baseline.
        cors_origins = [o for o in cors_origins if o != "*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Write endpoints auth (API key)
    app.add_middleware(WriteAuthMiddleware)

    # API request logging
    app.add_middleware(RequestLoggingMiddleware)

    # Mount static files (CSS/JS)
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Register API router
    app.include_router(v1_router)

    # Register Web page router (must be after API to avoid route conflicts)
    from src.web.router import web_router
    app.include_router(web_router)

    # WebSocket endpoint for pipeline live updates
    @app.websocket("/ws/pipelines/{pipeline_id}/live")
    async def pipeline_live(websocket: WebSocket, pipeline_id: str):
        await ws_manager.connect(pipeline_id, websocket)
        try:
            while True:
                # Keep connection alive, listen for client pings
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text('{"type":"pong"}')
        except WebSocketDisconnect:
            await ws_manager.disconnect(pipeline_id, websocket)
        except Exception:
            await ws_manager.disconnect(pipeline_id, websocket)

    # WebSocket endpoint for execution live progress
    @app.websocket("/ws/executions/{execution_id}/live")
    async def execution_live(websocket: WebSocket, execution_id: str):
        await ws_manager.connect(execution_id, websocket)
        try:
            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text('{"type":"pong"}')
        except WebSocketDisconnect:
            await ws_manager.disconnect(execution_id, websocket)
        except Exception:
            await ws_manager.disconnect(execution_id, websocket)

    return app


app = create_app()
