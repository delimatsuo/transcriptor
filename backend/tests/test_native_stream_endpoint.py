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


def _encode_native_packet(header: dict, payload: bytes = b"\x00\x00" * 800) -> bytes:
    header_bytes = json.dumps(header).encode("utf-8")
    header_len = len(header_bytes).to_bytes(4, byteorder="big")
    return header_len + header_bytes + payload


class FakeNativeWebSocket:
    def __init__(self, incoming_messages: list[dict]):
        self.incoming = list(incoming_messages)
        self.sent_json: list[dict] = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def receive(self):
        if not self.incoming:
            return {"type": "websocket.disconnect", "code": 1000}
        msg = dict(self.incoming.pop(0))
        if "type" not in msg:
            msg["type"] = "websocket.receive"
        return msg

    async def send_json(self, data):
        self.sent_json.append(data)


def test_native_stream_endpoint_handles_ping():
    ws = FakeNativeWebSocket([
        {"text": json.dumps({"type": "ping"})}
    ])
    asyncio.run(main.native_stream_endpoint(ws, "test-sess-ping"))
    assert ws.accepted is True
    assert ws.sent_json == [{"type": "pong"}]


@patch("backend.main.StreamManager")
def test_native_stream_endpoint_routes_microphone_and_system_audio(mock_sm_cls):
    mock_sm_instance = AsyncMock()
    mock_sm_cls.return_value = mock_sm_instance

    mic_packet = _encode_native_packet({"source": "microphone", "sequence": 1})
    sys_packet = _encode_native_packet({"source": "system_audio", "sequence": 1})

    ws = FakeNativeWebSocket([
        {"bytes": mic_packet},
        {"bytes": sys_packet},
    ])

    asyncio.run(main.native_stream_endpoint(ws, "test-sess-dual"))

    assert ws.accepted is True
    # Verify StreamManager instances were created and audio was forwarded
    assert mock_sm_cls.call_count == 2
    assert mock_sm_instance.start.call_count == 2
    assert mock_sm_instance.send_audio.call_count == 2
    assert mock_sm_instance.stop.call_count == 2


def test_native_stream_endpoint_handles_short_packets_gracefully():
    ws = FakeNativeWebSocket([
        {"bytes": b"\x00\x01"},
        {"bytes": b"\x00\x00\x00\x05hello"},
        {"text": json.dumps({"type": "ping"})},
    ])

    asyncio.run(main.native_stream_endpoint(ws, "test-sess-malformed"))
    assert ws.accepted is True
    assert ws.sent_json == [{"type": "pong"}]


def test_native_stream_endpoint_handles_gap_messages():
    ws = FakeNativeWebSocket([
        {"text": json.dumps({"type": "gap", "source": "system_audio", "reason": "overrun", "first_sample": 16000})},
        {"text": json.dumps({"type": "ping"})},
    ])

    asyncio.run(main.native_stream_endpoint(ws, "test-sess-gap"))
    assert ws.accepted is True
    assert ws.sent_json == [{"type": "pong"}]


def test_native_stream_endpoint_handles_corrupt_json_header():
    # Length is 4 bytes indicating 10 bytes header, but header is invalid JSON
    bad_header = b"\x00\x00\x00\x0a{invalid:}" + (b"\x00\x00" * 800)
    ws = FakeNativeWebSocket([
        {"bytes": bad_header},
        {"text": json.dumps({"type": "ping"})},
    ])

    asyncio.run(main.native_stream_endpoint(ws, "test-sess-bad-json"))
    assert ws.accepted is True
    assert ws.sent_json == [{"type": "pong"}]


@patch("backend.main.StreamManager")
def test_native_stream_endpoint_testclient_e2e(mock_sm_cls):
    from starlette.testclient import TestClient
    mock_sm_instance = AsyncMock()
    mock_sm_cls.return_value = mock_sm_instance

    client = TestClient(main.app)
    with client.websocket_connect("/api/stream/native/test-sess-client-e2e") as ws:
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

    # After websocket context exits (disconnect), verify StreamManagers were started and stopped
    assert mock_sm_cls.call_count == 2
    assert mock_sm_instance.start.call_count == 2
    assert mock_sm_instance.send_audio.call_count == 2
    assert mock_sm_instance.stop.call_count == 2

