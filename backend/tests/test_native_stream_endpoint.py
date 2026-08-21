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

