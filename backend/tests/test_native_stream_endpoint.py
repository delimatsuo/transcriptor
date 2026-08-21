"""Unit tests for the native macOS companion WebSocket streaming endpoint."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch
import pytest

from backend import main
from backend.config import Settings


@pytest.fixture(autouse=True)
def configure_test_settings(monkeypatch):
    test_settings = Settings(
        google_cloud_project="test-project",
        auth_allowed_emails="test@example.com",
    )
    monkeypatch.setattr(main, "settings", test_settings)
    main.native_stream_managers.clear()
    main.stream_managers.clear()
    main.stream_keys.clear()
    yield
    main.native_stream_managers.clear()
    main.stream_managers.clear()
    main.stream_keys.clear()


def _encode_native_packet(header: dict, payload: bytes = b"\x00\x00" * 800) -> bytes:
    header_bytes = json.dumps(header).encode("utf-8")
    header_len = len(header_bytes).to_bytes(4, byteorder="big")
    return header_len + header_bytes + payload


class FakeNativeWebSocket:
    def __init__(self, incoming_messages: list[dict], query_params: dict | None = None):
        self.incoming = list(incoming_messages)
        self.sent_json: list[dict] = []
        self.accepted = False
        self.query_params = dict(query_params) if query_params else {}
        self.closed_code: int | None = None

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000):
        self.closed_code = code

    async def receive(self):
        if not self.incoming:
            return {"type": "websocket.disconnect", "code": 1000}
        msg = dict(self.incoming.pop(0))
        if "type" not in msg:
            msg["type"] = "websocket.receive"
        return msg

    async def send_json(self, data):
        self.sent_json.append(data)


class _MidStreamWebSocket(FakeNativeWebSocket):
    """A companion connection that stays "open" across two frames; runs
    `on_second_receive` (an async callable) once, right before delivering the
    second message. Used to simulate a still-connected companion socket racing
    a concurrent session stop between two of its own frames."""

    def __init__(self, incoming_messages, on_second_receive):
        super().__init__(incoming_messages)
        self._on_second_receive = on_second_receive
        self._receive_count = 0

    async def receive(self):
        self._receive_count += 1
        if self._receive_count == 2 and self._on_second_receive is not None:
            await self._on_second_receive()
        return await super().receive()


class _FakeSession:
    def __init__(self, status="active"):
        from backend.schemas.models import SessionStatus
        self.status = SessionStatus(status)


def _install_session(monkeypatch, session_id, key="k" * 43, status="active"):
    fake_mgr = type("M", (), {"get_session": lambda self, sid: _FakeSession(status) if sid == session_id else None})()
    monkeypatch.setattr(main, "session_mgr", fake_mgr)
    main.stream_keys[session_id] = key
    return key


def test_native_stream_rejects_missing_key(monkeypatch):
    _install_session(monkeypatch, "s1")
    ws = FakeNativeWebSocket([], )
    ws.query_params = {}
    asyncio.run(main.native_stream_endpoint(ws, "s1"))
    assert ws.accepted is False
    assert ws.closed_code == 1008


def test_native_stream_rejects_wrong_key(monkeypatch):
    _install_session(monkeypatch, "s1", key="rightkey")
    ws = FakeNativeWebSocket([])
    ws.query_params = {"stream_key": "wrongkey"}
    asyncio.run(main.native_stream_endpoint(ws, "s1"))
    assert ws.accepted is False and ws.closed_code == 1008


def test_native_stream_rejects_unknown_session(monkeypatch):
    monkeypatch.setattr(main, "session_mgr", type("M", (), {"get_session": lambda self, sid: None})())
    main.stream_keys.pop("ghost", None)
    ws = FakeNativeWebSocket([])
    ws.query_params = {"stream_key": "anything"}
    asyncio.run(main.native_stream_endpoint(ws, "ghost"))
    assert ws.accepted is False and ws.closed_code == 1008


def test_native_stream_accepts_valid_key(monkeypatch):
    key = _install_session(monkeypatch, "s1")
    ws = FakeNativeWebSocket([{"text": json.dumps({"type": "ping"})}])
    ws.query_params = {"stream_key": key}
    asyncio.run(main.native_stream_endpoint(ws, "s1"))
    assert ws.accepted is True
    assert ws.sent_json == [{"type": "pong"}]


def test_native_stream_endpoint_handles_ping(monkeypatch):
    key = _install_session(monkeypatch, "test-sess-ping")
    ws = FakeNativeWebSocket([
        {"text": json.dumps({"type": "ping"})}
    ])
    ws.query_params = {"stream_key": key}
    asyncio.run(main.native_stream_endpoint(ws, "test-sess-ping"))
    assert ws.accepted is True
    assert ws.sent_json == [{"type": "pong"}]


@patch("backend.main.StreamManager")
def test_native_stream_endpoint_routes_microphone_and_system_audio(mock_sm_cls, monkeypatch):
    mock_sm_instance = AsyncMock()
    mock_sm_cls.return_value = mock_sm_instance

    mic_packet = _encode_native_packet({"source": "microphone", "sequence": 1})
    sys_packet = _encode_native_packet({"source": "system_audio", "sequence": 1})

    key = _install_session(monkeypatch, "test-sess-dual")
    ws = FakeNativeWebSocket([
        {"bytes": mic_packet},
        {"bytes": sys_packet},
    ])
    ws.query_params = {"stream_key": key}

    asyncio.run(main.native_stream_endpoint(ws, "test-sess-dual"))

    assert ws.accepted is True
    # Verify StreamManager instances were created and audio was forwarded
    assert mock_sm_cls.call_count == 2
    assert mock_sm_instance.start.call_count == 2
    assert mock_sm_instance.send_audio.call_count == 2
    # Session-scoped StreamManagers: a companion disconnect must not stop/drain
    # them. Only _stop_pipeline (session end) does that.
    assert mock_sm_instance.stop.call_count == 0


def test_native_stream_endpoint_handles_short_packets_gracefully(monkeypatch):
    key = _install_session(monkeypatch, "test-sess-malformed")
    ws = FakeNativeWebSocket([
        {"bytes": b"\x00\x01"},
        {"bytes": b"\x00\x00\x00\x05hello"},
        {"text": json.dumps({"type": "ping"})},
    ])
    ws.query_params = {"stream_key": key}

    asyncio.run(main.native_stream_endpoint(ws, "test-sess-malformed"))
    assert ws.accepted is True
    assert ws.sent_json == [{"type": "pong"}]


def test_native_stream_endpoint_handles_gap_messages(monkeypatch):
    key = _install_session(monkeypatch, "test-sess-gap")
    ws = FakeNativeWebSocket([
        {"text": json.dumps({"type": "gap", "source": "system_audio", "reason": "overrun", "first_sample": 16000})},
        {"text": json.dumps({"type": "ping"})},
    ])
    ws.query_params = {"stream_key": key}

    asyncio.run(main.native_stream_endpoint(ws, "test-sess-gap"))
    assert ws.accepted is True
    assert ws.sent_json == [{"type": "pong"}]


def test_native_stream_endpoint_handles_corrupt_json_header(monkeypatch):
    # Length is 4 bytes indicating 10 bytes header, but header is invalid JSON
    bad_header = b"\x00\x00\x00\x0a{invalid:}" + (b"\x00\x00" * 800)
    key = _install_session(monkeypatch, "test-sess-bad-json")
    ws = FakeNativeWebSocket([
        {"bytes": bad_header},
        {"text": json.dumps({"type": "ping"})},
    ])
    ws.query_params = {"stream_key": key}

    asyncio.run(main.native_stream_endpoint(ws, "test-sess-bad-json"))
    assert ws.accepted is True
    assert ws.sent_json == [{"type": "pong"}]


@patch("backend.main.StreamManager")
def test_native_stream_endpoint_testclient_e2e(mock_sm_cls, monkeypatch):
    from starlette.testclient import TestClient
    mock_sm_instance = AsyncMock()
    mock_sm_cls.return_value = mock_sm_instance

    key = _install_session(monkeypatch, "test-sess-client-e2e")
    client = TestClient(main.app)
    with client.websocket_connect(f"/api/stream/native/test-sess-client-e2e?stream_key={key}") as ws:
        # Ping
        ws.send_text(json.dumps({"type": "ping"}))
        response = ws.receive_json()
        assert response == {"type": "pong"}

        # Gap
        ws.send_text(json.dumps({"type": "gap", "source": "microphone", "reason": "device_lost", "first_sample": 8000}))

        # Mic audio packet
        mic_packet = _encode_native_packet({
            "session_id": "test-sess-client-e2e",
            "source": "microphone",
            "sequence": 1,
            "first_sample": 0,
            "captured_at_ms": 1000,
            "sample_rate": 16000,
            "channel_count": 1,
            "duration_ms": 50,
        }, payload=b"\x01\x00" * 800)
        ws.send_bytes(mic_packet)

        # Sys audio packet
        sys_packet = _encode_native_packet({
            "session_id": "test-sess-client-e2e",
            "source": "system_audio",
            "sequence": 1,
            "first_sample": 0,
            "captured_at_ms": 1000,
            "sample_rate": 16000,
            "channel_count": 1,
            "duration_ms": 50,
        }, payload=b"\x02\x00" * 800)
        ws.send_bytes(sys_packet)

    # After websocket context exits (disconnect), verify StreamManagers were started
    # and fed, but NOT stopped: session-scoped SMs survive a companion disconnect.
    assert mock_sm_cls.call_count == 2
    assert mock_sm_instance.start.call_count == 2
    assert mock_sm_instance.send_audio.call_count == 2
    assert mock_sm_instance.stop.call_count == 0


@patch("backend.main.StreamManager")
def test_stream_managers_survive_reconnect(mock_sm_cls, monkeypatch):
    mock_sm = AsyncMock()
    mock_sm_cls.return_value = mock_sm
    key = _install_session(monkeypatch, "s-reconnect")
    header = {"session_id": "s-reconnect", "source": "system_audio", "sequence": 1,
              "first_sample": 0, "captured_at_ms": 0, "sample_rate": 16000,
              "channel_count": 1, "duration_ms": 50}
    for _ in range(2):  # two sequential connections = drop + reconnect
        ws = FakeNativeWebSocket([{"bytes": _encode_native_packet(header)}])
        ws.query_params = {"stream_key": key}
        asyncio.run(main.native_stream_endpoint(ws, "s-reconnect"))
    assert mock_sm_cls.call_count == 1          # one SM per source per SESSION, not per connection
    assert mock_sm.send_audio.await_count == 2  # both connections fed it
    mock_sm.stop.assert_not_awaited()           # disconnect must not drain/stop
    assert "s-reconnect" in main.stream_managers  # registry intact for drain accounting


def test_stop_pipeline_stops_native_sms(monkeypatch):
    sm = AsyncMock()
    sm.drain_completed = True
    main.native_stream_managers["s-stop"] = {"Candidato": sm}
    main.stream_managers["s-stop"] = [sm]
    main.stream_keys["s-stop"] = "k"
    result = asyncio.run(main._stop_pipeline("s-stop"))
    assert result is True
    sm.stop.assert_awaited_once()
    assert "s-stop" not in main.native_stream_managers
    assert "s-stop" not in main.stream_keys


def test_stop_pipeline_survives_legacy_pipeline_pop(monkeypatch):
    """Regression test for a review finding: a legacy _run_audio_pipeline task
    (host_audio_capture_enabled=True) shares stream_managers[session_id] with
    the native gateway. Cancelling that task drives its own finally, which
    does stream_managers.pop(session_id, None) (backend/main.py:563) — before
    _stop_pipeline previously got a chance to read the list. The drain verdict
    must still reflect the SM's real (already-drained) state instead of a
    false "no managers found" failure.
    """
    sm = AsyncMock()
    sm.drain_completed = True
    main.native_stream_managers["s-legacy"] = {"Candidato": sm}
    main.stream_managers["s-legacy"] = [sm]
    main.stream_keys["s-legacy"] = "k"

    async def fake_legacy_task():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # Mimics _run_audio_pipeline's own finally block.
            main.stream_managers.pop("s-legacy", None)
            raise

    async def scenario():
        task = asyncio.create_task(fake_legacy_task())
        await asyncio.sleep(0)  # let it reach the try/sleep before cancellation
        main.pipeline_tasks["s-legacy"] = [task]
        return await main._stop_pipeline("s-legacy")

    result = asyncio.run(scenario())
    assert result is True
    sm.stop.assert_awaited_once()


@patch("backend.main.StreamManager")
def test_get_or_create_sm_refuses_new_sm_after_stop_pipeline(mock_sm_cls, monkeypatch):
    """Regression test for a review finding: a companion socket that is still
    open (already past the accept-time auth guard) must not be able to spin up
    a fresh, never-stopped StreamManager for a source label it hasn't used yet
    once _stop_pipeline has already torn the session down out from under it.
    """
    mock_sm = AsyncMock()
    mock_sm.drain_completed = True
    mock_sm_cls.return_value = mock_sm
    key = _install_session(monkeypatch, "s-mid-stop")

    mic_header = {"session_id": "s-mid-stop", "source": "microphone", "sequence": 1,
                  "first_sample": 0, "captured_at_ms": 0, "sample_rate": 16000,
                  "channel_count": 1, "duration_ms": 50}
    sys_header = {**mic_header, "source": "system_audio", "sequence": 2}

    async def stop_session_mid_flight():
        await main._stop_pipeline("s-mid-stop")

    ws = _MidStreamWebSocket(
        [
            {"bytes": _encode_native_packet(mic_header)},
            {"bytes": _encode_native_packet(sys_header)},
        ],
        on_second_receive=stop_session_mid_flight,
    )
    ws.query_params = {"stream_key": key}

    asyncio.run(main.native_stream_endpoint(ws, "s-mid-stop"))

    # Only the microphone SM (created before the mid-flight stop) was ever
    # built. The system_audio frame, arriving after _stop_pipeline already
    # popped this session's registries, must be refused rather than spin up
    # an orphaned StreamManager nothing will ever stop.
    assert mock_sm_cls.call_count == 1
    assert mock_sm.send_audio.await_count == 1


@patch("backend.main.StreamManager")
def test_get_or_create_sm_refuses_new_sm_when_stream_key_popped(mock_sm_cls, monkeypatch):
    """Narrower regression test isolating the stream_keys gate itself: even if
    only stream_keys[session_id] is gone (without native_stream_managers also
    being cleared), get_or_create_sm must still refuse to build a new SM."""
    mock_sm = AsyncMock()
    mock_sm_cls.return_value = mock_sm
    key = _install_session(monkeypatch, "s-key-popped")

    mic_header = {"session_id": "s-key-popped", "source": "microphone", "sequence": 1,
                  "first_sample": 0, "captured_at_ms": 0, "sample_rate": 16000,
                  "channel_count": 1, "duration_ms": 50}
    sys_header = {**mic_header, "source": "system_audio", "sequence": 2}

    async def pop_stream_key_only():
        main.stream_keys.pop("s-key-popped", None)

    ws = _MidStreamWebSocket(
        [
            {"bytes": _encode_native_packet(mic_header)},
            {"bytes": _encode_native_packet(sys_header)},
        ],
        on_second_receive=pop_stream_key_only,
    )
    ws.query_params = {"stream_key": key}

    asyncio.run(main.native_stream_endpoint(ws, "s-key-popped"))

    assert mock_sm_cls.call_count == 1           # only the microphone SM was ever built
    assert mock_sm.send_audio.await_count == 1   # system_audio frame after the pop was refused


def _health_msgs(fake_ws_manager):
    return [c.args[1] for c in fake_ws_manager.broadcast.await_args_list
            if c.args[1].type.value == "companion_health"]


def test_health_emitted_on_connect_first_frame_and_disconnect(monkeypatch):
    fake_wsm = type("W", (), {"broadcast": AsyncMock(), "next_sequence": staticmethod(lambda sid: 1)})()
    monkeypatch.setattr(main, "ws_manager", fake_wsm)
    key = _install_session(monkeypatch, "s-h")
    header = {"session_id": "s-h", "source": "system_audio", "sequence": 1, "first_sample": 0,
              "captured_at_ms": 0, "sample_rate": 16000, "channel_count": 1, "duration_ms": 50}
    with patch("backend.main.StreamManager") as sm_cls:
        sm_cls.return_value = AsyncMock()
        ws = FakeNativeWebSocket([{"bytes": _encode_native_packet(header)}])
        ws.query_params = {"stream_key": key}
        asyncio.run(main.native_stream_endpoint(ws, "s-h"))
    msgs = _health_msgs(fake_wsm)
    assert msgs[0].payload["physical_capture"] == "active"
    assert any(m.payload["sources"]["system_audio"] == "healthy" for m in msgs)
    assert msgs[-1].payload["physical_capture"] == "stopped"


def test_gap_rebroadcast_as_coverage_gap(monkeypatch):
    fake_wsm = type("W", (), {"broadcast": AsyncMock(), "next_sequence": staticmethod(lambda sid: 1)})()
    monkeypatch.setattr(main, "ws_manager", fake_wsm)
    key = _install_session(monkeypatch, "s-g")
    gap = {"type": "gap", "source": "system_audio", "reason": "device_lost", "first_sample": 16000}
    ws = FakeNativeWebSocket([{"text": json.dumps(gap)}])
    ws.query_params = {"stream_key": key}
    asyncio.run(main.native_stream_endpoint(ws, "s-g"))
    gaps = [c.args[1] for c in fake_wsm.broadcast.await_args_list if c.args[1].type.value == "coverage_gap"]
    assert len(gaps) == 1
    assert gaps[0].payload["gap"]["reason"] == "device_lost"
    assert gaps[0].payload["gap"]["start_ms"] == 1000.0


def test_stall_watchdog_flags_and_recovers_source_health(monkeypatch):
    """Deterministic stall-watchdog coverage (no real 10s waits): the check
    interval and stall timeout are monkeypatched down to a few milliseconds,
    and a real (short) pause is injected between two frames from the same
    source via _MidStreamWebSocket so the concurrently running watchdog task
    gets scheduler turns to observe the stall and flag device_unavailable;
    the second frame from the same source then recovers it to healthy."""
    fake_wsm = type("W", (), {"broadcast": AsyncMock(), "next_sequence": staticmethod(lambda sid: 1)})()
    monkeypatch.setattr(main, "ws_manager", fake_wsm)
    monkeypatch.setattr(main, "NATIVE_STALL_CHECK_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(main, "NATIVE_STALL_TIMEOUT_SECONDS", 0.03)
    key = _install_session(monkeypatch, "s-stall")
    header = {"session_id": "s-stall", "source": "system_audio", "sequence": 1, "first_sample": 0,
              "captured_at_ms": 0, "sample_rate": 16000, "channel_count": 1, "duration_ms": 50}

    async def pause_past_stall_threshold():
        await asyncio.sleep(0.2)

    with patch("backend.main.StreamManager") as sm_cls:
        sm_cls.return_value = AsyncMock()
        ws = _MidStreamWebSocket(
            [
                {"bytes": _encode_native_packet(header)},
                {"bytes": _encode_native_packet(header)},
            ],
            on_second_receive=pause_past_stall_threshold,
        )
        ws.query_params = {"stream_key": key}
        asyncio.run(main.native_stream_endpoint(ws, "s-stall"))

    msgs = _health_msgs(fake_wsm)
    states = [m.payload["sources"]["system_audio"] for m in msgs]
    assert "device_unavailable" in states
    stalled_at = states.index("device_unavailable")
    assert "healthy" in states[stalled_at + 1:]  # next frame recovers it
    assert msgs[-1].payload["physical_capture"] == "stopped"

