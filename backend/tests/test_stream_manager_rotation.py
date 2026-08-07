"""Audio sent while no STT stream is active must be buffered, not dropped."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

from backend.config import Settings
from backend.stt.stream_manager import StreamManager, _absolute_result_times


class FakeStream:
    def __init__(self, active: bool, stream_id: str = "stream-1"):
        self._active = active
        self.stream_id = stream_id
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
    mgr = StreamManager(
        Settings(google_cloud_project="test-project", sample_rate=10, channels=1)
    )
    mgr._running = True
    mgr._current_stream = FakeStream(active=False, stream_id="stream-1")
    asyncio.run(mgr.send_audio(b"0" * 10))
    asyncio.run(mgr.send_audio(b"1" * 10))

    fresh = FakeStream(active=True, stream_id="stream-2")
    mgr._current_stream = fresh
    asyncio.run(mgr.send_audio(b"2" * 10))

    assert fresh.received == [b"0" * 10, b"1" * 10, b"2" * 10]
    assert len(mgr._pending_audio) == 0
    assert mgr._stream_audio_origins["stream-2"] == 0.0
    assert mgr._audio_timeline_seconds == 1.5


def test_pending_buffer_is_bounded():
    mgr = make_manager()
    mgr._running = True
    mgr._current_stream = None
    limit = mgr._pending_audio.maxlen
    for i in range(limit + 10):
        asyncio.run(mgr.send_audio(f"c{i}".encode()))
    assert len(mgr._pending_audio) == limit  # oldest dropped, newest kept
    assert mgr._pending_audio[-1] == f"c{limit + 9}".encode()


def test_provider_word_offsets_map_to_stream_audio_origin():
    result = SimpleNamespace(result_end_offset=timedelta(seconds=2.5))
    alternative = SimpleNamespace(
        words=[
            SimpleNamespace(
                start_offset=timedelta(seconds=0.25),
                end_offset=timedelta(seconds=0.75),
            ),
            SimpleNamespace(
                start_offset=timedelta(seconds=2.0),
                end_offset=timedelta(seconds=2.25),
            ),
        ]
    )

    start, end = _absolute_result_times(
        stream_audio_origin=270.0,
        result=result,
        alternative=alternative,
        fallback_offset=999.0,
        text="fala sintética",
    )

    assert start == 270.25
    assert end == 272.25


def test_result_time_falls_back_to_callback_when_provider_offsets_are_absent():
    start, end = _absolute_result_times(
        stream_audio_origin=None,
        result=SimpleNamespace(result_end_offset=None),
        alternative=SimpleNamespace(words=[]),
        fallback_offset=12.0,
        text="1234567890",
    )

    assert start == 11.5
    assert end == 12.0
