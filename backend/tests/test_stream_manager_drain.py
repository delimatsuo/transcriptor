"""Stopping STT must drain final responses or mark the transcript incomplete."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from backend import main as backend_main
from backend.config import Settings
from backend.schemas.models import Session, SessionStatus, TranscriptSegment
from backend.sessions.manager import SessionManager
from backend.stt.google_stt import GoogleSTTStream
from backend.stt import stream_manager


class FinalOnCloseStream:
    """Fake provider stream that emits one final only after input is closed."""

    instances: list["FinalOnCloseStream"] = []

    def __init__(self, settings, stream_id):
        self.stream_id = stream_id
        self._accepting_audio = False
        self.request_opened = False
        self._input_closed = asyncio.Event()
        self.stop_calls = 0
        self.__class__.instances.append(self)

    @property
    def is_active(self):
        return self._accepting_audio

    @property
    def elapsed_seconds(self):
        return 0.0

    async def start(self):
        self._accepting_audio = True
        self.request_opened = True
        await self._input_closed.wait()
        alternative = SimpleNamespace(
            transcript="marcador final",
            confidence=0.99,
        )
        result = SimpleNamespace(alternatives=[alternative], is_final=True)
        yield SimpleNamespace(results=[result])

    async def send_audio(self, audio_bytes):
        return None

    async def stop(self):
        self.stop_calls += 1
        self._accepting_audio = False
        self._input_closed.set()


class NeverFinishesStream(FinalOnCloseStream):
    """Fake provider stream that ignores input close until cancelled."""

    async def start(self):
        self._accepting_audio = True
        try:
            await asyncio.Event().wait()
            if False:  # pragma: no cover - makes this an async generator
                yield None
        finally:
            self.cancelled = True


async def _wait_until_active(manager: stream_manager.StreamManager) -> None:
    for _ in range(100):
        current = manager._current_stream
        if current is not None and current.is_active:
            return
        await asyncio.sleep(0)
    raise AssertionError("fake STT stream did not become active")


def test_google_stream_half_close_keeps_reading_provider_responses():
    final_response = SimpleNamespace(results=["provider-final"])

    class FakeSpeechClient:
        def __init__(self):
            self.requests = []

        async def streaming_recognize(self, *, requests):
            async def responses():
                async for request in requests:
                    self.requests.append(request)
                yield final_response

            return responses()

    async def run():
        stream = GoogleSTTStream(
            Settings(google_cloud_project="test-project"),
            stream_id="stream-1",
        )
        client = FakeSpeechClient()
        stream._client = client
        received = []

        async def consume():
            async for response in stream.start():
                received.append(response)

        task = asyncio.create_task(consume())
        for _ in range(100):
            if stream.is_active:
                break
            await asyncio.sleep(0)
        else:
            raise AssertionError("Google stream did not become active")

        await stream.send_audio(b"last-audio")
        await stream.stop()
        await task
        return stream, client, received

    stream, client, received = asyncio.run(run())

    assert stream.is_active is False
    assert len(client.requests) == 2  # config request + final audio request
    assert client.requests[1].audio == b"last-audio"
    assert received == [final_response]


def test_stop_half_closes_input_and_awaits_one_final_callback(monkeypatch):
    FinalOnCloseStream.instances.clear()
    monkeypatch.setattr(stream_manager, "GoogleSTTStream", FinalOnCloseStream)
    received = []

    async def on_transcript(segment):
        await asyncio.sleep(0)
        received.append(segment)

    async def run():
        manager = stream_manager.StreamManager(
            Settings(
                google_cloud_project="test-project",
                stt_graceful_drain_timeout_seconds=0.5,
            ),
            on_transcript=on_transcript,
            source_label="Entrevistador",
        )
        await manager.start()
        await _wait_until_active(manager)
        drained = await manager.stop()
        return manager, drained

    manager, drained = asyncio.run(run())

    assert drained is True
    assert manager.drain_completed is True
    assert FinalOnCloseStream.instances[0].stop_calls == 1
    assert len(received) == 1
    assert received[0].text == "marcador final"
    assert received[0].is_final is True


def test_stop_timeout_is_bounded_and_cancels_response_task(monkeypatch):
    NeverFinishesStream.instances.clear()
    monkeypatch.setattr(stream_manager, "GoogleSTTStream", NeverFinishesStream)

    async def run():
        manager = stream_manager.StreamManager(
            Settings(
                google_cloud_project="test-project",
                stt_graceful_drain_timeout_seconds=0.02,
            )
        )
        await manager.start()
        await _wait_until_active(manager)
        started_at = time.monotonic()
        drained = await manager.stop()
        return manager, drained, time.monotonic() - started_at

    manager, drained, elapsed = asyncio.run(run())

    assert drained is False
    assert manager.drain_completed is False
    assert manager.drain_failure_reason == "timeout"
    assert elapsed < 0.5
    assert manager._response_task is not None
    assert manager._response_task.done()
    assert NeverFinishesStream.instances[0].cancelled is True


def test_final_callback_failure_marks_drain_incomplete(monkeypatch):
    FinalOnCloseStream.instances.clear()
    monkeypatch.setattr(stream_manager, "GoogleSTTStream", FinalOnCloseStream)

    async def failing_callback(_segment):
        raise RuntimeError("durable write failed")

    async def run():
        manager = stream_manager.StreamManager(
            Settings(
                google_cloud_project="test-project",
                stt_graceful_drain_timeout_seconds=0.5,
            ),
            on_transcript=failing_callback,
        )
        await manager.start()
        await _wait_until_active(manager)
        drained = await manager.stop()
        return manager, drained

    manager, drained = asyncio.run(run())

    assert drained is False
    assert manager.drain_failure_reason == "transcript_callback_error"


def test_transcript_callback_failure_stops_reconnect_loop(monkeypatch):
    class CallbackFailureStream(FinalOnCloseStream):
        instances: list["CallbackFailureStream"] = []

        async def start(self):
            self._accepting_audio = True
            self.request_opened = True
            alternative = SimpleNamespace(
                transcript="callback failure",
                confidence=0.99,
            )
            result = SimpleNamespace(alternatives=[alternative], is_final=True)
            yield SimpleNamespace(results=[result])
            await asyncio.Event().wait()

    CallbackFailureStream.instances.clear()
    monkeypatch.setattr(stream_manager, "GoogleSTTStream", CallbackFailureStream)

    async def failing_callback(_segment):
        raise RuntimeError("durable write failed")

    async def run():
        manager = stream_manager.StreamManager(
            Settings(google_cloud_project="test-project"),
            on_transcript=failing_callback,
        )
        await manager.start()
        for _ in range(100):
            if manager.drain_failure_reason is not None:
                break
            await asyncio.sleep(0)
        await asyncio.sleep(0.6)
        stream_count = len(CallbackFailureStream.instances)
        await manager.stop()
        return manager, stream_count

    manager, stream_count = asyncio.run(run())

    assert manager.drain_failure_reason == "transcript_callback_error"
    assert manager._running is False
    assert stream_count == 1


def test_provider_response_failure_stops_reconnect_loop(monkeypatch):
    class ResponseFailureStream(FinalOnCloseStream):
        instances: list["ResponseFailureStream"] = []

        async def start(self):
            self._accepting_audio = True
            self.request_opened = True
            raise RuntimeError("provider rejected request")
            if False:  # pragma: no cover - makes this an async generator
                yield None

    ResponseFailureStream.instances.clear()
    monkeypatch.setattr(stream_manager, "GoogleSTTStream", ResponseFailureStream)

    async def run():
        manager = stream_manager.StreamManager(
            Settings(google_cloud_project="test-project")
        )
        await manager.start()
        for _ in range(100):
            if manager.drain_failure_reason is not None:
                break
            await asyncio.sleep(0)
        await asyncio.sleep(0.6)
        stream_count = len(ResponseFailureStream.instances)
        await manager.stop()
        return manager, stream_count

    manager, stream_count = asyncio.run(run())

    assert manager.drain_failure_reason == "response_error"
    assert manager._running is False
    assert stream_count == 1


def test_final_firestore_failure_propagates_to_completion_contract(monkeypatch):
    segment = TranscriptSegment(text="final", is_final=True)

    class FakeSessionManager:
        def add_transcript_segment(self, _session_id, _segment):
            pass

        def get_transcript_word_count(self, _session_id, *, from_seq):
            assert from_seq == 0
            return 0

        def get_session(self, _session_id):
            return None

    class FakeContextWindow:
        last_summary_seq = 0

        def should_summarize(self, _word_count):
            return False

    class FailingFirestoreStorage:
        save_transcript_segment = AsyncMock(
            side_effect=RuntimeError("firestore unavailable")
        )

    monkeypatch.setattr(backend_main, "session_mgr", FakeSessionManager())
    monkeypatch.setattr(backend_main, "context_window", FakeContextWindow())
    monkeypatch.setattr(
        backend_main,
        "firestore_storage",
        FailingFirestoreStorage(),
    )
    monkeypatch.setattr(
        backend_main.ws_manager,
        "broadcast",
        AsyncMock(),
    )

    with pytest.raises(RuntimeError, match="firestore unavailable"):
        asyncio.run(backend_main._on_transcript("session-1", segment))


def test_final_child_failure_marks_parent_durability_pending(monkeypatch):
    manager = SessionManager(Settings(google_cloud_project="test-project"))
    session = manager.create_session()
    segment = TranscriptSegment(text="not durable yet", is_final=True)

    class FakeContextWindow:
        last_summary_seq = 0

        def should_summarize(self, _word_count):
            return False

    class FailingFirestoreStorage:
        save_transcript_segment = AsyncMock(
            side_effect=RuntimeError("firestore unavailable")
        )
        save_session = AsyncMock()

    storage = FailingFirestoreStorage()
    monkeypatch.setattr(backend_main, "session_mgr", manager)
    monkeypatch.setattr(backend_main, "context_window", FakeContextWindow())
    monkeypatch.setattr(backend_main, "firestore_storage", storage)
    monkeypatch.setattr(backend_main.ws_manager, "broadcast", AsyncMock())
    backend_main.transcript_persistence_failures.clear()

    try:
        with pytest.raises(RuntimeError, match="firestore unavailable"):
            asyncio.run(backend_main._on_transcript(session.id, segment))
        assert session.transcript_durability == "pending"
        assert session.transcript_failure_count == 1
        storage.save_session.assert_awaited_once_with(session)
    finally:
        backend_main.transcript_persistence_failures.discard(session.id)


def test_stop_marks_unsent_rotation_audio_incomplete():
    async def run():
        manager = stream_manager.StreamManager(
            Settings(google_cloud_project="test-project")
        )
        manager._running = True
        manager._start_requested = True
        manager._ever_stream_opened = True
        manager._pending_audio.append(b"not-yet-forwarded")
        drained = await manager.stop()
        return manager, drained

    manager, drained = asyncio.run(run())

    assert drained is False
    assert manager.drain_failure_reason == "pending_audio_not_sent"


def test_stop_without_start_is_incomplete():
    async def run():
        manager = stream_manager.StreamManager(
            Settings(google_cloud_project="test-project")
        )
        drained = await manager.stop()
        return manager, drained

    manager, drained = asyncio.run(run())

    assert drained is False
    assert manager.drain_failure_reason == "not_started"


def test_rotation_overflow_stays_incomplete_after_pending_audio_flushes():
    class ActiveStream:
        request_opened = True
        is_active = True

        def __init__(self):
            self.received = []

        async def send_audio(self, audio_bytes):
            self.received.append(audio_bytes)

        async def stop(self):
            self.is_active = False

    async def run():
        manager = stream_manager.StreamManager(
            Settings(
                google_cloud_project="test-project",
                audio_buffer_max_seconds=2,
                audio_chunk_duration_ms=1000,
            )
        )
        manager._running = True
        manager._start_requested = True
        await manager.send_audio(b"dropped")
        await manager.send_audio(b"kept-1")
        await manager.send_audio(b"kept-2")

        active = ActiveStream()
        manager._current_stream = active
        await manager.send_audio(b"live")
        drained = await manager.stop()
        return manager, active, drained

    manager, active, drained = asyncio.run(run())

    assert active.received == [b"kept-1", b"kept-2", b"live"]
    assert drained is False
    assert manager.drain_failure_reason == "pending_audio_dropped"


def test_session_manager_marks_failed_drain_incomplete():
    manager = SessionManager(Settings(google_cloud_project="test-project"))
    session = manager.create_session()

    stopped = asyncio.run(
        manager.stop_session(session.id, transcription_complete=False)
    )

    assert stopped is not None
    assert stopped.status == SessionStatus.INCOMPLETE


def test_session_manager_preserves_terminal_status_on_repeated_stop():
    manager = SessionManager(Settings(google_cloud_project="test-project"))
    session = manager.create_session()

    first = asyncio.run(
        manager.stop_session(session.id, transcription_complete=True)
    )
    repeated = asyncio.run(
        manager.stop_session(session.id, transcription_complete=False)
    )

    assert first is not None
    assert repeated is not None
    assert first.status == SessionStatus.COMPLETED
    assert repeated.status == SessionStatus.COMPLETED


def test_stop_endpoint_surfaces_incomplete_drain_and_skips_report(monkeypatch):
    session = Session(id="session-1")

    class FakeSessionManager:
        def get_session(self, session_id):
            assert session_id == session.id
            return session

        async def stop_session(self, session_id, *, transcription_complete):
            assert session_id == session.id
            assert transcription_complete is False
            session.status = SessionStatus.INCOMPLETE
            return session

    class FakeFirestoreStorage:
        save_session = AsyncMock()

    async def incomplete_pipeline(_session_id):
        return False

    broadcast = AsyncMock()
    generate_report = AsyncMock()
    monkeypatch.setattr(backend_main, "session_mgr", FakeSessionManager())
    monkeypatch.setattr(backend_main, "firestore_storage", FakeFirestoreStorage())
    monkeypatch.setattr(backend_main, "_stop_pipeline", incomplete_pipeline)
    monkeypatch.setattr(backend_main, "_generate_final_summary", generate_report)
    monkeypatch.setattr(backend_main.ws_manager, "broadcast", broadcast)
    monkeypatch.setattr(
        backend_main,
        "interview_documents",
        {session.id: {"resume": "sensitive CV"}},
    )
    monkeypatch.setattr(
        backend_main,
        "speaker_correlators",
        {session.id: object()},
    )
    monkeypatch.setattr(
        backend_main,
        "extension_tokens",
        {session.id: "secret token"},
    )
    monkeypatch.setattr(
        backend_main,
        "_clock_sync_timestamps",
        {session.id: 123.0},
    )

    result = asyncio.run(backend_main.stop_session(session.id))

    assert result == {
        "session_id": session.id,
        "status": "incomplete",
        "transcription_complete": False,
    }
    backend_main.firestore_storage.save_session.assert_awaited_once_with(session)
    generate_report.assert_not_awaited()
    assert session.id not in backend_main.interview_documents
    assert session.id not in backend_main.speaker_correlators
    assert session.id not in backend_main.extension_tokens
    assert session.id not in backend_main._clock_sync_timestamps
    broadcast.assert_awaited_once()
    message = broadcast.await_args.args[1]
    assert message.payload["severity"] == "fatal"
    assert message.payload["code"] == "stt_graceful_drain_incomplete"
    assert "Transcrição incompleta" in message.payload["message"]


def test_repeated_stop_preserves_completed_session(monkeypatch):
    session = Session(id="session-completed", status=SessionStatus.COMPLETED)

    class FakeSessionManager:
        stop_session = AsyncMock()

        def get_session(self, session_id):
            assert session_id == session.id
            return session

    class FakeFirestoreStorage:
        save_session = AsyncMock()

    stop_pipeline = AsyncMock(return_value=False)
    generate_report = AsyncMock()
    monkeypatch.setattr(backend_main, "session_mgr", FakeSessionManager())
    monkeypatch.setattr(backend_main, "firestore_storage", FakeFirestoreStorage())
    monkeypatch.setattr(backend_main, "_stop_pipeline", stop_pipeline)
    monkeypatch.setattr(backend_main, "_generate_final_summary", generate_report)
    monkeypatch.setattr(backend_main, "final_summary_scheduled", set())

    async def run():
        result = await backend_main.stop_session(session.id)
        await asyncio.sleep(0)
        return result

    result = asyncio.run(run())

    assert result == {
        "session_id": session.id,
        "status": "completed",
        "transcription_complete": True,
    }
    stop_pipeline.assert_not_awaited()
    backend_main.session_mgr.stop_session.assert_not_awaited()
    backend_main.firestore_storage.save_session.assert_awaited_once_with(session)
    generate_report.assert_awaited_once_with(session.id)


def test_failed_terminal_save_is_replayed_before_report_on_retry(monkeypatch):
    manager = SessionManager(Settings(google_cloud_project="test-project"))
    session = manager.create_session()

    class FlakyFirestoreStorage:
        save_session = AsyncMock(
            side_effect=[RuntimeError("write failed"), None]
        )

    stop_pipeline = AsyncMock(return_value=True)
    generate_report = AsyncMock()
    monkeypatch.setattr(backend_main, "session_mgr", manager)
    monkeypatch.setattr(backend_main, "firestore_storage", FlakyFirestoreStorage())
    monkeypatch.setattr(backend_main, "_stop_pipeline", stop_pipeline)
    monkeypatch.setattr(backend_main, "_generate_final_summary", generate_report)
    monkeypatch.setattr(backend_main, "session_stop_locks", {})
    monkeypatch.setattr(backend_main, "final_summary_scheduled", set())

    async def run():
        with pytest.raises(RuntimeError, match="write failed"):
            await backend_main.stop_session(session.id)
        assert session.status == SessionStatus.COMPLETED

        result = await backend_main.stop_session(session.id)
        await asyncio.sleep(0)
        return result

    result = asyncio.run(run())

    assert result["status"] == "completed"
    assert result["transcription_complete"] is True
    assert backend_main.firestore_storage.save_session.await_count == 2
    stop_pipeline.assert_awaited_once_with(session.id)
    generate_report.assert_awaited_once_with(session.id)


def test_stop_retries_failed_final_children_before_terminal_parent_save(monkeypatch):
    manager = SessionManager(Settings(google_cloud_project="test-project"))
    session = manager.create_session()
    segment = TranscriptSegment(text="durable retry", is_final=True)
    manager.add_transcript_segment(session.id, segment)

    class RetryStorage:
        save_transcript_batch = AsyncMock()
        save_session = AsyncMock()

    storage = RetryStorage()
    monkeypatch.setattr(backend_main, "session_mgr", manager)
    monkeypatch.setattr(backend_main, "firestore_storage", storage)
    monkeypatch.setattr(backend_main, "_stop_pipeline", AsyncMock(return_value=False))
    monkeypatch.setattr(backend_main, "session_stop_locks", {})
    monkeypatch.setattr(backend_main, "transcript_persistence_failures", {session.id})
    monkeypatch.setattr(backend_main.ws_manager, "broadcast", AsyncMock())

    result = asyncio.run(backend_main.stop_session(session.id))

    assert result["status"] == "incomplete"
    storage.save_transcript_batch.assert_awaited_once()
    assert storage.save_transcript_batch.await_args.args[0] == session.id
    assert storage.save_transcript_batch.await_args.args[1] == [segment]
    storage.save_session.assert_awaited_once_with(session)
    assert session.id not in backend_main.transcript_persistence_failures
    assert manager.get_transcript(session.id) == []


def test_concurrent_stops_share_one_drain_and_consistent_terminal(monkeypatch):
    manager = SessionManager(Settings(google_cloud_project="test-project"))
    session = manager.create_session()
    drain_entered = asyncio.Event()
    release_drain = asyncio.Event()

    async def controlled_drain(_session_id):
        drain_entered.set()
        await release_drain.wait()
        return True

    class FakeFirestoreStorage:
        save_session = AsyncMock()

    generate_report = AsyncMock()
    monkeypatch.setattr(backend_main, "session_mgr", manager)
    monkeypatch.setattr(backend_main, "firestore_storage", FakeFirestoreStorage())
    monkeypatch.setattr(backend_main, "_stop_pipeline", AsyncMock(side_effect=controlled_drain))
    monkeypatch.setattr(backend_main, "_generate_final_summary", generate_report)
    monkeypatch.setattr(backend_main, "session_stop_locks", {})
    monkeypatch.setattr(backend_main, "final_summary_scheduled", set())

    async def run():
        first = asyncio.create_task(backend_main.stop_session(session.id))
        await drain_entered.wait()
        second = asyncio.create_task(backend_main.stop_session(session.id))
        await asyncio.sleep(0)
        release_drain.set()
        results = await asyncio.gather(first, second)
        await asyncio.sleep(0)
        return results

    results = asyncio.run(run())

    assert results == [
        {
            "session_id": session.id,
            "status": "completed",
            "transcription_complete": True,
        },
        {
            "session_id": session.id,
            "status": "completed",
            "transcription_complete": True,
        },
    ]
    assert backend_main._stop_pipeline.await_count == 1
    assert backend_main.firestore_storage.save_session.await_count == 2
    generate_report.assert_awaited_once_with(session.id)


def test_audio_pipeline_forwards_capture_tail_before_half_close(
    monkeypatch,
    tmp_path,
):
    events = []
    stream_started = asyncio.Event()

    class FakeCapture:
        def __init__(self, _settings, output_queue, **_kwargs):
            self.output_queue = output_queue

        async def start(self):
            events.append("capture_started")

        async def stop(self):
            events.append("capture_stopped")
            asyncio.get_running_loop().call_soon(
                self.output_queue.put_nowait,
                np.full(8, 0.25, dtype=np.float32),
            )

    class FakeStreamManager:
        def __init__(self, **_kwargs):
            self.drain_completed = None

        async def start(self):
            events.append("stream_started")
            stream_started.set()

        async def send_audio(self, audio_bytes):
            events.append(("audio_sent", audio_bytes))

        async def stop(self):
            events.append("stream_half_closed")
            self.drain_completed = True
            return True

    class FakeSessionManager:
        def get_session(self, _session_id):
            return SimpleNamespace(status=SessionStatus.ACTIVE)

    async def run():
        monkeypatch.setattr(backend_main, "AudioCapture", FakeCapture)
        monkeypatch.setattr(backend_main, "StreamManager", FakeStreamManager)
        monkeypatch.setattr(
            backend_main,
            "settings",
            Settings(
                google_cloud_project="test-project",
                audio_backup_dir=str(tmp_path / "recordings"),
            ),
        )
        monkeypatch.setattr(
            backend_main,
            "session_mgr",
            FakeSessionManager(),
        )

        task = asyncio.create_task(
            backend_main._run_single_audio_stream(
                "session-1", "device", "Entrevistador", [], [], [],
            )
        )
        await stream_started.wait()
        await asyncio.sleep(0)
        task.cancel()
        await task

    asyncio.run(run())

    assert events[0:2] == ["capture_started", "stream_started"]
    assert events[2] == "capture_stopped"
    assert events[3][0] == "audio_sent"
    assert len(events[3][1]) == 16
    assert events[4] == "stream_half_closed"


def test_capture_start_failure_marks_stream_incomplete(monkeypatch, tmp_path):
    class FailingCapture:
        def __init__(self, _settings, _output_queue, **_kwargs):
            pass

        async def start(self):
            raise RuntimeError("capture unavailable")

        async def stop(self):
            pass

    class FakeSessionManager:
        def get_session(self, _session_id):
            return SimpleNamespace(status=SessionStatus.ACTIVE)

    async def run():
        monkeypatch.setattr(backend_main, "AudioCapture", FailingCapture)
        monkeypatch.setattr(
            backend_main,
            "settings",
            Settings(
                google_cloud_project="test-project",
                audio_backup_dir=str(tmp_path / "recordings"),
            ),
        )
        monkeypatch.setattr(
            backend_main,
            "session_mgr",
            FakeSessionManager(),
        )
        managers = []
        await backend_main._run_single_audio_stream(
            "session-1", "device", "Entrevistador", [], [], managers,
        )
        return managers[0]

    manager = asyncio.run(run())

    assert manager.drain_completed is False
    assert manager.drain_failure_reason == "audio_pipeline_error"


def test_stop_pipeline_without_initialized_stream_is_incomplete(monkeypatch):
    session_id = "session-not-initialized"

    async def run():
        task = asyncio.create_task(asyncio.Event().wait())
        monkeypatch.setitem(backend_main.pipeline_tasks, session_id, [task])
        monkeypatch.setitem(backend_main.stream_managers, session_id, [])
        return await backend_main._stop_pipeline(session_id)

    assert asyncio.run(run()) is False
