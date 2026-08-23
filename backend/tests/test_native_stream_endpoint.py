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
    main.native_session_health.clear()
    main.native_frame_last_seq.clear()
    yield
    main.native_stream_managers.clear()
    main.stream_managers.clear()
    main.stream_keys.clear()
    main.native_session_health.clear()
    main.native_frame_last_seq.clear()


def _encode_native_packet(header: dict, payload: bytes = b"\x00\x00" * 800) -> bytes:
    header_bytes = json.dumps(header).encode("utf-8")
    header_len = len(header_bytes).to_bytes(4, byteorder="big")
    return header_len + header_bytes + payload


class FakeNativeWebSocket:
    def __init__(self, incoming_messages: list[dict], query_params: dict | None = None, headers: dict | None = None):
        self.incoming = list(incoming_messages)
        self.sent_json: list[dict] = []
        self.accepted = False
        self.accepted_subprotocol: str | None = None
        self.query_params = dict(query_params) if query_params else {}
        self.headers = dict(headers) if headers else {}
        self.closed_code: int | None = None

    async def accept(self, subprotocol: str | None = None):
        self.accepted = True
        self.accepted_subprotocol = subprotocol

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

    def __init__(self, incoming_messages, on_second_receive, query_params=None, headers=None):
        super().__init__(incoming_messages, query_params=query_params, headers=headers)
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


def test_native_stream_rejects_non_ascii_key_without_raising(monkeypatch):
    """Regression test for a review finding: secrets.compare_digest(str, str)
    raises TypeError on non-ASCII input before reaching the clean 1008
    close, leaving a traceback for an attacker-controlled query param. The
    probe must be cleanly rejected with no exception escaping the endpoint."""
    _install_session(monkeypatch, "s-nonascii", key="rightkey")
    ws = FakeNativeWebSocket([])
    ws.query_params = {"stream_key": "café☃🔥"}
    asyncio.run(main.native_stream_endpoint(ws, "s-nonascii"))
    assert ws.accepted is False
    assert ws.closed_code == 1008


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
    # A mere WS reconnect (network blip) does not restart the companion
    # process, so its own sequence counter keeps incrementing across it —
    # only a companion PROCESS restart legitimately resets sequence to 1
    # (see the dedup-guard restart test below). Each reconnect iteration
    # here sends the next sequence number so this scenario isn't conflated
    # with the replay-dedup guard: this test is specifically about
    # StreamManager surviving reconnects, not about duplicate sequences.
    for i in range(2):  # two sequential connections = drop + reconnect
        header = {"session_id": "s-reconnect", "source": "system_audio", "sequence": i + 1,
                  "first_sample": 0, "captured_at_ms": 0, "sample_rate": 16000,
                  "channel_count": 1, "duration_ms": 50}
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
    # Finding 1 + Finding 2 module state must be torn down alongside the
    # other per-session state, not left to leak across sessions.
    main.native_session_health["s-stop"] = {
        "sources": {"microphone": "healthy", "system_audio": "unknown"},
        "connections": 1,
    }
    main.native_frame_last_seq["s-stop"] = {"system_audio": 42}
    result = asyncio.run(main._stop_pipeline("s-stop"))
    assert result is True
    sm.stop.assert_awaited_once()
    assert "s-stop" not in main.native_stream_managers
    assert "s-stop" not in main.stream_keys
    assert "s-stop" not in main.native_session_health
    assert "s-stop" not in main.native_frame_last_seq


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
    assert msgs[-1].payload["sources"]["system_audio"] == "reconnecting"
    assert msgs[-1].payload["physical_capture"] == "unknown"


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
    assert msgs[-1].payload["physical_capture"] == "unknown"


# --- Finding 1: merged per-session health view across concurrent connections ---
#
# native_stream_endpoint serves MULTIPLE concurrent WS connections per
# session (browser mic sends source="microphone"; companion sends
# source="system_audio"). Before the fix, each connection kept its OWN
# CompanionHealthPayload from scratch, so a companion connecting/disconnecting
# after the browser would wipe the mic badge to "unknown" (and briefly report
# physical_capture="stopped") even while the browser's mic connection was
# still live. FakeNativeWebSocket-driven tests run everything on one thread
# sequentially, so true concurrency is simulated via _MidStreamWebSocket's
# on_second_receive hook: it runs connection B's *entire* endpoint lifecycle
# (connect through disconnect) in the middle of connection A's own receive
# loop, while A is still considered "open".

def test_health_companion_connect_disconnect_does_not_clobber_open_mic_connection(monkeypatch):
    """(a) A (microphone) goes healthy, then a companion-style connection B
    connects and disconnects with NO frames of its own while A is still open.
    No broadcast observed during B's lifetime may ever regress microphone
    away from healthy, or report physical_capture as anything but active;
    physical_capture must reach "unknown" once A itself later closes
    too (i.e. once both connections have closed) on an active session."""
    fake_wsm = type("W", (), {"broadcast": AsyncMock(), "next_sequence": staticmethod(lambda sid: 1)})()
    monkeypatch.setattr(main, "ws_manager", fake_wsm)
    session_id = "s-merge-a"
    key = _install_session(monkeypatch, session_id)
    mic_header = {"session_id": session_id, "source": "microphone", "sequence": 1,
                  "first_sample": 0, "captured_at_ms": 0, "sample_rate": 16000,
                  "channel_count": 1, "duration_ms": 50}
    captured: dict = {}

    async def run_companion_b():
        ws_b = FakeNativeWebSocket([])  # connects, observes nothing, disconnects
        ws_b.query_params = {"stream_key": key}
        await main.native_stream_endpoint(ws_b, session_id)
        # Snapshot right after B has fully closed, while A is still open.
        captured["mid_b_msgs"] = list(_health_msgs(fake_wsm))

    with patch("backend.main.StreamManager") as mock_sm_cls:
        mock_sm_cls.return_value = AsyncMock()
        ws_a = _MidStreamWebSocket(
            [{"bytes": _encode_native_packet(mic_header)}],
            on_second_receive=run_companion_b,
        )
        ws_a.query_params = {"stream_key": key}
        asyncio.run(main.native_stream_endpoint(ws_a, session_id))

    mid_b_msgs = captured["mid_b_msgs"]
    assert mid_b_msgs, "expected at least one health broadcast by the time B closed"
    healthy_seen = False
    for m in mid_b_msgs:
        if m.payload["sources"]["microphone"] == "healthy":
            healthy_seen = True
        if healthy_seen:
            assert m.payload["sources"]["microphone"] == "healthy", (
                "microphone regressed away from healthy while A was still open"
            )
        assert m.payload["physical_capture"] == "active", (
            "physical_capture must stay active while A (browser mic) is still open"
        )
    assert healthy_seen

    # When A *itself* later closes (both connections now closed on active session),
    # physical_capture reaches "unknown" (reconnecting).
    all_msgs = _health_msgs(fake_wsm)
    assert all_msgs[-1].payload["physical_capture"] == "unknown"


def test_health_companion_disconnect_only_resets_its_own_source(monkeypatch):
    """(b) A companion-style connection B produces system_audio frames (goes
    healthy) then closes while A (microphone, already healthy) is still open.
    system_audio becomes "reconnecting"; microphone must stay healthy
    and physical_capture must stay active, since A is still connected."""
    fake_wsm = type("W", (), {"broadcast": AsyncMock(), "next_sequence": staticmethod(lambda sid: 1)})()
    monkeypatch.setattr(main, "ws_manager", fake_wsm)
    session_id = "s-merge-b"
    key = _install_session(monkeypatch, session_id)
    mic_header = {"session_id": session_id, "source": "microphone", "sequence": 1,
                  "first_sample": 0, "captured_at_ms": 0, "sample_rate": 16000,
                  "channel_count": 1, "duration_ms": 50}
    sys_header = {**mic_header, "source": "system_audio", "sequence": 1}
    captured: dict = {}

    async def run_companion_b():
        ws_b = FakeNativeWebSocket([{"bytes": _encode_native_packet(sys_header)}])
        ws_b.query_params = {"stream_key": key}
        await main.native_stream_endpoint(ws_b, session_id)
        captured["mid_b_msgs"] = list(_health_msgs(fake_wsm))

    with patch("backend.main.StreamManager") as mock_sm_cls:
        mock_sm_cls.return_value = AsyncMock()
        ws_a = _MidStreamWebSocket(
            [{"bytes": _encode_native_packet(mic_header)}],
            on_second_receive=run_companion_b,
        )
        ws_a.query_params = {"stream_key": key}
        asyncio.run(main.native_stream_endpoint(ws_a, session_id))

    last = captured["mid_b_msgs"][-1]
    assert last.payload["sources"]["system_audio"] == "reconnecting"
    assert last.payload["sources"]["microphone"] == "healthy"
    assert last.payload["physical_capture"] == "active"


# --- Finding 2: windowed per-source sequence dedup at the gateway ---
#
# Replay-after-timeout is designed behavior (the companion may resend a frame
# whose send() timed out but actually landed), which can otherwise feed one
# 50ms chunk to STT twice. A frame is dropped (send_audio skipped) only when
# its sequence is <= the last accepted sequence for that (session, source)
# AND the backward gap is small (< 200, ~10s at 50ms/frame); a larger
# backward jump is treated as a legitimate companion-process restart
# (sequence counter restarts at 1) and passes through.

def _dedup_header(session_id: str, seq: int) -> dict:
    return {"session_id": session_id, "source": "system_audio", "sequence": seq,
            "first_sample": 0, "captured_at_ms": 0, "sample_rate": 16000,
            "channel_count": 1, "duration_ms": 50}


@patch("backend.main.StreamManager")
def test_frame_dedup_drops_replay_within_window(mock_sm_cls, monkeypatch):
    """(1) seq 5 followed by another seq 5 for the same (session, source)
    within the window is a replay, not new audio: the second is dropped."""
    mock_sm = AsyncMock()
    mock_sm_cls.return_value = mock_sm
    key = _install_session(monkeypatch, "s-dedup-1")
    ws = FakeNativeWebSocket([
        {"bytes": _encode_native_packet(_dedup_header("s-dedup-1", 5))},
        {"bytes": _encode_native_packet(_dedup_header("s-dedup-1", 5))},  # replay
    ])
    ws.query_params = {"stream_key": key}
    asyncio.run(main.native_stream_endpoint(ws, "s-dedup-1"))
    assert mock_sm.send_audio.await_count == 1  # duplicate dropped, count unchanged


@patch("backend.main.StreamManager")
def test_frame_dedup_accepts_large_backward_jump_as_restart(mock_sm_cls, monkeypatch):
    """(2) seq 1 after seq 5000 is a backward jump far larger than the
    window — a legitimate companion-process restart, not a replay — and
    must be accepted."""
    mock_sm = AsyncMock()
    mock_sm_cls.return_value = mock_sm
    key = _install_session(monkeypatch, "s-dedup-2")
    ws = FakeNativeWebSocket([
        {"bytes": _encode_native_packet(_dedup_header("s-dedup-2", 5000))},
        {"bytes": _encode_native_packet(_dedup_header("s-dedup-2", 1))},  # restart-style reset
    ])
    ws.query_params = {"stream_key": key}
    asyncio.run(main.native_stream_endpoint(ws, "s-dedup-2"))
    assert mock_sm.send_audio.await_count == 2  # both accepted


@patch("backend.main.StreamManager")
def test_frame_dedup_accepts_normal_increments(mock_sm_cls, monkeypatch):
    """(3) Ordinary strictly-increasing sequence numbers are never treated
    as duplicates."""
    mock_sm = AsyncMock()
    mock_sm_cls.return_value = mock_sm
    key = _install_session(monkeypatch, "s-dedup-3")
    ws = FakeNativeWebSocket([
        {"bytes": _encode_native_packet(_dedup_header("s-dedup-3", 1))},
        {"bytes": _encode_native_packet(_dedup_header("s-dedup-3", 2))},
        {"bytes": _encode_native_packet(_dedup_header("s-dedup-3", 3))},
    ])
    ws.query_params = {"stream_key": key}
    asyncio.run(main.native_stream_endpoint(ws, "s-dedup-3"))
    assert mock_sm.send_audio.await_count == 3


@patch("backend.main.StreamManager")
def test_frame_dedup_restart_baseline_resets_not_max(mock_sm_cls, monkeypatch):
    """Re-review regression test: after a restart-style backward jump is
    accepted, the tracked baseline must be REPLACED with the new stream's
    sequence, not max()-ed against the old (now-irrelevant) high-water mark.
    Under max()-ing, the stale baseline survives the restart, and once the
    new stream's own sequence climbs back within NATIVE_FRAME_DEDUP_WINDOW
    of it, genuine post-restart frames get misread as within-window replays
    of the OLD stream and silently dropped — here, seq 60 would wrongly
    read as a replay of the old stream's seq 250 (250-60=190 < 200)."""
    mock_sm = AsyncMock()
    mock_sm_cls.return_value = mock_sm
    key = _install_session(monkeypatch, "s-dedup-restart-baseline")
    ws = FakeNativeWebSocket([
        {"bytes": _encode_native_packet(_dedup_header("s-dedup-restart-baseline", 250))},  # old stream reaches 250
        {"bytes": _encode_native_packet(_dedup_header("s-dedup-restart-baseline", 1))},    # restart: jump 249 >= 200, accepted
        {"bytes": _encode_native_packet(_dedup_header("s-dedup-restart-baseline", 60))},   # new stream's own frame 60
    ])
    ws.query_params = {"stream_key": key}
    asyncio.run(main.native_stream_endpoint(ws, "s-dedup-restart-baseline"))
    assert mock_sm.send_audio.await_count == 3  # all three forwarded, none silently dropped


def test_native_stream_accepts_subprotocol_key(monkeypatch):
    key = _install_session(monkeypatch, "s-sub-1")
    ws = FakeNativeWebSocket(
        [{"text": json.dumps({"type": "ping"})}],
        headers={"sec-websocket-protocol": f"tars-stream, {key}"},
    )
    asyncio.run(main.native_stream_endpoint(ws, "s-sub-1"))
    assert ws.accepted is True
    assert ws.accepted_subprotocol == "tars-stream"
    assert ws.sent_json == [{"type": "pong"}]


def test_native_stream_rejects_wrong_subprotocol_key(monkeypatch):
    _install_session(monkeypatch, "s-sub-2", key="rightkey")
    ws = FakeNativeWebSocket(
        [],
        headers={"sec-websocket-protocol": "tars-stream, wrongkey"},
    )
    asyncio.run(main.native_stream_endpoint(ws, "s-sub-2"))
    assert ws.accepted is False
    assert ws.closed_code == 1008


def test_native_stream_rejects_unknown_subprotocol_name(monkeypatch):
    key = _install_session(monkeypatch, "s-sub-3")
    ws = FakeNativeWebSocket(
        [],
        headers={"sec-websocket-protocol": f"something-else, {key}"},
    )
    asyncio.run(main.native_stream_endpoint(ws, "s-sub-3"))
    assert ws.accepted is False
    assert ws.closed_code == 1008


def test_native_stream_query_key_still_works_and_warns(monkeypatch):
    key = _install_session(monkeypatch, "s-query-warn")
    warnings_recorded = []
    original_warning = main.logger.warning

    def fake_warning(event, **kw):
        warnings_recorded.append((event, kw))
        try:
            return original_warning(event, **kw)
        except Exception:
            pass

    monkeypatch.setattr(main.logger, "warning", fake_warning)
    ws = FakeNativeWebSocket(
        [{"text": json.dumps({"type": "ping"})}],
        query_params={"stream_key": key},
    )
    asyncio.run(main.native_stream_endpoint(ws, "s-query-warn"))
    assert ws.accepted is True
    assert ws.accepted_subprotocol is None
    assert ws.sent_json == [{"type": "pong"}]
    assert warnings_recorded == [
        ("native_stream_query_key_deprecated", {"session_id": "s-query-warn"})
    ]


def test_native_stream_rejects_subprotocol_with_extra_empty_entry(monkeypatch):
    key = _install_session(monkeypatch, "s-sub-extra-empty")
    ws = FakeNativeWebSocket(
        [],
        headers={"sec-websocket-protocol": f"tars-stream, {key},"},
    )
    asyncio.run(main.native_stream_endpoint(ws, "s-sub-extra-empty"))
    assert ws.accepted is False
    assert ws.closed_code == 1008


def test_hello_never_produced_source_alarms_then_recovers(monkeypatch):
    fake_wsm = type("W", (), {"broadcast": AsyncMock(), "next_sequence": staticmethod(lambda sid: 1)})()
    monkeypatch.setattr(main, "ws_manager", fake_wsm)
    monkeypatch.setattr(main, "NATIVE_STALL_CHECK_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(main, "NATIVE_NEVER_PRODUCED_TIMEOUT_SECONDS", 0.03)
    session_id = "s-never-prod"
    key = _install_session(monkeypatch, session_id)

    warnings: list[tuple[str, dict]] = []
    orig_warning = main.logger.warning

    def fake_warning(event, **kw):
        warnings.append((event, kw))
        try:
            return orig_warning(event, **kw)
        except Exception:
            pass

    monkeypatch.setattr(main.logger, "warning", fake_warning)

    hello_msg = {"type": "hello", "sources": ["system_audio"]}
    frame_header = {
        "session_id": session_id,
        "source": "system_audio",
        "sequence": 1,
        "first_sample": 0,
        "captured_at_ms": 0,
        "sample_rate": 16000,
        "channel_count": 1,
        "duration_ms": 50,
    }

    async def pause_past_timeout():
        await asyncio.sleep(0.1)

    with patch("backend.main.StreamManager") as sm_cls:
        sm_cls.return_value = AsyncMock()
        ws = _MidStreamWebSocket(
            [
                {"text": json.dumps(hello_msg)},
                {"bytes": _encode_native_packet(frame_header)},
            ],
            on_second_receive=pause_past_timeout,
            headers={"sec-websocket-protocol": f"tars-stream, {key}"},
        )
        asyncio.run(main.native_stream_endpoint(ws, session_id))

    msgs = _health_msgs(fake_wsm)
    expected_msg = (
        "Nenhum frame recebido de Áudio do Sistema em 15 s. "
        "Verifique se há áudio em reprodução e se a permissão do companion está ativa."
    )
    alarm_seen = any(
        m.payload["sources"]["system_audio"] == "device_unavailable"
        and m.payload.get("message") == expected_msg
        for m in msgs
    )
    assert alarm_seen

    recovered_seen = any(
        m.payload["sources"]["system_audio"] == "healthy"
        and m.payload.get("message") is None
        for m in msgs
    )
    assert recovered_seen

    never_prod_logs = [
        kw for e, kw in warnings
        if e == "native_source_never_produced_frames"
        and kw.get("session_id") == session_id
        and kw.get("source") == "system_audio"
    ]
    assert len(never_prod_logs) == 1


def test_announced_source_disconnect_is_reconnecting_not_stopped(monkeypatch):
    fake_wsm = type("W", (), {"broadcast": AsyncMock(), "next_sequence": staticmethod(lambda sid: 1)})()
    monkeypatch.setattr(main, "ws_manager", fake_wsm)
    session_id = "s-reconnect-disc"
    key = _install_session(monkeypatch, session_id)

    hello_msg = {"type": "hello", "sources": ["system_audio"]}
    ws = FakeNativeWebSocket(
        [{"text": json.dumps(hello_msg)}],
        headers={"sec-websocket-protocol": f"tars-stream, {key}"},
    )
    asyncio.run(main.native_stream_endpoint(ws, session_id))

    msgs = _health_msgs(fake_wsm)
    last_msg = msgs[-1]
    assert last_msg.payload["sources"]["system_audio"] == "reconnecting"
    assert last_msg.payload["physical_capture"] == "unknown"
    assert last_msg.payload.get("message") is None


def test_session_stop_disconnect_is_stopped_not_reconnecting(monkeypatch):
    fake_wsm = type("W", (), {"broadcast": AsyncMock(), "next_sequence": staticmethod(lambda sid: 1)})()
    monkeypatch.setattr(main, "ws_manager", fake_wsm)
    session_id = "s-stop-disc"
    key = _install_session(monkeypatch, session_id)

    async def simulate_session_stop():
        main.stream_keys.pop(session_id, None)

    hello_msg = {"type": "hello", "sources": ["system_audio"]}
    ws = _MidStreamWebSocket(
        [
            {"text": json.dumps(hello_msg)},
            {"text": json.dumps({"type": "ping"})},
        ],
        on_second_receive=simulate_session_stop,
        headers={"sec-websocket-protocol": f"tars-stream, {key}"},
    )
    asyncio.run(main.native_stream_endpoint(ws, session_id))

    msgs = _health_msgs(fake_wsm)
    last_msg = msgs[-1]
    assert last_msg.payload["sources"]["system_audio"] == "unknown"
    assert last_msg.payload["physical_capture"] == "stopped"


def test_invalid_hello_does_not_claim_sources(monkeypatch):
    fake_wsm = type("W", (), {"broadcast": AsyncMock(), "next_sequence": staticmethod(lambda sid: 1)})()
    monkeypatch.setattr(main, "ws_manager", fake_wsm)
    session_id = "s-inv-hello"
    key = _install_session(monkeypatch, session_id)

    warnings: list[tuple[str, dict]] = []
    orig_warning = main.logger.warning

    def fake_warning(event, **kw):
        warnings.append((event, kw))
        try:
            return orig_warning(event, **kw)
        except Exception:
            pass

    monkeypatch.setattr(main.logger, "warning", fake_warning)

    bad_hello = {"type": "hello", "sources": ["system_audio", "not-a-source"]}
    ws = FakeNativeWebSocket(
        [{"text": json.dumps(bad_hello)}],
        headers={"sec-websocket-protocol": f"tars-stream, {key}"},
    )
    asyncio.run(main.native_stream_endpoint(ws, session_id))

    msgs = _health_msgs(fake_wsm)
    last_msg = msgs[-1]
    assert last_msg.payload["sources"]["system_audio"] == "unknown"
    assert last_msg.payload["sources"]["microphone"] == "unknown"
    assert last_msg.payload["physical_capture"] == "stopped"

    invalid_logs = [
        kw for e, kw in warnings
        if e == "native_companion_hello_invalid"
    ]
    assert len(invalid_logs) == 1
    assert invalid_logs[0] == {"session_id": session_id}


def test_overlapping_same_source_disconnect_preserves_live_owner(monkeypatch):
    fake_wsm = type("W", (), {"broadcast": AsyncMock(), "next_sequence": staticmethod(lambda sid: 1)})()
    monkeypatch.setattr(main, "ws_manager", fake_wsm)
    session_id = "s-overlap"
    key = _install_session(monkeypatch, session_id)
    mic_header = {
        "session_id": session_id,
        "source": "microphone",
        "sequence": 1,
        "first_sample": 0,
        "captured_at_ms": 0,
        "sample_rate": 16000,
        "channel_count": 1,
        "duration_ms": 50,
    }
    captured: dict = {}

    async def run_companion_b():
        ws_b = FakeNativeWebSocket(
            [{"text": json.dumps({"type": "hello", "sources": ["microphone"]})}],
            headers={"sec-websocket-protocol": f"tars-stream, {key}"},
        )
        await main.native_stream_endpoint(ws_b, session_id)
        captured["mid_b_msgs"] = list(_health_msgs(fake_wsm))

    with patch("backend.main.StreamManager") as mock_sm_cls:
        mock_sm_cls.return_value = AsyncMock()
        ws_a = _MidStreamWebSocket(
            [{"bytes": _encode_native_packet(mic_header)}],
            on_second_receive=run_companion_b,
            headers={"sec-websocket-protocol": f"tars-stream, {key}"},
        )
        asyncio.run(main.native_stream_endpoint(ws_a, session_id))

    mid_b_msgs = captured["mid_b_msgs"]
    last_after_b = mid_b_msgs[-1]
    assert last_after_b.payload["sources"]["microphone"] == "healthy"
    assert last_after_b.payload["physical_capture"] == "active"


def test_never_produced_overlap_does_not_clobber_healthy_owner(monkeypatch):
    fake_wsm = type("W", (), {"broadcast": AsyncMock(), "next_sequence": staticmethod(lambda sid: 1)})()
    monkeypatch.setattr(main, "ws_manager", fake_wsm)
    monkeypatch.setattr(main, "NATIVE_STALL_CHECK_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(main, "NATIVE_NEVER_PRODUCED_TIMEOUT_SECONDS", 0.03)
    session_id = "s-overlap-healthy"
    key = _install_session(monkeypatch, session_id)

    warnings: list[tuple[str, dict]] = []
    orig_warning = main.logger.warning

    def fake_warning(event, **kw):
        warnings.append((event, kw))
        try:
            return orig_warning(event, **kw)
        except Exception:
            pass

    monkeypatch.setattr(main.logger, "warning", fake_warning)

    mic_header = {
        "session_id": session_id,
        "source": "microphone",
        "sequence": 1,
        "first_sample": 0,
        "captured_at_ms": 0,
        "sample_rate": 16000,
        "channel_count": 1,
        "duration_ms": 50,
    }
    captured: dict = {}

    async def pause_past_timeout_in_b():
        await asyncio.sleep(0.1)

    async def run_companion_b():
        ws_b = _MidStreamWebSocket(
            [
                {"text": json.dumps({"type": "hello", "sources": ["microphone"]})},
                {"text": json.dumps({"type": "ping"})},
            ],
            on_second_receive=pause_past_timeout_in_b,
            headers={"sec-websocket-protocol": f"tars-stream, {key}"},
        )
        await main.native_stream_endpoint(ws_b, session_id)
        captured["mid_b_msgs"] = list(_health_msgs(fake_wsm))

    with patch("backend.main.StreamManager") as mock_sm_cls:
        mock_sm_cls.return_value = AsyncMock()
        ws_a = _MidStreamWebSocket(
            [{"bytes": _encode_native_packet(mic_header)}],
            on_second_receive=run_companion_b,
            headers={"sec-websocket-protocol": f"tars-stream, {key}"},
        )
        asyncio.run(main.native_stream_endpoint(ws_a, session_id))

    mid_b_msgs = captured["mid_b_msgs"]
    assert mid_b_msgs, "expected health broadcasts during test"

    for m in mid_b_msgs:
        if m.payload["sources"]["microphone"] != "unknown":
            assert m.payload["sources"]["microphone"] == "healthy"
            assert m.payload.get("message") is None

    never_prod_logs = [
        kw for e, kw in warnings
        if e == "native_source_never_produced_frames"
        and kw.get("session_id") == session_id
        and kw.get("source") == "microphone"
    ]
    assert len(never_prod_logs) == 0

    last_after_b = mid_b_msgs[-1]
    assert last_after_b.payload["sources"]["microphone"] == "healthy"
    assert last_after_b.payload["physical_capture"] == "active"
    assert last_after_b.payload.get("message") is None
