"""Focused HTTP and WebSocket authorization matrix for the internal tenancy gate.

All provider and persistence calls in this module are mocked.  These tests are
deliberately narrower than an end-to-end Firebase/Firestore run: they prove
that the ASGI boundary, one-time capabilities, and owner/org checks fail closed
before a route or socket can touch interview data.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException, Request
from fastapi.responses import PlainTextResponse
from starlette.websockets import WebSocketDisconnect

from backend import main
from backend.auth import AuthContext, AuthenticationError, verify_bearer_token
from backend.config import Settings
from backend.schemas.models import SessionStatus


def auth_settings(**overrides) -> Settings:
    values = {
        "google_cloud_project": "tars-test",
        "auth_allowed_emails": "recruiter@example.com",
        "auth_org_id": "ella-internal",
    }
    values.update(overrides)
    return Settings(**values)


def claims(**overrides) -> dict:
    value = {
        "uid": "uid-a",
        "email": "recruiter@example.com",
        "email_verified": True,
        "aud": "tars-test",
        "iss": "https://securetoken.google.com/tars-test",
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize("authorization", [None, "", "Basic token", "Bearer "])
def test_missing_or_malformed_bearer_is_rejected_without_provider_call(authorization):
    with patch("backend.auth.firebase_auth.verify_id_token") as verify:
        with pytest.raises(AuthenticationError):
            verify_bearer_token(authorization, auth_settings())
    verify.assert_not_called()


def test_revoked_token_is_rejected_with_provider_revocation_check():
    with patch(
        "backend.auth.firebase_auth.verify_id_token",
        side_effect=RuntimeError("revoked"),
    ) as verify:
        with pytest.raises(AuthenticationError):
            verify_bearer_token("Bearer revoked", auth_settings())
    verify.assert_called_once_with("revoked", check_revoked=True)


@pytest.mark.parametrize(
    "token_claims",
    [
        claims(email_verified=False),
        claims(email="not-provisioned@example.com"),
        claims(aud="another-project"),
        claims(iss="https://securetoken.google.com/another-project"),
    ],
)
def test_unverified_unallowlisted_or_wrong_audience_identity_is_rejected(token_claims):
    with patch("backend.auth.firebase_auth.verify_id_token", return_value=token_claims):
        with pytest.raises(AuthenticationError):
            verify_bearer_token("Bearer token", auth_settings())


def _request(path: str, *, method: str = "GET", headers: dict[str, str] | None = None) -> Request:
    encoded_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": encoded_headers,
            "client": ("testclient", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def _run_middleware(path: str, *, headers: dict[str, str] | None = None):
    async def endpoint(_request):
        return PlainTextResponse("ok")

    return asyncio.run(
        main.authenticate_api_requests(
            _request(path, headers=headers),
            endpoint,
        )
    )


def test_api_auth_boundary_rejects_route_requests_but_leaves_health_probe_public(
    monkeypatch,
):
    monkeypatch.setattr(main, "settings", auth_settings())
    denied = _run_middleware("/api/me")
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == "Bearer"
    assert _run_middleware("/healthz").status_code == 200


@pytest.mark.parametrize(
    "path",
    sorted(
        {
            route.path
            for route in main.app.routes
            if getattr(route, "path", "").startswith("/api/")
        }
    ),
)
def test_every_api_route_pattern_is_inside_the_auth_boundary(path, monkeypatch):
    monkeypatch.setattr(main, "settings", auth_settings())
    response = _run_middleware(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_cors_headers_survive_auth_401():
    async def request():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(
                "/api/me",
                headers={"Origin": "http://localhost:3000"},
            )

    old_settings = main.settings
    main.settings = auth_settings()
    try:
        response = asyncio.run(request())
    finally:
        main.settings = old_settings

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cross_owner_mutation_is_non_enumerating_404_at_http_boundary(monkeypatch):
    class FakeSessionManager:
        def get_session(self, _session_id):
            return SimpleNamespace(owner_id="uid-a", org_id="ella-internal")

    async def request():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/sessions/s1/speakers",
                headers={"Authorization": "Bearer token"},
                json={"candidate": "Candidato"},
            )

    monkeypatch.setattr(main, "settings", auth_settings())
    monkeypatch.setattr(main, "session_mgr", FakeSessionManager())
    monkeypatch.setattr(
        main,
        "verify_bearer_token",
        lambda _authorization, _settings: AuthContext(
            "uid-b", "other@example.com", "ella-internal"
        ),
    )
    response = asyncio.run(request())
    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}


@pytest.mark.parametrize(
    "record",
    [
        {"ownerId": "uid-b", "orgId": "ella-internal"},
        {"ownerId": "uid-a", "orgId": "other-org"},
        {},
    ],
)
def test_child_records_missing_or_outside_parent_scope_are_rejected(record):
    user_token = main.set_current_auth(AuthContext("uid-a", "a@example.com", "ella-internal"))
    enforced_token = main.set_auth_enforced()
    session = SimpleNamespace(owner_id="uid-a", org_id="ella-internal")
    try:
        with pytest.raises(HTTPException) as exc_info:
            main._assert_child_scope([record], session)
        assert exc_info.value.status_code == 404
    finally:
        main.reset_current_auth(user_token)
        main.reset_auth_enforced(enforced_token)


def test_stop_capability_is_bounded_and_only_fallback_for_matching_stop_route(monkeypatch):
    settings = auth_settings(auth_stop_capability_ttl_seconds=120)
    monkeypatch.setattr(main, "settings", settings)
    main.stop_capabilities.clear()
    user = AuthContext("uid-a", "a@example.com", "ella-internal")
    try:
        minted = main._mint_capability(main.stop_capabilities, user, "s1", 120)
        owner, session_id, expires_at = main.stop_capabilities[minted]
        assert owner == user
        assert session_id == "s1"
        assert expires_at > datetime.now(timezone.utc) + timedelta(seconds=119)

        accepted = _run_middleware(
            "/api/sessions/s1/stop",
            headers={"X-TARS-Stop-Capability": minted},
        )
        assert accepted.status_code == 200

        wrong_session = _run_middleware(
            "/api/sessions/s2/stop",
            headers={"X-TARS-Stop-Capability": minted},
        )
        assert wrong_session.status_code == 401

        main.stop_capabilities[minted] = (user, "s1", datetime.now(timezone.utc) - timedelta(seconds=1))
        expired = _run_middleware(
            "/api/sessions/s1/stop",
            headers={"X-TARS-Stop-Capability": minted},
        )
        assert expired.status_code == 401
    finally:
        main.stop_capabilities.clear()


def test_incomplete_stop_keeps_recovery_capability_until_terminal_write_succeeds(
    monkeypatch,
):
    user = AuthContext("uid-a", "a@example.com", "ella-internal")
    session = SimpleNamespace(
        id="s1",
        owner_id=user.uid,
        org_id=user.org_id,
        status=SessionStatus.INCOMPLETE,
        mode="meeting",
    )

    class FakeSessionManager:
        def get_session(self, _session_id):
            return session

    class FlakyFirestore:
        def __init__(self):
            self.calls = 0

        async def save_session(self, _session):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient Firestore failure")

    firestore = FlakyFirestore()
    old_session_mgr = main.session_mgr
    old_firestore = main.firestore_storage
    old_locks = main.session_stop_locks.copy()
    main.session_stop_locks.clear()
    main.stop_capabilities.clear()
    main.session_mgr = FakeSessionManager()
    main.firestore_storage = firestore
    auth_token = main.set_current_auth(user)
    capability = main._mint_capability(main.stop_capabilities, user, session.id, 120)

    async def no_pipeline(_session_id):
        raise AssertionError("incomplete retry must not restart the pipeline")

    monkeypatch.setattr(main, "_stop_pipeline", no_pipeline)
    try:
        with pytest.raises(RuntimeError, match="transient Firestore failure"):
            asyncio.run(main.stop_session(session.id))
        assert capability in main.stop_capabilities

        result = asyncio.run(main.stop_session(session.id))
        assert result["transcription_complete"] is False
        assert capability not in main.stop_capabilities
        assert firestore.calls == 2
    finally:
        main.reset_current_auth(auth_token)
        main.stop_capabilities.clear()
        main.session_stop_locks.clear()
        main.session_stop_locks.update(old_locks)
        main.session_mgr = old_session_mgr
        main.firestore_storage = old_firestore


def test_active_session_delete_is_rejected_before_storage_mutation(monkeypatch):
    user = AuthContext("uid-a", "a@example.com", "ella-internal")
    session = SimpleNamespace(
        id="active-session",
        owner_id=user.uid,
        org_id=user.org_id,
        status=SessionStatus.ACTIVE,
    )

    class FakeSessionManager:
        def get_session(self, _session_id):
            return session

    class FakeFirestore:
        _get_db = AsyncMock()

    old_session_mgr = main.session_mgr
    old_firestore = main.firestore_storage
    main.session_mgr = FakeSessionManager()
    storage = FakeFirestore()
    main.firestore_storage = storage
    auth_token = main.set_current_auth(user)
    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(main.delete_session(session.id))
        assert exc_info.value.status_code == 409
        assert "Stop the active session" in str(exc_info.value.detail)
        storage._get_db.assert_not_awaited()
    finally:
        main.reset_current_auth(auth_token)
        main.session_mgr = old_session_mgr
        main.firestore_storage = old_firestore


def test_delete_fences_late_callbacks_and_cancels_final_report(monkeypatch):
    session = SimpleNamespace(
        id="completed-session",
        owner_id=None,
        org_id=None,
        status=SessionStatus.COMPLETED,
    )

    class FakeSessionManager:
        def get_session(self, _session_id):
            return session

    class FakeFirestore:
        _get_db = AsyncMock(return_value=object())

    deletion = AsyncMock(return_value={"session_id": session.id})
    monkeypatch.setattr(
        "backend.storage.deletion.delete_session_everywhere",
        deletion,
    )
    old_session_mgr = main.session_mgr
    old_firestore = main.firestore_storage
    old_locks = main.session_stop_locks
    old_tasks = main.final_summary_tasks
    old_scheduled = main.final_summary_scheduled
    old_single_source_tasks = main.single_source_check_tasks
    old_fences = main.session_deletion_fences
    old_deleted = main.deleted_sessions
    old_documents = main.interview_documents.get(session.id)
    old_context_window = main.context_windows.get(session.id)
    cleanup_calls = []
    monkeypatch.setattr(
        main.ws_manager,
        "cleanup_session",
        lambda session_id: cleanup_calls.append(session_id),
    )
    main.session_mgr = FakeSessionManager()
    main.firestore_storage = FakeFirestore()
    main.session_stop_locks = {}
    main.final_summary_tasks = {}
    main.final_summary_scheduled = set()
    main.single_source_check_tasks = {}
    main.session_deletion_fences = set()
    main.deleted_sessions = set()
    main.interview_documents[session.id] = {"resume": "sensitive"}
    main.context_windows[session.id] = object()
    main.single_source_warned.add(session.id)

    async def pending_report():
        await asyncio.Event().wait()

    async def run():
        task = asyncio.create_task(pending_report())
        warning_task = asyncio.create_task(pending_report())
        main.final_summary_tasks[session.id] = task
        main.final_summary_scheduled.add(session.id)
        main.single_source_check_tasks[session.id] = warning_task
        result = await main.delete_session(session.id)
        return result, task, warning_task

    try:
        result, task, warning_task = asyncio.run(run())
        assert result["session_id"] == session.id
        assert task.cancelled()
        assert warning_task.cancelled()
        assert deletion.await_count == 1
        assert session.id in main.session_deletion_fences
        assert session.id in main.deleted_sessions
        assert session.id not in main.interview_documents
        assert session.id not in main.context_windows
        assert session.id not in main.single_source_check_tasks
        assert session.id not in main.single_source_warned
        assert cleanup_calls == [session.id]
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(main._read_session(session.id))
        assert exc_info.value.status_code == 404
    finally:
        main.session_mgr = old_session_mgr
        main.firestore_storage = old_firestore
        main.session_stop_locks = old_locks
        main.final_summary_tasks = old_tasks
        main.final_summary_scheduled = old_scheduled
        main.single_source_check_tasks = old_single_source_tasks
        main.session_deletion_fences = old_fences
        main.deleted_sessions = old_deleted
        if old_documents is None:
            main.interview_documents.pop(session.id, None)
        else:
            main.interview_documents[session.id] = old_documents
        if old_context_window is None:
            main.context_windows.pop(session.id, None)
        else:
            main.context_windows[session.id] = old_context_window
        main.single_source_warned.discard(session.id)


class FakeWebSocket:
    def __init__(self, ticket: str):
        self.headers = {"sec-websocket-protocol": f"tars-ticket, {ticket}"}
        self.query_params = {}
        self.closed: list[dict] = []

    async def close(self, **kwargs):
        self.closed.append(kwargs)

    async def receive_json(self):
        raise WebSocketDisconnect(code=1000)


def test_websocket_ticket_is_single_use_and_bound_to_session(monkeypatch):
    ticket = "one-time-ticket"
    user = AuthContext("uid-a", "a@example.com", "ella-internal")
    main.ws_tickets.clear()
    main.ws_tickets[ticket] = (
        user,
        "s1",
        datetime.now(timezone.utc) + timedelta(seconds=1),
    )

    async def read_session(_session_id):
        return SimpleNamespace(owner_id="uid-a", org_id="ella-internal")

    connect = AsyncMock()
    expiry = AsyncMock()
    monkeypatch.setattr(main, "_read_session", read_session)
    monkeypatch.setattr(main.ws_manager, "connect", connect)
    monkeypatch.setattr(main.ws_manager, "disconnect", lambda *_args: None)
    monkeypatch.setattr(main, "_close_ws_at_expiry", expiry)
    try:
        first = FakeWebSocket(ticket)
        asyncio.run(main.websocket_endpoint(first, "s1"))
        assert first.closed == []
        connect.assert_awaited_once()
        assert ticket not in main.ws_tickets

        replay = FakeWebSocket(ticket)
        asyncio.run(main.websocket_endpoint(replay, "s1"))
        assert replay.closed == [{"code": 1008}]
    finally:
        main.ws_tickets.clear()


def test_websocket_expiry_closes_socket_without_provider_or_http_calls():
    websocket = FakeWebSocket("unused")
    asyncio.run(
        main._close_ws_at_expiry(
            websocket,
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    assert websocket.closed == [{"code": 4001, "reason": "auth_expired"}]


def test_extension_bridge_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(main, "settings", auth_settings(extension_enabled=False))
    main.extension_tokens["s1"] = "legacy-token"
    try:
        with pytest.raises(HTTPException) as exc_info:
            main._validate_extension_token("s1", "Bearer legacy-token")
        assert exc_info.value.status_code == 404
    finally:
        main.extension_tokens.clear()


def test_review_rejects_foreign_raw_record_before_deserialization(monkeypatch):
    class FakeFirestore:
        async def get_session_record(self, _session_id):
            return {
                "ownerId": "uid-b",
                "orgId": "ella-internal",
                "mode": object(),
            }

    monkeypatch.setattr(main, "firestore_storage", FakeFirestore())
    auth_token = main.set_current_auth(
        AuthContext("uid-a", "a@example.com", "ella-internal")
    )
    enforced_token = main.set_auth_enforced()
    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(main.get_session_review("foreign"))
        assert exc_info.value.status_code == 404
    finally:
        main.reset_current_auth(auth_token)
        main.reset_auth_enforced(enforced_token)
