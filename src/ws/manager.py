"""WebSocket connection manager for pipeline live updates."""

import json
from collections import defaultdict

from fastapi import WebSocket
from structlog import get_logger

logger = get_logger(__name__)


class ConnectionManager:
    """Track active WebSocket connections grouped by pipeline_id."""

    def __init__(self):
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, pipeline_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections[pipeline_id].add(ws)
        logger.info("ws_connected", pipeline_id=pipeline_id)

    async def disconnect(self, pipeline_id: str, ws: WebSocket) -> None:
        self._connections[pipeline_id].discard(ws)
        if not self._connections[pipeline_id]:
            del self._connections[pipeline_id]
        logger.info("ws_disconnected", pipeline_id=pipeline_id)

    async def broadcast(self, pipeline_id: str, message: dict) -> None:
        """Send a message to all connections watching a pipeline."""
        connections = self._connections.get(pipeline_id, set())
        dead: set[WebSocket] = set()
        payload = json.dumps(message, default=str)
        for ws in connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        for ws in dead:
            connections.discard(ws)
        if dead:
            logger.info("ws_stale_removed", pipeline_id=pipeline_id, count=len(dead))

    async def broadcast_stage_change(
        self, pipeline_id: str, current_stage: str, pipeline_status: str
    ) -> None:
        await self.broadcast(
            pipeline_id,
            {
                "type": "stage_change",
                "current_stage": current_stage,
                "pipeline_status": pipeline_status,
            },
        )

    async def broadcast_log(self, pipeline_id: str, message: str) -> None:
        await self.broadcast(
            pipeline_id,
            {"type": "log", "message": message},
        )

    # ── Execution-level broadcasting ──

    async def broadcast_execution_progress(
        self,
        execution_id: str,
        data: dict,
    ) -> None:
        """Send execution progress update (case-by-case)."""
        await self.broadcast(
            execution_id,
            {"type": "execution_progress", **data},
        )

    async def broadcast_execution_complete(
        self,
        execution_id: str,
        summary: dict,
    ) -> None:
        """Send execution complete notification with summary."""
        await self.broadcast(
            execution_id,
            {"type": "execution_complete", "summary": summary},
        )


# Singleton
ws_manager = ConnectionManager()
