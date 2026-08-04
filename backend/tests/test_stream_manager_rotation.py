"""Audio sent while no STT stream is active must be buffered, not dropped."""

import asyncio

from backend.config import Settings
from backend.stt.stream_manager import StreamManager


class FakeStream:
    def __init__(self, active: bool):
        self._active = active
        self.received: list[bytes] = []

    @property
    def is_active(self) -> bool:
        return self._active

    async def send_audio(self, audio_bytes: bytes) -> None:
        self.received.append(audio_bytes)


def make_manager() -> StreamManager:
    return StreamManager(Settings(google_cloud_project="test-project"))


def test_audio_buffered_while_stream_inactive():
    mgr = make_manager()
    mgr._running = True
    mgr._current_stream = FakeStream(active=False)

    asyncio.run(mgr.send_audio(b"chunk-1"))
    asyncio.run(mgr.send_audio(b"chunk-2"))

    assert list(mgr._pending_audio) == [b"chunk-1", b"chunk-2"]
    assert mgr._current_stream.received == []


def test_pending_flushes_in_order_before_live_chunk():
    mgr = make_manager()
    mgr._running = True
    mgr._current_stream = FakeStream(active=False)
    asyncio.run(mgr.send_audio(b"during-rotation-1"))
    asyncio.run(mgr.send_audio(b"during-rotation-2"))

    fresh = FakeStream(active=True)
    mgr._current_stream = fresh
    asyncio.run(mgr.send_audio(b"live"))

    assert fresh.received == [b"during-rotation-1", b"during-rotation-2", b"live"]
    assert len(mgr._pending_audio) == 0


def test_pending_buffer_is_bounded():
    mgr = make_manager()
    mgr._running = True
    mgr._current_stream = None
    limit = mgr._pending_audio.maxlen
    for i in range(limit + 10):
        asyncio.run(mgr.send_audio(f"c{i}".encode()))
    assert len(mgr._pending_audio) == limit  # oldest dropped, newest kept
    assert mgr._pending_audio[-1] == f"c{limit + 9}".encode()
