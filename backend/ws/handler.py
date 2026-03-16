"""WebSocket endpoint with reconnection support."""

from __future__ import annotations

import asyncio
from collections import deque

import structlog
from fastapi import WebSocket, WebSocketDisconnect

from backend.schemas.models import (
    ConnectionHealth,
    ConnectionStatusPayload,
    SessionState,
    WSMessage,
    WSMessageType,
)

logger = structlog.get_logger()

# Ring buffer size for reconnection replay
REPLAY_BUFFER_SIZE = 1000


class WSConnectionManager:
    """Manages WebSocket connections and message broadcasting."""

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}  # session_id -> connections
        self._message_buffer: dict[str, deque[WSMessage]] = {}  # session_id -> ring buffer
        self._sequence_counters: dict[str, int] = {}  # session_id -> current seq

    def _ensure_session(self, session_id: str) -> None:
        if session_id not in self._connections:
            self._connections[session_id] = []
            self._message_buffer[session_id] = deque(maxlen=REPLAY_BUFFER_SIZE)
            self._sequence_counters[session_id] = 0

    def next_sequence(self, session_id: str) -> int:
        self._ensure_session(session_id)
        self._sequence_counters[session_id] += 1
        return self._sequence_counters[session_id]

    async def connect(
        self, websocket: WebSocket, session_id: str, last_seq: int = 0
    ) -> None:
        """Accept a WebSocket connection and replay missed messages."""
        await websocket.accept()
        self._ensure_session(session_id)
        self._connections[session_id].append(websocket)

        logger.info(
            "ws_client_connected",
            session_id=session_id,
            last_seq=last_seq,
            active_connections=len(self._connections[session_id]),
        )

        # Replay missed messages
        if last_seq > 0:
            await self._replay_messages(websocket, session_id, last_seq)

    async def _replay_messages(
        self, websocket: WebSocket, session_id: str, last_seq: int
    ) -> None:
        """Replay messages missed during disconnection."""
        buffer = self._message_buffer.get(session_id, deque())
        missed = [msg for msg in buffer if msg.sequence_number > last_seq]

        if not missed:
            return

        current_seq = self._sequence_counters.get(session_id, 0)
        gap = current_seq - last_seq

        if gap > REPLAY_BUFFER_SIZE:
            # Gap too large — client needs full state snapshot
            logger.info(
                "ws_gap_too_large",
                session_id=session_id,
                gap=gap,
                buffer_size=REPLAY_BUFFER_SIZE,
            )
            # The caller should send a session_state message instead
            return

        logger.info(
            "ws_replaying_messages",
            session_id=session_id,
            count=len(missed),
            from_seq=last_seq + 1,
        )

        for msg in missed:
            try:
                await websocket.send_json(msg.model_dump())
            except Exception:
                logger.exception("ws_replay_error")
                break

    def disconnect(self, websocket: WebSocket, session_id: str) -> None:
        """Remove a WebSocket connection."""
        conns = self._connections.get(session_id, [])
        if websocket in conns:
            conns.remove(websocket)
        logger.info(
            "ws_client_disconnected",
            session_id=session_id,
            remaining=len(conns),
        )

    async def broadcast(self, session_id: str, message: WSMessage) -> None:
        """Send a message to all connected clients for a session."""
        self._ensure_session(session_id)

        # Store in ring buffer for reconnection replay
        self._message_buffer[session_id].append(message)

        data = message.model_dump()
        conns = self._connections.get(session_id, [])
        dead = []

        for ws in conns:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws, session_id)

    def cleanup_session(self, session_id: str) -> None:
        """Remove all state for a session."""
        self._connections.pop(session_id, None)
        self._message_buffer.pop(session_id, None)
        self._sequence_counters.pop(session_id, None)


# Global singleton
ws_manager = WSConnectionManager()
