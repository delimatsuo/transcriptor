from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.responses import PlainTextResponse
from starlette.websockets import WebSocketDisconnect

from backend import main
from backend.auth import AuthContext
from backend.config import Settings, select_cors_allowed_origins
from backend.auth_runtime import AuthRuntimeGate
from backend.schemas.models import SessionMode, SessionStatus, TranscriptSegment
from backend.sessions.manager import SessionManager
from backend.ws.handler import ConnectionAdmissionLost, WSConnectionManager


ADMITTED = ",".join(
    [
        "task08-recruiter@ellaexecutivesearch.com",
        "task08-operator@ellaexecutivesearch.com",
        "task08-auditor@ellaexecutivesearch.com",
        "task08-reviewer@ellaexecutivesearch.com",
        "task08-backup@ellaexecutivesearch.com",
    ]
)
OPERATOR_EMAIL = "task08-operator@ellaexecutivesearch.com"


def iap_settings(**overrides) -> Settings:
    values = {
        "google_cloud_project": "synthetic-project",
        "auth_allowed_emails": ADMITTED,
        "auth_mode": "iap",
        "auth_iap_audience": "/projects/123/locations/us-central1/services/tars-api",
        "auth_iap_frontend_origin": "https://tars.ellaexecutivesearch.com",
        "auth_task08_operator_emails": OPERATOR_EMAIL,
    }
    values.update(overrides)
    return Settings(**values)


def test_runtime_gate_latch_revocation_and_idempotent_leases():
    gate = AuthRuntimeGate()
    lease = gate.register_connection("u1", 10)
    assert lease is not None
    assert gate.counts()["registered_browser_connections"] == 1
    assert gate.revoke_principal("u1", 10) is True
    assert lease.closed is True
    assert gate.revoke_principal("u1", 10) is False
    assert gate.register_connection("u1", 10) is None
    assert gate.kill() is True
    assert gate.kill() is False
    assert gate.kill_latched is True
    assert gate.register_connection("u2", 11) is None


def test_runtime_gate_ticket_and_stream_key_counts_are_revocable():
    gate = AuthRuntimeGate()
    assert gate.register_ticket("t1", "u1", 1)
    assert gate.register_stream_key("s1", "u1")
    assert gate.counts()["outstanding_browser_tickets"] == 1
    assert gate.counts()["active_stream_keys"] == 1
    gate.revoke_principal("u1", 1)
    assert gate.counts()["outstanding_browser_tickets"] == 0
    assert gate.counts()["active_stream_keys"] == 1
    gate.revoke_stream_keys("u1")
    assert gate.counts()["active_stream_keys"] == 0


def test_iap_middleware_rejects_unsigned_headers_and_firebase_bearer_before_route(monkeypatch):
    async def endpoint(_request):
        raise AssertionError("route code must not run")

    monkeypatch.setattr(main, "settings", iap_settings())
    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("no signed claim")))

    def request(headers):
        encoded = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
        return main.Request({
            "type": "http", "method": "GET", "path": "/api/me",
            "raw_path": b"/api/me", "query_string": b"", "headers": encoded,
            "client": ("test", 1), "server": ("test", 80), "scheme": "https",
        })

    for headers in [
        {"x-goog-authenticated-user-email": OPERATOR_EMAIL},
        {"authorization": "Bearer firebase-token"},
    ]:
        response = asyncio.run(main.authenticate_api_requests(request(headers), endpoint))
        assert response.status_code == 401


def test_iap_middleware_admits_only_signed_principal_and_cors_preflight_is_exception(monkeypatch):
    user = AuthContext("u1", OPERATOR_EMAIL, "ella-internal", auth_time=22)
    monkeypatch.setattr(main, "settings", iap_settings())
    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: user)
    main.auth_runtime.reset_for_tests()

    async def endpoint(_request):
        return PlainTextResponse("route-ran")

    def request(method="GET", headers=None):
        encoded = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
        return main.Request({
            "type": "http", "method": method, "path": "/api/me", "raw_path": b"/api/me",
            "query_string": b"", "headers": encoded, "client": ("test", 1),
            "server": ("test", 80), "scheme": "https",
        })

    response = asyncio.run(main.authenticate_api_requests(request(), endpoint))
    assert response.status_code == 200
    assert main.current_auth() is None
    preflight = asyncio.run(main.authenticate_api_requests(request("OPTIONS", {"origin": "https://tars.ellaexecutivesearch.com", "access-control-request-method": "GET"}), endpoint))
    assert preflight.status_code == 200


@pytest.mark.parametrize(
    "raw",
    [
        "*",
        "https://tars.ellaexecutivesearch.com,https://evil.example",
        "https://tars.ellaexecutivesearch.com,https://tars.ellaexecutivesearch.com/other",
    ],
)
def test_iap_cors_selection_rejects_wildcards_and_additional_origins(raw):
    with pytest.raises(ValueError):
        select_cors_allowed_origins("iap", raw)
    assert select_cors_allowed_origins("iap", None) == [
        "https://tars.ellaexecutivesearch.com"
    ]
    assert select_cors_allowed_origins("firebase", "http://localhost:3000") == [
        "http://localhost:3000"
    ]


def test_iap_lifespan_probes_adc_before_resource_constructors_without_firebase_init(monkeypatch):
    events: list[str] = []
    test_settings = iap_settings()
    previous_globals = {
        name: getattr(main, name)
        for name in (
            "settings",
            "session_mgr",
            "firestore_storage",
            "gcs_storage",
            "gemini_client",
            "context_window",
        )
    }
    previous_context_windows = dict(main.context_windows)
    previous_ready = main.app.state.ready

    async def probe():
        events.append("adc-probe")

    class FakeSessionManager:
        def __init__(self, _settings):
            events.append("session-manager")

        def detect_orphaned_sessions(self):
            return []

    class FakeResource:
        def __init__(self, _settings):
            events.append(self.__class__.__name__)

    class FakeFirestore(FakeResource):
        pass

    class FakeGcs(FakeResource):
        pass

    class FakeGemini(FakeResource):
        pass

    monkeypatch.setattr(main, "get_settings", lambda: test_settings)
    monkeypatch.setattr(main, "probe_application_default_credentials", probe)
    monkeypatch.setattr(main, "initialize_firebase_admin", lambda *_: events.append("firebase-init"))
    monkeypatch.setattr(main, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(main, "FirestoreStorage", FakeFirestore)
    monkeypatch.setattr(main, "GCSStorage", FakeGcs)
    monkeypatch.setattr(main, "GeminiClient", FakeGemini)
    monkeypatch.setattr(main, "_stop_pipeline", AsyncMock())

    async def exercise():
        async with main.lifespan(main.app):
            assert main.app.state.ready is True

    try:
        asyncio.run(exercise())
        assert events[:4] == ["adc-probe", "session-manager", "FakeFirestore", "FakeGcs"]
        assert events[4] == "FakeGemini"
        assert "firebase-init" not in events
    finally:
        for name, value in previous_globals.items():
            setattr(main, name, value)
        main.context_windows.clear()
        main.context_windows.update(previous_context_windows)
        main.app.state.ready = previous_ready


def test_browser_ws_verifies_iap_before_ticket_pop_and_registers_then_releases_lease(monkeypatch):
    user = AuthContext("u1", OPERATOR_EMAIL, "ella-internal", auth_time=22)
    settings = iap_settings()
    monkeypatch.setattr(main, "settings", settings)
    main.auth_runtime.reset_for_tests()
    ticket = "one-time-browser-ticket"
    main.ws_tickets.clear()
    main.ws_tickets[ticket] = (
        user,
        "s1",
        datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    order: list[str] = []

    class Socket:
        headers = {
            "x-goog-iap-jwt-assertion": "synthetic-assertion",
            "sec-websocket-protocol": f"tars-ticket, {ticket}",
        }
        query_params = {}

        async def close(self, **_kwargs):
            order.append("close")

        async def receive_json(self):
            raise WebSocketDisconnect(code=1000)

    async def read_session(_session_id):
        order.append("session-read")
        return SimpleNamespace(owner_id="u1", org_id="ella-internal")

    async def connect(*_args, **_kwargs):
        order.append("accept-and-replay")

    async def close_expiry(*_args, **_kwargs):
        order.append("expiry-task")

    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: user)
    monkeypatch.setattr(main, "_read_session", read_session)
    monkeypatch.setattr(main.ws_manager, "connect", connect)
    monkeypatch.setattr(main.ws_manager, "disconnect", lambda *_args: None)
    monkeypatch.setattr(main, "_close_ws_at_expiry", close_expiry)
    asyncio.run(main.websocket_endpoint(Socket(), "s1"))
    assert order[:2] == ["session-read", "accept-and-replay"]
    assert ticket not in main.ws_tickets
    assert main.auth_runtime.live_connection_count == 0


def test_invalid_iap_browser_socket_leaves_ticket_untouched_and_reads_no_session(monkeypatch):
    settings = iap_settings()
    monkeypatch.setattr(main, "settings", settings)
    main.auth_runtime.reset_for_tests()
    ticket = "invalid-iap-ticket"
    user = AuthContext("u1", OPERATOR_EMAIL, "ella-internal", auth_time=22)
    main.ws_tickets.clear()
    main.ws_tickets[ticket] = (user, "s1", datetime.now(timezone.utc) + timedelta(seconds=60))
    reads: list[str] = []
    accepts: list[str] = []

    class Socket:
        headers = {
            "x-goog-iap-jwt-assertion": "bad-assertion",
            "sec-websocket-protocol": f"tars-ticket, {ticket}",
        }
        query_params = {}

        async def close(self, **_kwargs):
            pass

    async def read_session(_session_id):
        reads.append("session-read")
        return SimpleNamespace(owner_id="u1", org_id="ella-internal")

    async def connect(*_args, **_kwargs):
        accepts.append("accept")

    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad")))
    monkeypatch.setattr(main, "_read_session", read_session)
    monkeypatch.setattr(main.ws_manager, "connect", connect)
    asyncio.run(main.websocket_endpoint(Socket(), "s1"))
    assert ticket in main.ws_tickets
    assert reads == []
    assert accepts == []


@pytest.mark.parametrize(
    "ticket_session, ticket_uid",
    [("s1", "ticket"), ("s2", "signed")],
)
def test_iap_browser_socket_rejects_ticket_principal_or_session_mismatch(monkeypatch, ticket_session, ticket_uid):
    settings = iap_settings()
    monkeypatch.setattr(main, "settings", settings)
    main.auth_runtime.reset_for_tests()
    signed_user = AuthContext("signed", OPERATOR_EMAIL, "ella-internal", auth_time=22)
    ticket_user = AuthContext(ticket_uid, OPERATOR_EMAIL, "ella-internal", auth_time=22)
    ticket = "mismatch-ticket"
    main.ws_tickets.clear()
    main.ws_tickets[ticket] = (ticket_user, ticket_session, datetime.now(timezone.utc) + timedelta(seconds=60))
    reads: list[str] = []

    class Socket:
        headers = {
            "x-goog-iap-jwt-assertion": "signed-assertion",
            "sec-websocket-protocol": f"tars-ticket, {ticket}",
        }
        query_params = {}

        async def close(self, **_kwargs):
            pass

    async def read_session(_session_id):
        reads.append("session-read")
        return SimpleNamespace(owner_id="signed", org_id="ella-internal")

    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: signed_user)
    monkeypatch.setattr(main, "_read_session", read_session)
    asyncio.run(main.websocket_endpoint(Socket(), "s1"))
    assert ticket in main.ws_tickets
    assert reads == []


def test_iap_socket_expiry_uses_injected_clock_and_closes_at_3300_not_3299():
    class Socket:
        closed: list[dict] = []

        async def close(self, **kwargs):
            self.closed.append(kwargs)

    async def exercise():
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        current = [base + timedelta(seconds=3299)]
        release = asyncio.Event()
        activity_observed = asyncio.Event()
        sleeps: list[float] = []
        ping_count = [0]

        async def controlled_sleep(delay: float):
            sleeps.append(delay)
            for _ in range(3):
                ping_count[0] += 1
                await asyncio.sleep(0)
            activity_observed.set()
            await release.wait()

        socket = Socket()
        task = asyncio.create_task(
            main._close_ws_at_expiry(
                socket,
                base + timedelta(seconds=3300),
                sleep=controlled_sleep,
                clock=lambda: current[0],
            )
        )
        await activity_observed.wait()
        assert sleeps == [1.0]
        assert ping_count == [3]
        assert socket.closed == []
        # Advancing from 3299 to 3300 releases the absolute-deadline sleeper;
        # there is no activity path that can replace or extend this deadline.
        current[0] = base + timedelta(seconds=3300)
        release.set()
        await task
        assert socket.closed == [{"code": 4001, "reason": "auth_expired"}]

    asyncio.run(exercise())


def test_iap_logout_revokes_lease_tickets_stream_keys_and_auth_time_before_return(monkeypatch):
    settings = iap_settings()
    user = AuthContext("logout-user", OPERATOR_EMAIL, "ella-internal", auth_time=22)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "current_auth", lambda: user)
    monkeypatch.setattr(main, "auth_is_enforced", lambda: True)
    main.auth_runtime.reset_for_tests()
    main.ws_tickets.clear()
    main.stream_tickets.clear()
    main.stream_keys.clear()
    main.stream_key_owners.clear()
    lease = main.auth_runtime.register_connection(user.uid, user.auth_time)
    assert lease is not None
    main.auth_runtime.register_ticket("runtime-ws", user.uid, user.auth_time)
    main.auth_runtime.register_ticket("runtime-stream", user.uid, user.auth_time)
    main.auth_runtime.register_stream_key("s1", user.uid)
    main.ws_tickets["ws"] = (user, "s1", datetime.now(timezone.utc) + timedelta(seconds=60))
    main.stream_tickets["stream"] = (user, "s1", datetime.now(timezone.utc) + timedelta(seconds=60))
    main.stream_keys["s1"] = "opaque"
    main.stream_key_owners["s1"] = user.uid

    response = asyncio.run(main.auth_logout())
    assert response.status_code == 204
    assert lease.closed is True
    assert main.ws_tickets == {}
    assert main.stream_tickets == {}
    assert main.stream_keys == {}
    assert main.stream_key_owners == {}
    assert main.auth_runtime.admit_principal(user.uid, user.auth_time) is False


def test_expired_abandoned_iap_tickets_are_pruned_from_store_and_runtime_counts(monkeypatch):
    user = AuthContext("expired-ticket-user", OPERATOR_EMAIL, "ella-internal", auth_time=23)
    monkeypatch.setattr(main, "settings", iap_settings())
    main.auth_runtime.reset_for_tests()
    main.ws_tickets.clear()
    main.stream_tickets.clear()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    main.ws_tickets["expired-ws"] = (user, "s1", now)
    main.stream_tickets["expired-stream"] = (user, "s1", now)
    assert main.auth_runtime.register_ticket("expired-ws", user.uid, user.auth_time)
    assert main.auth_runtime.register_ticket("expired-stream", user.uid, user.auth_time)

    assert main._prune_expired_iap_tickets(now + timedelta(microseconds=1)) == 2
    assert main.ws_tickets == {}
    assert main.stream_tickets == {}
    assert main.auth_runtime.counts()["outstanding_browser_tickets"] == 0
    monkeypatch.setattr(main, "session_mgr", SimpleNamespace(_sessions={}))
    monkeypatch.setattr(main, "current_auth", lambda: user)
    monkeypatch.setattr(main, "auth_is_enforced", lambda: True)
    readiness = asyncio.run(main.task08_transition_readiness())
    assert readiness["outstanding_browser_tickets"] == 0


def test_iap_kill_latches_before_clearing_all_capabilities_and_is_idempotent(monkeypatch):
    settings = iap_settings()
    user = AuthContext("kill-user", OPERATOR_EMAIL, "ella-internal", auth_time=22)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "current_auth", lambda: user)
    monkeypatch.setattr(main, "auth_is_enforced", lambda: True)
    main.auth_runtime.reset_for_tests()
    lease = main.auth_runtime.register_connection(user.uid, user.auth_time)
    assert lease is not None
    main.auth_runtime.register_ticket("runtime-ticket", user.uid, user.auth_time)
    main.auth_runtime.register_stream_key("s1", user.uid)
    events: list[str] = []

    class OrderedStore(dict):
        def clear(self):
            assert main.auth_runtime.kill_latched
            events.append("clear")
            super().clear()

    monkeypatch.setattr(main, "ws_tickets", OrderedStore({"ws": (user, "s1", datetime.now(timezone.utc))}))
    monkeypatch.setattr(main, "stream_tickets", OrderedStore({"stream": (user, "s1", datetime.now(timezone.utc))}))
    monkeypatch.setattr(main, "stream_keys", OrderedStore({"s1": "opaque"}))
    monkeypatch.setattr(main, "stream_key_owners", OrderedStore({"s1": user.uid}))

    first = asyncio.run(main.task08_kill_switch())
    second = asyncio.run(main.task08_kill_switch())
    assert first["kill_switch_active"] is True
    assert second["kill_switch_active"] is True
    assert main.auth_runtime.kill_latched is True
    assert lease.closed is True
    assert main.auth_runtime.admit_principal(user.uid, user.auth_time) is False
    assert main.auth_runtime.register_connection(user.uid, user.auth_time) is None
    assert first["ready"] is False
    assert main.ws_tickets == {}
    assert main.stream_tickets == {}
    assert main.stream_keys == {}
    assert main.stream_key_owners == {}
    assert len(events) == 8


def test_kill_switch_stops_active_native_manager_and_clears_business_state(monkeypatch):
    settings = iap_settings()
    user = AuthContext("kill-active", OPERATOR_EMAIL, "ella-internal", auth_time=24)
    manager = SessionManager(settings)
    session = manager.create_session(owner_id=user.uid, org_id=user.org_id)
    stopped: list[str] = []

    class ActiveStreamManager:
        drain_completed = True

        async def stop(self):
            stopped.append("stop")

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    monkeypatch.setattr(main, "firestore_storage", None)
    monkeypatch.setattr(main, "current_auth", lambda: user)
    monkeypatch.setattr(main, "auth_is_enforced", lambda: True)
    main.auth_runtime.reset_for_tests()
    main.stream_keys.clear()
    main.stream_key_owners.clear()
    main.native_stream_managers.clear()
    main.stream_managers.clear()
    main.context_windows.clear()
    main.stop_capabilities.clear()
    main.stream_keys[session.id] = "key"
    main.stream_key_owners[session.id] = user.uid
    assert main.auth_runtime.register_stream_key(session.id, user.uid)
    main.native_stream_managers[session.id] = {"candidate": ActiveStreamManager()}
    main.stop_capabilities["cap"] = (user, session.id, datetime.now(timezone.utc) + timedelta(seconds=60))

    result = asyncio.run(main.task08_kill_switch())
    assert result["kill_switch_active"] is True
    assert stopped == ["stop"]
    assert session.status == SessionStatus.INCOMPLETE
    assert manager.count_active_sessions() == 0
    assert session.id not in main.native_stream_managers
    assert session.id not in main.stream_keys
    assert session.id not in main.context_windows
    assert session.id not in main.stop_capabilities
    assert result["active_business_sessions"] == 0


def test_native_manager_start_losing_kill_admission_is_stopped_and_never_published(monkeypatch):
    settings = iap_settings()
    user = AuthContext("native-race", OPERATOR_EMAIL, "ella-internal", auth_time=25)
    manager = SessionManager(settings)
    session = manager.create_session(owner_id=user.uid, org_id=user.org_id)
    ticket = "native-race-ticket"
    entered = asyncio.Event()
    release = asyncio.Event()
    stopped: list[str] = []
    sent: list[bytes] = []

    class SuspendedStreamManager:
        drain_completed = True

        def __init__(self, **_kwargs):
            pass

        async def start(self):
            entered.set()
            await release.wait()

        async def stop(self):
            stopped.append("stop")

        async def send_audio(self, payload):
            sent.append(payload)

    class Socket:
        headers = {
            "x-goog-iap-jwt-assertion": "signed",
            "sec-websocket-protocol": f"tars-stream, {ticket}",
        }

        def __init__(self):
            self.receives = 0

        async def accept(self, **_kwargs):
            return None

        async def close(self, **_kwargs):
            return None

        async def send_json(self, _payload):
            return None

        async def receive(self):
            self.receives += 1
            if self.receives == 1:
                header = {
                    "session_id": session.id,
                    "source": "microphone",
                    "sequence": 1,
                }
                body = json.dumps(header).encode()
                return {"bytes": len(body).to_bytes(4, "big") + body + b"audio"}
            return {"type": "websocket.disconnect"}

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: user)
    monkeypatch.setattr(main, "ws_manager", SimpleNamespace(
        next_sequence=lambda _sid: 1,
        broadcast=AsyncMock(),
    ))
    monkeypatch.setattr(main, "StreamManager", SuspendedStreamManager)
    main.auth_runtime.reset_for_tests()
    main.stream_tickets.clear()
    main.stream_keys.clear()
    main.native_stream_managers.clear()
    main.stream_managers.clear()
    main.stream_tickets[ticket] = (
        user,
        session.id,
        datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    main.stream_keys[session.id] = "stream-key"
    assert main.auth_runtime.register_ticket(ticket, user.uid, user.auth_time)
    assert main.auth_runtime.register_stream_key(session.id, user.uid)
    socket = Socket()

    async def exercise():
        task = asyncio.create_task(main.native_stream_endpoint(socket, session.id))
        await asyncio.wait_for(entered.wait(), timeout=1)
        main.auth_runtime.kill()
        release.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(exercise())
    assert stopped == ["stop"]
    assert sent == []
    assert main.native_stream_managers.get(session.id) in (None, {})
    main.stream_tickets.clear()
    main.stream_keys.clear()


def test_concurrent_native_manager_start_reuses_published_manager(monkeypatch):
    settings = iap_settings()
    user = AuthContext("native-duplicate", OPERATOR_EMAIL, "ella-internal", auth_time=26)
    manager = SessionManager(settings)
    session = manager.create_session(owner_id=user.uid, org_id=user.org_id)
    start_count = 0
    starts_ready = asyncio.Event()
    release_starts = asyncio.Event()
    created: list[object] = []

    class ConcurrentStreamManager:
        drain_completed = True

        def __init__(self, **_kwargs):
            nonlocal start_count
            self.abort_calls = 0
            self.sent_payloads: list[bytes] = []
            created.append(self)

        async def start(self):
            nonlocal start_count
            start_count += 1
            if start_count == 2:
                starts_ready.set()
            await release_starts.wait()

        async def abort_emergency(self):
            self.abort_calls += 1

        async def stop(self):
            raise AssertionError("duplicate loser must use emergency abort")

        async def send_audio(self, payload):
            self.sent_payloads.append(payload)

    class Socket:
        def __init__(self, ticket, sequence):
            self.headers = {
                "x-goog-iap-jwt-assertion": "signed",
                "sec-websocket-protocol": f"tars-stream, {ticket}",
            }
            self.sequence = sequence
            self.receives = 0

        async def accept(self, **_kwargs):
            return None

        async def close(self, **_kwargs):
            return None

        async def send_json(self, _payload):
            return None

        async def receive(self):
            self.receives += 1
            if self.receives == 1:
                header = {
                    "session_id": session.id,
                    "source": "microphone",
                    "sequence": self.sequence,
                }
                body = json.dumps(header).encode()
                payload = f"audio-{self.sequence}".encode()
                return {"bytes": len(body).to_bytes(4, "big") + body + payload}
            return {"type": "websocket.disconnect"}

    tickets = ["native-duplicate-a", "native-duplicate-b"]
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: user)
    monkeypatch.setattr(main, "StreamManager", ConcurrentStreamManager)
    monkeypatch.setattr(
        main,
        "ws_manager",
        SimpleNamespace(next_sequence=lambda _sid: 1, broadcast=AsyncMock()),
    )
    main.auth_runtime.reset_for_tests()
    main.stream_tickets.clear()
    main.stream_keys.clear()
    main.native_stream_managers.clear()
    main.stream_managers.clear()
    for ticket in tickets:
        main.stream_tickets[ticket] = (
            user,
            session.id,
            datetime.now(timezone.utc) + timedelta(seconds=60),
        )
        assert main.auth_runtime.register_ticket(ticket, user.uid, user.auth_time)
    main.stream_keys[session.id] = "stream-key"
    assert main.auth_runtime.register_stream_key(session.id, user.uid)

    async def exercise():
        tasks = [
            asyncio.create_task(
                main.native_stream_endpoint(Socket(ticket, index + 1), session.id)
            )
            for index, ticket in enumerate(tickets)
        ]
        await asyncio.wait_for(starts_ready.wait(), timeout=1)
        release_starts.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

    asyncio.run(exercise())
    published = main.native_stream_managers.get(session.id, {})
    assert list(published.values()) == [created[0]]
    assert len(published) == 1
    assert created[0].sent_payloads == [b"audio-1", b"audio-2"]
    assert created[1].sent_payloads == []
    assert created[0].abort_calls == 0
    assert created[1].abort_calls == 1
    assert main.stream_managers[session.id] == [created[0]]
    main.native_stream_managers.clear()
    main.stream_managers.clear()
    main.stream_tickets.clear()
    main.stream_keys.clear()
    main.stream_key_owners.clear()
    main.auth_runtime.reset_for_tests()


def test_native_desktop_socket_without_signed_iap_session_is_denied_before_session_read(monkeypatch):
    monkeypatch.setattr(main, "settings", iap_settings())
    main.auth_runtime.reset_for_tests()
    calls = []
    monkeypatch.setattr(
        main,
        "session_mgr",
        SimpleNamespace(get_session=lambda _sid: calls.append("session-read")),
    )
    main.stream_keys["s1"] = "native-key"

    class Socket:
        headers = {"sec-websocket-protocol": "tars-stream, native-key"}

        async def close(self, **_kwargs):
            calls.append("close")

    asyncio.run(main.native_stream_endpoint(Socket(), "s1"))
    assert calls == ["close"]
    assert main.auth_runtime.live_connection_count == 0
    main.stream_keys.pop("s1", None)


@pytest.mark.parametrize(
    "ticket_session, ticket_uid",
    [("s1", "ticket"), ("s2", "signed")],
)
def test_iap_native_audio_rejects_ticket_principal_or_session_before_session_read(
    monkeypatch, ticket_session, ticket_uid
):
    settings = iap_settings()
    monkeypatch.setattr(main, "settings", settings)
    main.auth_runtime.reset_for_tests()
    signed_user = AuthContext("signed", OPERATOR_EMAIL, "ella-internal", auth_time=22)
    ticket_user = AuthContext(ticket_uid, OPERATOR_EMAIL, "ella-internal", auth_time=22)
    ticket = "native-mismatch-ticket"
    main.stream_tickets.clear()
    main.stream_tickets[ticket] = (
        ticket_user,
        ticket_session,
        datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    reads: list[str] = []
    closes: list[str] = []

    class Socket:
        headers = {
            "x-goog-iap-jwt-assertion": "signed-assertion",
            "sec-websocket-protocol": f"tars-stream, {ticket}",
        }

        async def close(self, **_kwargs):
            closes.append("close")

    class FakeSessionManager:
        def get_session(self, _session_id):
            reads.append("session-read")
            return SimpleNamespace(owner_id="signed", org_id="ella-internal", status=SessionStatus.ACTIVE)

    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: signed_user)
    monkeypatch.setattr(main, "session_mgr", FakeSessionManager())
    asyncio.run(main.native_stream_endpoint(Socket(), "s1"))
    assert closes == ["close"]
    assert reads == []
    assert ticket in main.stream_tickets
    main.stream_tickets.clear()


def test_transition_readiness_is_count_only_and_kill_switch_latches(monkeypatch):
    user = AuthContext("u1", OPERATOR_EMAIL, "ella-internal", auth_time=22)
    monkeypatch.setattr(main, "settings", iap_settings())
    monkeypatch.setattr(main, "session_mgr", SimpleNamespace(_sessions={
        "s1": SimpleNamespace(status=SessionStatus.ACTIVE),
    }))
    monkeypatch.setattr(main, "current_auth", lambda: user)
    monkeypatch.setattr(main, "auth_is_enforced", lambda: True)
    main.auth_runtime.reset_for_tests()
    main.ws_tickets.clear()
    main.stream_tickets.clear()
    main.stream_keys.clear()
    main.stream_key_owners.clear()
    main.ws_tickets["opaque"] = (
        user,
        "s1",
        datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    before = asyncio.run(main.task08_transition_readiness())
    assert before["active_business_sessions"] == 1
    assert before["outstanding_browser_tickets"] == 1
    assert set(before).issuperset({"ready", "kill_switch_active"})
    killed = asyncio.run(main.task08_kill_switch())
    assert killed["kill_switch_active"] is True
    assert killed["ready"] is False
    assert main.auth_runtime.kill_latched is True
    assert main.ws_tickets == {}


def test_kill_latch_keeps_only_fresh_operator_controls_reachable(monkeypatch):
    operator = AuthContext("operator", OPERATOR_EMAIL, "ella-internal", auth_time=22)
    nonoperator = AuthContext(
        "recruiter", "task08-recruiter@ellaexecutivesearch.com", "ella-internal", auth_time=22
    )
    monkeypatch.setattr(main, "settings", iap_settings())
    monkeypatch.setattr(main, "session_mgr", SimpleNamespace(_sessions={}))
    monkeypatch.setattr(main, "auth_is_enforced", lambda: True)
    main.auth_runtime.reset_for_tests()

    def request(method: str, path: str):
        return main.Request({
            "type": "http", "method": method, "path": path,
            "raw_path": path.encode(), "query_string": b"",
            "headers": [(b"x-goog-iap-jwt-assertion", b"signed")],
            "client": ("test", 1), "server": ("test", 80), "scheme": "https",
        })

    async def kill_route(_request):
        return await main.task08_kill_switch()

    async def readiness_route(_request):
        return await main.task08_transition_readiness()

    async def business_route(_request):
        return PlainTextResponse("business")

    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: operator)
    asyncio.run(main.authenticate_api_requests(
        request("POST", "/api/admin/task08/kill-switch"), kill_route
    ))
    assert main.auth_runtime.kill_latched is True
    # Repeated control requests remain reachable after the global latch.
    readiness = asyncio.run(main.authenticate_api_requests(
        request("GET", "/api/admin/task08/transition-readiness"), readiness_route
    ))
    assert readiness["kill_switch_active"] is True

    # A signed non-operator and an ordinary business request remain denied.
    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: nonoperator)
    denied = asyncio.run(main.authenticate_api_requests(
        request("GET", "/api/me"), business_route
    ))
    assert denied.status_code == 401


def test_revoked_operator_cannot_use_post_kill_exception_but_current_operator_can(monkeypatch):
    operator_a = AuthContext("operator-a", OPERATOR_EMAIL, "ella-internal", auth_time=10)
    operator_b = AuthContext("operator-b", OPERATOR_EMAIL, "ella-internal", auth_time=20)
    monkeypatch.setattr(main, "settings", iap_settings())
    monkeypatch.setattr(main, "session_mgr", SimpleNamespace(_sessions={}))
    monkeypatch.setattr(main, "auth_is_enforced", lambda: True)
    main.auth_runtime.reset_for_tests()

    def request(method: str, path: str):
        return main.Request({
            "type": "http", "method": method, "path": path,
            "raw_path": path.encode(), "query_string": b"",
            "headers": [(b"x-goog-iap-jwt-assertion", b"signed")],
            "client": ("test", 1), "server": ("test", 80), "scheme": "https",
        })

    async def readiness_route(_request):
        return await main.task08_transition_readiness()

    async def kill_route(_request):
        return await main.task08_kill_switch()

    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: operator_a)
    main.auth_runtime.revoke_principal(operator_a.uid, operator_a.auth_time)
    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: operator_b)
    asyncio.run(main.authenticate_api_requests(
        request("POST", "/api/admin/task08/kill-switch"), kill_route
    ))
    assert main.auth_runtime.kill_latched is True

    # The old, revoked principal cannot use either operator exception after
    # another operator has latched the global kill.
    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: operator_a)
    for method, path, route in [
        ("GET", "/api/admin/task08/transition-readiness", readiness_route),
        ("POST", "/api/admin/task08/kill-switch", kill_route),
    ]:
        denied = asyncio.run(main.authenticate_api_requests(request(method, path), route))
        assert denied.status_code == 401

    # A fresh, non-revoked operator remains able to observe and repeat the
    # idempotent controls after the global latch.
    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: operator_b)
    readiness = asyncio.run(main.authenticate_api_requests(
        request("GET", "/api/admin/task08/transition-readiness"), readiness_route
    ))
    repeated = asyncio.run(main.authenticate_api_requests(
        request("POST", "/api/admin/task08/kill-switch"), kill_route
    ))
    assert readiness["kill_switch_active"] is True
    assert repeated["kill_switch_active"] is True


def test_create_session_kill_during_firestore_save_terminalizes_and_releases_operation(monkeypatch):
    settings = iap_settings()
    user = AuthContext("race-user", OPERATOR_EMAIL, "ella-internal", auth_time=22)
    manager = SessionManager(settings)
    entered = asyncio.Event()
    release = asyncio.Event()
    saves: list[SessionStatus] = []

    class Storage:
        async def save_session(self, session):
            saves.append(session.status)
            if len(saves) == 1:
                entered.set()
                await release.wait()

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    monkeypatch.setattr(main, "firestore_storage", Storage())
    monkeypatch.setattr(main, "gemini_client", object())
    monkeypatch.setattr(main, "current_auth", lambda: user)
    monkeypatch.setattr(main, "auth_is_enforced", lambda: True)
    main.auth_runtime.reset_for_tests()
    main.pipeline_tasks.clear()
    main.stream_keys.clear()
    main.stream_key_owners.clear()
    main.stop_capabilities.clear()
    main.context_windows.clear()

    async def exercise():
        task = asyncio.create_task(main.create_session())
        await entered.wait()
        main.auth_runtime.kill()
        release.set()
        with pytest.raises(Exception) as exc_info:
            await task
        assert getattr(exc_info.value, "status_code", None) == 401

    asyncio.run(exercise())
    sessions = list(manager._sessions.values())
    assert sessions and sessions[0].status == SessionStatus.INCOMPLETE
    assert manager.count_active_sessions() == 0
    assert main.auth_runtime._operation_leases == {}
    assert saves == [SessionStatus.ACTIVE, SessionStatus.INCOMPLETE]
    main.auth_runtime.reset_for_tests()


def test_logout_terminalizes_owned_active_sessions_before_content_free_response(monkeypatch):
    settings = iap_settings()
    user = AuthContext("logout-active", OPERATOR_EMAIL, "ella-internal", auth_time=33)
    manager = SessionManager(settings)
    persisted: list[SessionStatus] = []

    class Storage:
        async def save_session(self, session):
            persisted.append(session.status)

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    monkeypatch.setattr(main, "firestore_storage", Storage())
    monkeypatch.setattr(main, "current_auth", lambda: user)
    monkeypatch.setattr(main, "auth_is_enforced", lambda: True)
    main.auth_runtime.reset_for_tests()
    main.pipeline_tasks.clear()
    main.stream_keys.clear()
    main.stream_key_owners.clear()
    session = manager.create_session(owner_id=user.uid, org_id=user.org_id)

    async def exercise():
        await manager.start_heartbeat(session.id)
        main.pipeline_tasks[session.id] = []
        response = await main.auth_logout()
        assert response.status_code == 204

    asyncio.run(exercise())
    assert session.status == SessionStatus.INCOMPLETE
    assert manager.count_active_sessions() == 0
    assert persisted == [SessionStatus.INCOMPLETE]


def test_logout_is_idempotent_and_persistence_failure_keeps_local_state_terminal(monkeypatch):
    settings = iap_settings()
    user = AuthContext("logout-repeat", OPERATOR_EMAIL, "ella-internal", auth_time=34)
    manager = SessionManager(settings)

    class Storage:
        async def save_session(self, _session):
            raise RuntimeError("synthetic persistence outage")

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    monkeypatch.setattr(main, "firestore_storage", Storage())
    monkeypatch.setattr(main, "current_auth", lambda: user)
    monkeypatch.setattr(main, "auth_is_enforced", lambda: True)
    main.auth_runtime.reset_for_tests()
    main.pipeline_tasks.clear()
    main.stream_keys.clear()
    main.stream_key_owners.clear()
    session = manager.create_session(owner_id=user.uid, org_id=user.org_id)

    async def exercise():
        first = await main.auth_logout()
        second = await main.auth_logout()
        return first, second

    first, second = asyncio.run(exercise())
    assert first.status_code == 204
    assert second.status_code == 204
    assert session.status == SessionStatus.INCOMPLETE
    assert manager.count_active_sessions() == 0


def test_logout_stop_failure_forces_pipeline_and_capability_cleanup(monkeypatch):
    settings = iap_settings()
    user = AuthContext("logout-stop-failure", OPERATOR_EMAIL, "ella-internal", auth_time=35)
    manager = SessionManager(settings)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    monkeypatch.setattr(main, "firestore_storage", None)
    monkeypatch.setattr(main, "current_auth", lambda: user)
    monkeypatch.setattr(main, "auth_is_enforced", lambda: True)
    async def failing_stop(_session_id):
        raise RuntimeError("synthetic stop failure")
    monkeypatch.setattr(main, "_stop_pipeline", failing_stop)
    main.auth_runtime.reset_for_tests()
    main.pipeline_tasks.clear()
    main.stream_keys.clear()
    main.stream_key_owners.clear()
    main.stop_capabilities.clear()
    session = manager.create_session(owner_id=user.uid, org_id=user.org_id)

    async def exercise():
        await manager.start_heartbeat(session.id)
        main.pipeline_tasks[session.id] = []
        response = await main.auth_logout()
        return response

    response = asyncio.run(exercise())
    assert response.status_code == 204
    assert session.status == SessionStatus.INCOMPLETE
    assert manager.count_active_sessions() == 0
    assert session.id not in main.pipeline_tasks
    assert session.id not in manager._heartbeat_tasks


def test_terminal_cleanup_waits_for_cancellation_resistant_session_tasks(monkeypatch):
    session_id = "settlement-session"
    main.rolling_summary_tasks.clear()
    main.interview_suggestion_tasks.clear()
    main.single_source_check_tasks.clear()
    settled = asyncio.Event()

    async def cancellation_resistant_task():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Simulate a task that performs one final cooperative cleanup turn.
            await asyncio.sleep(0)
            settled.set()

    async def exercise():
        task = asyncio.create_task(cancellation_resistant_task())
        main.rolling_summary_tasks[session_id] = task
        await asyncio.sleep(0)
        await main._cleanup_session_context_async(session_id)
        assert settled.is_set()
        assert task.done()
        assert session_id not in main.rolling_summary_tasks

    asyncio.run(exercise())


def test_force_clear_bounds_resistant_pipeline_and_keeps_readiness_until_done():
    session_id = "resistant-pipeline"
    release = asyncio.Event()
    main.active_provider_operations.clear()
    main.pipeline_tasks.clear()
    main.auth_runtime.reset_for_tests()

    async def resistant_pipeline():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            raise

    async def exercise():
        task = asyncio.create_task(resistant_pipeline())
        await asyncio.sleep(0)
        main.pipeline_tasks[session_id] = [task]
        started = time.monotonic()
        await main._force_clear_session_runtime(session_id)
        elapsed = time.monotonic() - started
        assert elapsed < 0.5
        assert not task.done()
        assert main.auth_runtime.counts()["active_provider_operations"] == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

    asyncio.run(exercise())
    assert main.active_provider_operations == {}
    assert main.auth_runtime.counts()["active_provider_operations"] == 0
    main.auth_runtime.reset_for_tests()


def test_iap_stt_registration_rejects_missing_or_boolean_generation_before_side_effect(
    monkeypatch,
):
    settings = iap_settings()
    user = AuthContext("stt-invalid-generation", OPERATOR_EMAIL, "ella-internal", auth_time=84)
    manager = SessionManager(settings)
    session = manager.create_session(owner_id=user.uid, org_id=user.org_id)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    main.active_provider_operations.clear()
    main.session_auth_generations.pop(session.id, None)
    main.auth_runtime.reset_for_tests()
    side_effects: list[str] = []

    async def exercise():
        for generation in (None, (user.uid, True)):
            if generation is None:
                main.session_auth_generations.pop(session.id, None)
            else:
                main.session_auth_generations[session.id] = generation  # type: ignore[assignment]

            async def worker():
                side_effects.append("provider-or-stt")
                await asyncio.Event().wait()

            task = asyncio.create_task(worker())
            assert main._register_stt_task(task, session_id=session.id) is None
            with pytest.raises(asyncio.CancelledError):
                await task
            assert main.active_provider_operations == {}
            assert main.auth_runtime.counts()["active_provider_operations"] == 0

    try:
        asyncio.run(exercise())
        assert side_effects == []
    finally:
        main.session_auth_generations.pop(session.id, None)
        main.auth_runtime.reset_for_tests()


def test_native_accept_failure_releases_lease_without_health_or_watchdog_leak(monkeypatch):
    settings = iap_settings()
    user = AuthContext("native-user", OPERATOR_EMAIL, "ella-internal", auth_time=44)
    session = SimpleNamespace(
        owner_id=user.uid,
        org_id=user.org_id,
        status=SessionStatus.ACTIVE,
    )
    ticket = "native-valid-ticket"
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", SimpleNamespace(get_session=lambda _sid: session))
    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: user)
    main.auth_runtime.reset_for_tests()
    main.stream_tickets.clear()
    main.native_session_health.clear()
    main.stream_tickets[ticket] = (
        user,
        "s1",
        datetime.now(timezone.utc) + timedelta(seconds=60),
    )

    class Socket:
        headers = {
            "x-goog-iap-jwt-assertion": "signed",
            "sec-websocket-protocol": f"tars-stream, {ticket}",
        }

        async def accept(self, **_kwargs):
            raise RuntimeError("synthetic accept failure")

        async def close(self, **_kwargs):
            pass

    with pytest.raises(RuntimeError):
        asyncio.run(main.native_stream_endpoint(Socket(), "s1"))
    assert main.auth_runtime.live_connection_count == 0
    assert main.native_session_health == {}
    main.stream_tickets.clear()


def test_native_stalled_accept_is_fenced_by_revocation_before_health_setup(monkeypatch):
    settings = iap_settings()
    user = AuthContext("native-stalled", OPERATOR_EMAIL, "ella-internal", auth_time=45)
    session = SimpleNamespace(
        owner_id=user.uid,
        org_id=user.org_id,
        status=SessionStatus.ACTIVE,
    )
    ticket = "native-stalled-ticket"
    closed = asyncio.Event()
    accepted = asyncio.Event()
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", SimpleNamespace(get_session=lambda _sid: session))
    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: user)
    main.auth_runtime.reset_for_tests()
    main.stream_tickets.clear()
    main.stream_keys.clear()

    main.native_session_health.clear()
    main.stream_tickets[ticket] = (
        user,
        "s-stalled",
        datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    main.stream_keys["s-stalled"] = "stream-key"

    class Socket:
        headers = {
            "x-goog-iap-jwt-assertion": "signed",
            "sec-websocket-protocol": f"tars-stream, {ticket}",
        }

        async def accept(self, **_kwargs):
            accepted.set()
            await asyncio.Event().wait()

        async def close(self, **_kwargs):
            closed.set()

    async def exercise():
        task = asyncio.create_task(main.native_stream_endpoint(Socket(), "s-stalled"))
        await accepted.wait()
        main.auth_runtime.revoke_principal(user.uid, user.auth_time)
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(exercise())
    assert closed.is_set()
    assert main.auth_runtime.live_connection_count == 0
    assert main.native_session_health == {}
    main.stream_tickets.clear()
    main.stream_keys.clear()


def test_native_health_setup_failure_removes_partial_health_record_and_lease(monkeypatch):
    settings = iap_settings()
    user = AuthContext("native-health-fail", OPERATOR_EMAIL, "ella-internal", auth_time=46)
    session = SimpleNamespace(
        owner_id=user.uid,
        org_id=user.org_id,
        status=SessionStatus.ACTIVE,
    )
    ticket = "native-health-fail-ticket"

    class FailingHealth(dict):
        def setdefault(self, key, default=None):
            if key == "source_connections":
                raise RuntimeError("synthetic health setup failure")
            return super().setdefault(key, default)

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", SimpleNamespace(get_session=lambda _sid: session))
    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: user)
    main.auth_runtime.reset_for_tests()
    main.stream_tickets.clear()
    main.stream_keys.clear()
    main.native_session_health.clear()
    main.stream_tickets[ticket] = (
        user,
        "s-health-fail",
        datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    main.stream_keys["s-health-fail"] = "stream-key"
    main.native_session_health["s-health-fail"] = FailingHealth()

    class Socket:
        headers = {
            "x-goog-iap-jwt-assertion": "signed",
            "sec-websocket-protocol": f"tars-stream, {ticket}",
        }

        async def accept(self, **_kwargs):
            return None

        async def close(self, **_kwargs):
            return None

    with pytest.raises(RuntimeError):
        asyncio.run(main.native_stream_endpoint(Socket(), "s-health-fail"))
    assert main.auth_runtime.live_connection_count == 0
    assert main.native_session_health == {}
    main.stream_tickets.clear()
    main.stream_keys.clear()


def test_provider_operation_registry_waits_for_final_summary_cancellation(monkeypatch):
    settings = iap_settings()
    user = AuthContext("provider-owner", OPERATOR_EMAIL, "ella-internal", auth_time=77)
    manager = SessionManager(settings)
    session = manager.create_session(owner_id=user.uid, org_id=user.org_id)
    manager.add_transcript_segment(
        session.id,
        TranscriptSegment(text="final evidence", is_final=True, speaker="Speaker 1"),
    )
    started = asyncio.Event()
    release = asyncio.Event()
    provider_calls: list[str] = []

    class BlockingGemini:
        async def generate(self, **_kwargs):
            provider_calls.append("generate")
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                # Simulate a provider task that performs a cooperative final
                # turn after cancellation; logout must await that turn.
                await release.wait()
                raise
            return "summary"

    class Storage:
        async def save_summary(self, *_args, **_kwargs):
            raise AssertionError("terminal cancellation must not persist")

        async def save_session(self, *_args, **_kwargs):
            raise AssertionError("terminal cancellation must not persist")

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    monkeypatch.setattr(main, "gemini_client", BlockingGemini())
    monkeypatch.setattr(main, "firestore_storage", Storage())
    monkeypatch.setattr(main, "current_auth", lambda: user)
    main.auth_runtime.reset_for_tests()

    async def exercise():
        task = asyncio.create_task(main._generate_final_summary(session.id))
        await started.wait()
        main.auth_runtime.revoke_principal(user.uid, user.auth_time)
        drain = asyncio.create_task(main._cancel_provider_operations(user.uid))
        await asyncio.sleep(0)
        assert not drain.done()
        release.set()
        await drain
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert provider_calls == ["generate"]
    assert main.active_provider_operations == {}
    assert main.auth_runtime.counts()["active_provider_operations"] == 0
    main.auth_runtime.reset_for_tests()


def test_final_summary_terminal_during_early_interview_read_has_no_later_side_effects(
    monkeypatch,
):
    settings = iap_settings()
    user = AuthContext("summary-early-read", OPERATOR_EMAIL, "ella-internal", auth_time=82)
    manager = SessionManager(settings)
    session = manager.create_session(
        owner_id=user.uid,
        org_id=user.org_id,
        mode=SessionMode.INTERVIEW,
    )
    read_started = asyncio.Event()
    release_read = asyncio.Event()
    provider_calls: list[str] = []
    writes: list[str] = []

    class Storage:
        async def get_interview_report(self, _session_id):
            read_started.set()
            await release_read.wait()
            return None

        async def get_report_generation_state(self, _session_id):
            raise AssertionError("terminal read must fence later storage reads")

        async def save_report_generation_state(self, _session_id, state, **_kwargs):
            writes.append(state)

    class Gemini:
        async def generate(self, **_kwargs):
            provider_calls.append("generate")
            return "{}"

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    monkeypatch.setattr(main, "firestore_storage", Storage())
    monkeypatch.setattr(main, "gemini_client", Gemini())
    main.active_provider_operations.clear()
    main.session_auth_generations[session.id] = (user.uid, user.auth_time)
    main.auth_runtime.reset_for_tests()

    async def exercise():
        task = asyncio.create_task(main._generate_final_summary(session.id))
        await read_started.wait()
        main.auth_runtime.revoke_principal(user.uid, user.auth_time)
        release_read.set()
        await task

    try:
        asyncio.run(exercise())
        assert provider_calls == []
        assert writes == []
        assert main.active_provider_operations == {}
    finally:
        main.session_auth_generations.pop(session.id, None)
        main.auth_runtime.reset_for_tests()


def test_final_summary_failure_state_is_fenced_after_terminal_early_read(monkeypatch):
    settings = iap_settings()
    user = AuthContext("summary-early-failure", OPERATOR_EMAIL, "ella-internal", auth_time=83)
    manager = SessionManager(settings)
    session = manager.create_session(
        owner_id=user.uid,
        org_id=user.org_id,
        mode=SessionMode.INTERVIEW,
    )
    read_started = asyncio.Event()
    release_read = asyncio.Event()
    writes: list[str] = []

    class Storage:
        async def get_interview_report(self, _session_id):
            read_started.set()
            await release_read.wait()
            raise RuntimeError("synthetic early read failure")

        async def save_report_generation_state(self, _session_id, state, **_kwargs):
            writes.append(state)

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    monkeypatch.setattr(main, "firestore_storage", Storage())
    monkeypatch.setattr(main, "gemini_client", SimpleNamespace())
    main.active_provider_operations.clear()
    main.session_auth_generations[session.id] = (user.uid, user.auth_time)
    main.auth_runtime.reset_for_tests()

    async def exercise():
        task = asyncio.create_task(main._generate_final_summary(session.id))
        await read_started.wait()
        main.auth_runtime.revoke_principal(user.uid, user.auth_time)
        release_read.set()
        await task

    try:
        asyncio.run(exercise())
        assert writes == []
        assert main.active_provider_operations == {}
    finally:
        main.session_auth_generations.pop(session.id, None)
        main.auth_runtime.reset_for_tests()


def test_iap_final_summary_provider_failure_does_not_write_failed_marker(monkeypatch):
    settings = iap_settings()
    user = AuthContext("summary-provider-failure", OPERATOR_EMAIL, "ella-internal", auth_time=85)
    manager = SessionManager(settings)
    session = manager.create_session(
        owner_id=user.uid,
        org_id=user.org_id,
        mode=SessionMode.INTERVIEW,
    )
    writes: list[str] = []
    provider_calls: list[str] = []

    class Storage:
        async def get_interview_report(self, _session_id):
            return None

        async def get_report_generation_state(self, _session_id):
            return None

        async def save_report_generation_state(self, _session_id, state, **_kwargs):
            writes.append(state)

        async def get_session_transcript(self, _session_id):
            return [
                {
                    "id": "summary-evidence",
                    "text": "durable evidence",
                    "speaker": "Speaker 1",
                    "startTime": 0.0,
                    "endTime": 1.0,
                    "confidence": 1.0,
                    "sequenceNumber": 1,
                    "ownerId": user.uid,
                    "orgId": user.org_id,
                }
            ]

        async def get_session_notes(self, _session_id):
            return []

        async def get_interview_context(self, _session_id):
            return []

    class Gemini:
        async def generate(self, **_kwargs):
            provider_calls.append("generate")
            main.auth_runtime.revoke_principal(user.uid, user.auth_time)
            raise RuntimeError("synthetic provider failure")

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    monkeypatch.setattr(main, "firestore_storage", Storage())
    monkeypatch.setattr(main, "gemini_client", Gemini())
    main.active_provider_operations.clear()
    main.session_auth_generations[session.id] = (user.uid, user.auth_time)
    main.auth_runtime.reset_for_tests()

    try:
        asyncio.run(main._generate_final_summary(session.id))
        assert provider_calls == ["generate"]
        assert writes == ["generating"]
        assert "failed" not in writes
        assert main.active_provider_operations == {}
    finally:
        main.session_auth_generations.pop(session.id, None)
        main.auth_runtime.reset_for_tests()


def test_non_iap_final_summary_provider_failure_keeps_failed_marker(monkeypatch):
    settings = Settings(google_cloud_project="synthetic-project", auth_mode="firebase")
    manager = SessionManager(settings)
    session = manager.create_session(mode=SessionMode.INTERVIEW)
    writes: list[str] = []

    class Storage:
        async def get_interview_report(self, _session_id):
            return None

        async def get_report_generation_state(self, _session_id):
            return None

        async def save_report_generation_state(self, _session_id, state, **_kwargs):
            writes.append(state)

        async def get_session_transcript(self, _session_id):
            return [
                {
                    "id": "compat-evidence",
                    "text": "durable evidence",
                    "speaker": "Speaker 1",
                    "startTime": 0.0,
                    "endTime": 1.0,
                    "confidence": 1.0,
                    "sequenceNumber": 1,
                }
            ]

        async def get_session_notes(self, _session_id):
            return []

        async def get_interview_context(self, _session_id):
            return []

    class Gemini:
        async def generate(self, **_kwargs):
            raise RuntimeError("synthetic provider failure")

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    monkeypatch.setattr(main, "firestore_storage", Storage())
    monkeypatch.setattr(main, "gemini_client", Gemini())
    main.active_provider_operations.clear()
    main.auth_runtime.reset_for_tests()

    asyncio.run(main._generate_final_summary(session.id))
    assert writes == ["generating", "failed"]
    assert main.active_provider_operations == {}
    main.auth_runtime.reset_for_tests()


def test_provider_cancellation_is_bounded_and_retains_live_accounting(monkeypatch):
    settings = iap_settings()
    user = AuthContext("provider-resistant", OPERATOR_EMAIL, "ella-internal", auth_time=79)
    monkeypatch.setattr(main, "settings", settings)
    main.active_provider_operations.clear()
    main.auth_runtime.reset_for_tests()

    started = asyncio.Event()
    release = asyncio.Event()

    async def worker():
        operation_id = main._register_provider_operation(user=user)
        assert operation_id is not None
        try:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
                raise
        finally:
            main._release_provider_operation(operation_id)

    async def exercise():
        task = asyncio.create_task(worker())
        await started.wait()
        main.auth_runtime.revoke_principal(user.uid, user.auth_time)
        started_cancel = time.monotonic()
        await main._cancel_provider_operations(user.uid)
        elapsed = time.monotonic() - started_cancel
        assert elapsed < 1.0
        assert len(main.active_provider_operations) == 1
        assert main.auth_runtime.counts()["active_provider_operations"] == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

    asyncio.run(exercise())
    assert main.active_provider_operations == {}
    assert main.auth_runtime.counts()["active_provider_operations"] == 0
    main.auth_runtime.reset_for_tests()


def test_iap_provider_operation_rejects_missing_auth_time_before_provider_call(monkeypatch):
    settings = iap_settings()
    user = AuthContext("missing-generation", OPERATOR_EMAIL, "ella-internal")
    manager = SessionManager(settings)
    session = manager.create_session(owner_id=user.uid, org_id=user.org_id)
    provider_calls: list[str] = []

    class Gemini:
        async def generate(self, **_kwargs):
            provider_calls.append("generate")
            return "1. follow up"

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    monkeypatch.setattr(main, "gemini_client", Gemini())
    main.active_provider_operations.clear()
    main.session_auth_generations[session.id] = (user.uid, None)  # type: ignore[assignment]
    main.auth_runtime.reset_for_tests()

    try:
        asyncio.run(main._generate_interview_suggestions(session.id))
        assert provider_calls == []
        assert main.active_provider_operations == {}
    finally:
        main.session_auth_generations.pop(session.id, None)
        main.auth_runtime.reset_for_tests()


def test_iap_provider_operation_rejects_unbound_user_before_provider_call(monkeypatch):
    monkeypatch.setattr(main, "settings", iap_settings())
    main.active_provider_operations.clear()
    main.auth_runtime.reset_for_tests()

    async def exercise():
        assert main._register_provider_operation(user=None) is None

    asyncio.run(exercise())
    assert main.active_provider_operations == {}
    assert main.auth_runtime.counts()["active_provider_operations"] == 0
    main.auth_runtime.reset_for_tests()


def test_rolling_summary_rechecks_operation_after_terminal_broadcast(monkeypatch):
    settings = iap_settings()
    user = AuthContext("rolling-owner", OPERATOR_EMAIL, "ella-internal", auth_time=80)
    manager = SessionManager(settings)
    session = manager.create_session(owner_id=user.uid, org_id=user.org_id)
    manager.add_transcript_segment(
        session.id,
        TranscriptSegment(text="candidate text", is_final=True),
    )

    test_settings = settings

    class Context:
        settings = test_settings
        last_summary_seq = 0
        current_summary = ""

        async def update_summary(self, _text, batch_end):
            self.last_summary_seq = batch_end
            return "summary"

    class Storage:
        def __init__(self):
            self.saved = []

        async def save_summary(self, *args, **kwargs):
            self.saved.append((args, kwargs))

    storage = Storage()
    broadcast_started = asyncio.Event()
    release_broadcast = asyncio.Event()

    async def broadcast(*_args, **_kwargs):
        broadcast_started.set()
        await release_broadcast.wait()

    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    monkeypatch.setattr(main, "context_window", Context())
    main.context_windows[session.id] = main.context_window
    monkeypatch.setattr(main, "gemini_client", SimpleNamespace())
    monkeypatch.setattr(main, "ws_manager", SimpleNamespace(next_sequence=lambda _sid: 1, broadcast=broadcast))
    monkeypatch.setattr(main, "firestore_storage", storage)
    main.session_auth_generations[session.id] = (user.uid, user.auth_time)
    main.auth_runtime.reset_for_tests()

    async def exercise():
        task = asyncio.create_task(main._generate_rolling_summary(session.id))
        await broadcast_started.wait()
        main.auth_runtime.revoke_principal(user.uid, user.auth_time)
        release_broadcast.set()
        await task

    asyncio.run(exercise())
    assert storage.saved == []
    main.session_auth_generations.pop(session.id, None)
    main.context_windows.pop(session.id, None)
    main.auth_runtime.reset_for_tests()


def test_provider_generation_rejects_old_auth_and_allows_newer_auth(monkeypatch):
    settings = iap_settings()
    user = AuthContext("generation-owner", OPERATOR_EMAIL, "ella-internal", auth_time=81)
    manager = SessionManager(settings)
    session = manager.create_session(owner_id=user.uid, org_id=user.org_id)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "session_mgr", manager)
    main.auth_runtime.reset_for_tests()
    main.session_auth_generations[session.id] = (user.uid, user.auth_time)
    main.auth_runtime.revoke_principal(user.uid, user.auth_time)

    async def exercise():
        assert main._register_provider_operation(user=user, session_id=session.id) is None
        newer = AuthContext(user.uid, user.email, user.org_id, auth_time=82)
        main.session_auth_generations[session.id] = (newer.uid, newer.auth_time)
        operation_id = main._register_provider_operation(user=newer, session_id=session.id)
        assert operation_id is not None
        main._release_provider_operation(operation_id)

    asyncio.run(exercise())
    main.session_auth_generations.pop(session.id, None)
    main.auth_runtime.reset_for_tests()
    main.active_provider_operations.clear()
    main.auth_runtime.reset_for_tests()


def test_ticket_mint_replaces_same_session_ticket_and_runtime_count(monkeypatch):
    user = AuthContext("ticket-owner", OPERATOR_EMAIL, "ella-internal", auth_time=78)
    monkeypatch.setattr(main, "settings", iap_settings())
    main.auth_runtime.reset_for_tests()
    main.ws_tickets.clear()
    first = main._mint_capability(main.ws_tickets, user, "same-session", 60)
    assert main.auth_runtime.register_ticket(first, user.uid, user.auth_time)
    second = main._mint_capability(main.ws_tickets, user, "same-session", 60)
    assert first not in main.ws_tickets
    assert second in main.ws_tickets
    assert main.auth_runtime.counts()["outstanding_browser_tickets"] == 0
    assert main.auth_runtime.register_ticket(second, user.uid, user.auth_time)
    assert main.auth_runtime.counts()["outstanding_browser_tickets"] == 1
    main.ws_tickets.clear()
    main.auth_runtime.reset_for_tests()


def test_browser_replay_watcher_closes_on_revocation_before_replay_finishes(monkeypatch):
    settings = iap_settings()
    user = AuthContext("replay-user", OPERATOR_EMAIL, "ella-internal", auth_time=55)
    ticket = "replay-ticket"
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: user)
    monkeypatch.setattr(
        main,
        "_read_session",
        lambda _sid: asyncio.sleep(0, result=SimpleNamespace(owner_id=user.uid, org_id=user.org_id)),
    )
    main.auth_runtime.reset_for_tests()
    main.ws_tickets.clear()
    main.ws_tickets[ticket] = (
        user,
        "s1",
        datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    replay_started = asyncio.Event()
    closed = asyncio.Event()

    class Socket:
        headers = {
            "x-goog-iap-jwt-assertion": "signed",
            "sec-websocket-protocol": f"tars-ticket, {ticket}",
        }
        query_params = {"last_seq": "1"}

        async def close(self, **_kwargs):
            closed.set()

        async def receive_json(self):
            raise WebSocketDisconnect(code=1000)

    async def slow_connect(*_args, **_kwargs):
        replay_started.set()
        await closed.wait()

    monkeypatch.setattr(main.ws_manager, "connect", slow_connect)
    monkeypatch.setattr(main.ws_manager, "disconnect", lambda *_args: None)

    async def exercise():
        task = asyncio.create_task(main.websocket_endpoint(Socket(), "s1"))
        await replay_started.wait()
        main.auth_runtime.revoke_principal(user.uid, user.auth_time)
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(exercise())
    assert main.auth_runtime.live_connection_count == 0


def test_browser_read_terminal_race_never_accepts_or_replays(monkeypatch):
    settings = iap_settings()
    user = AuthContext("read-race-user", OPERATOR_EMAIL, "ella-internal", auth_time=56)
    ticket = "read-race-ticket"
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "verify_iap_token", lambda *_args, **_kwargs: user)
    main.auth_runtime.reset_for_tests()
    main.ws_tickets.clear()
    main.ws_tickets[ticket] = (
        user,
        "s-read-race",
        datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    assert main.auth_runtime.register_ticket(ticket, user.uid, user.auth_time)
    read_started = asyncio.Event()
    release_read = asyncio.Event()
    closed = asyncio.Event()
    connected: list[str] = []

    class Socket:
        headers = {
            "x-goog-iap-jwt-assertion": "signed",
            "sec-websocket-protocol": f"tars-ticket, {ticket}",
        }
        query_params = {}

        async def close(self, **_kwargs):
            closed.set()

    async def read_session(_session_id):
        read_started.set()
        await release_read.wait()
        return SimpleNamespace(
            owner_id=user.uid,
            org_id=user.org_id,
            status=SessionStatus.ACTIVE,
        )

    async def connect(*_args, **_kwargs):
        connected.append("connect")

    monkeypatch.setattr(main, "_read_session", read_session)
    monkeypatch.setattr(main.ws_manager, "connect", connect)
    monkeypatch.setattr(main.ws_manager, "disconnect", lambda *_args: None)

    async def exercise():
        endpoint = asyncio.create_task(
            main.websocket_endpoint(Socket(), "s-read-race")
        )
        await read_started.wait()
        main.auth_runtime.revoke_principal(user.uid, user.auth_time)
        await asyncio.wait_for(closed.wait(), timeout=1)
        release_read.set()
        await asyncio.wait_for(endpoint, timeout=1)

    asyncio.run(exercise())
    assert connected == []
    assert main.auth_runtime.live_connection_count == 0
    assert main.auth_runtime.counts()["outstanding_browser_tickets"] == 0
    main.ws_tickets.clear()
    main.auth_runtime.reset_for_tests()


def test_browser_connect_terminal_during_replay_unregisters_and_closes():
    manager = WSConnectionManager()
    admitted = True
    replay_started = asyncio.Event()
    release_replay = asyncio.Event()
    closed: list[dict] = []

    class Socket:
        async def accept(self, **_kwargs):
            return None

        async def send_json(self, _payload):
            replay_started.set()
            await release_replay.wait()

        async def close(self, **kwargs):
            closed.append(kwargs)

    manager._ensure_session("connect-race")
    manager._sequence_counters["connect-race"] = 2
    manager._message_buffer["connect-race"].append(
        SimpleNamespace(sequence_number=2, model_dump=lambda: {"type": "replay"})
    )

    async def exercise():
        nonlocal admitted
        task = asyncio.create_task(
            manager.connect(
                Socket(),
                "connect-race",
                last_seq=1,
                admission_check=lambda: admitted,
            )
        )
        await replay_started.wait()
        admitted = False
        release_replay.set()
        with pytest.raises(ConnectionAdmissionLost):
            await task

    asyncio.run(exercise())
    assert manager._connections["connect-race"] == []
    assert closed and closed[-1]["code"] == 4003
