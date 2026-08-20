"""WebSocket delivery is bounded so slow clients cannot stall capture."""

import asyncio

from backend.schemas.models import ErrorPayload, ErrorSeverity, WSMessage
from backend.ws import handler


class FastSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


class SlowSocket:
    async def send_json(self, _payload):
        await asyncio.Event().wait()


def test_broadcast_sends_fast_peers_without_waiting_for_slow_peer(monkeypatch):
    manager = handler.WSConnectionManager()
    fast = FastSocket()
    slow = SlowSocket()
    session_id = "session-1"
    manager._ensure_session(session_id)
    manager._connections[session_id] = [slow, fast]
    monkeypatch.setattr(handler, "WS_SEND_TIMEOUT_SECONDS", 0.01)

    message = WSMessage.error_msg(
        session_id,
        1,
        ErrorPayload(severity=ErrorSeverity.WARNING, message="warning"),
    )
    asyncio.run(manager.broadcast(session_id, message))

    assert len(fast.messages) == 1
    assert manager._connections[session_id] == [fast]
