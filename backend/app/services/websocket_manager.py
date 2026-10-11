import json
import logging
from typing import List, Dict, Any
from fastapi import WebSocket

logger = logging.getLogger("cloudlens.ws")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


class WebSocketManager:
    """
    Real-Time WebSocket Connection & Event Streaming Manager.
    Broadcasts live telemetry events, incident status updates, dynamic topology mutations,
    and remediation actions to all connected SRE dashboards with sub-millisecond latency.
    """

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket client connected. Active sessions: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket client disconnected. Remaining: {len(self.active_connections)}")

    async def broadcast(self, message_type: str, payload: Dict[str, Any]):
        """Broadcasts a structured JSON payload to all active WebSocket clients."""
        if not self.active_connections:
            return

        message = {
            "type": message_type,
            "data": payload
        }
        raw_json = json.dumps(message, default=str)

        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_text(raw_json)
            except Exception:
                dead_connections.append(connection)

        for dead in dead_connections:
            self.disconnect(dead)


# Global singleton instance
ws_manager = WebSocketManager()
