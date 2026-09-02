from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from backend.workers.meet_transcript_automation import (
    MeetAutomationInvalid,
    MeetAutomationNotFound,
    PushTokenClaims,
    verify_push_token,
)
from backend.tests.test_meet_transcript_automation import binding, grant
from backend.workers.meet_transcript_provider import (
    GOOGLE_OAUTH2_CERTS_URL,
    REQUEST_TIMEOUT_SECONDS,
    StaticAccessTokenSource,
    create_meet_transcript_provider_runtime,
    HttpxWorkspaceTransport,
    GooglePubSubPushTokenVerifier,
    GoogleWorkspaceMeetProvider,
)


NOW = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
MEETING_CODE = "abc-mnop-xyz"
CONFERENCE = "conferenceRecords/record-1"
TRANSCRIPT = f"{CONFERENCE}/transcripts/transcript-1"
SPACE = "spaces/space-1"
ACCESS_TOKEN = "workspace-access-token"


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def rsa_material():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    return key, pem


def signed_jwt(private_key, claims, *, kid="test-kid", alg="RS256"):
    header = b64url(json.dumps({"alg": alg, "typ": "JWT", "kid": kid}, separators=(",", ":")).encode())
    payload = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{b64url(signature)}"


class ScriptedHandler:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            return httpx.Response(500, json={"error": "unexpected request"})
        status, body = self.responses.pop(0)
        if isinstance(body, bytes):
            return httpx.Response(status, content=body)
        return httpx.Response(status, json=body)


def calendar_event(event_id="event-1", conference_id=MEETING_CODE):
    return {
        "id": event_id,
        "conferenceData": {
            "conferenceId": conference_id,
            "conferenceSolution": {"key": {"type": "hangoutsMeet"}},
        },
    }


def conference_record():
    return {"name": CONFERENCE, "space": SPACE}


def generated_transcript():
    return {"name": TRANSCRIPT, "state": "FILE_GENERATED"}


def entries_page():
    return {
        "transcriptEntries": [
            {
                "name": f"{TRANSCRIPT}/entries/entry-1",
                "participant": f"{CONFERENCE}/participants/participant-1",
                "text": "Synthetic transcript entry 1",
                "languageCode": "en-US",
                "startTime": "2026-09-01T16:00:00Z",
                "endTime": "2026-09-01T16:00:01Z",
            }
        ]
    }


async def client_for(handler):
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        trust_env=False,
        follow_redirects=False,
    )


def test_runtime_factory_requires_explicit_dependencies_and_ignores_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_WORKSPACE_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/not-used.json")
    assert create_meet_transcript_provider_runtime() is None
    assert (
        create_meet_transcript_provider_runtime(
            transport=object(),
            token_source=None,
            expected_audience="https://push.example.test/meet",
            expected_service_account_email="push@example.iam.gserviceaccount.com",
        )
        is None
    )


def test_main_does_not_import_or_install_the_provider_adapter(monkeypatch):
    from backend import main as backend_main

    monkeypatch.setenv("GOOGLE_WORKSPACE_ACCESS_TOKEN", "secret-token")
    source = Path("backend/main.py").read_text(encoding="utf-8")
    assert "meet_transcript_provider" not in source
    assert "create_meet_transcript_provider_runtime" not in source
    assert backend_main.workspace_push_token_verifier is None
    assert backend_main.meet_transcript_automation_orchestrator is None


def test_resolve_uses_exact_calendar_and_meet_urls_without_retry():
    item = binding()
    handler = ScriptedHandler(
        [
            (200, calendar_event()),
            (200, {"conferenceRecords": [conference_record()]}),
            (200, {"transcripts": [generated_transcript()]}),
        ]
    )

    async def scenario():
        client = await client_for(handler)
        async with client:
            provider = GoogleWorkspaceMeetProvider(
                HttpxWorkspaceTransport(client),
                StaticAccessTokenSource(ACCESS_TOKEN),
            )
            resolved = await provider.resolve_transcript(grant(), item)
            assert resolved.transcript_name == TRANSCRIPT
            assert resolved.meet_target == item.meet_target
            assert resolved.calendar_event_id == item.calendar_event_id

    asyncio.run(scenario())
    urls = [request.url for request in handler.requests]
    calendar_id = quote(item.calendar_id, safe="")
    event_id = quote(item.calendar_event_id, safe="")
    assert str(urls[0]) == (
        f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}"
    )
    records = urlparse(str(urls[1]))
    assert records.scheme == "https"
    assert records.netloc == "meet.googleapis.com"
    assert records.path == "/v2/conferenceRecords"
    query = parse_qs(records.query, strict_parsing=True)
    assert query == {
        "filter": [f'space.meeting_code = "{MEETING_CODE}"'],
        "pageSize": ["100"],
    }
    assert str(urls[2]) == (
        f"https://meet.googleapis.com/v2/{CONFERENCE}/transcripts?pageSize=100"
    )
    for request in handler.requests:
        assert request.method == "GET"
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        assert request.headers["accept"] == "application/json"
        assert request.extensions.get("timeout") is not None


def test_fetch_uses_exact_entries_url_and_returns_raw_bytes():
    item = binding()
    body = json.dumps(entries_page(), separators=(",", ":")).encode()
    handler = ScriptedHandler([(200, body)])

    async def scenario():
        client = await client_for(handler)
        async with client:
            provider = GoogleWorkspaceMeetProvider(
                HttpxWorkspaceTransport(client),
                StaticAccessTokenSource(ACCESS_TOKEN),
            )
            raw = await provider.fetch_transcript_entries_page(
                grant(),
                item,
                TRANSCRIPT,
                page_size=100,
                page_token="page-2",
            )
            assert raw == body

    asyncio.run(scenario())
    request = handler.requests[0]
    parsed = urlparse(str(request.url))
    assert request.method == "GET"
    assert parsed.netloc == "meet.googleapis.com"
    assert parsed.path == f"/v2/{TRANSCRIPT}/entries"
    assert parse_qs(parsed.query, strict_parsing=True) == {
        "pageSize": ["100"],
        "pageToken": ["page-2"],
    }
    assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert request.headers["accept"] == "application/json"
    assert request.extensions.get("timeout") is not None


def test_provider_does_not_retry_server_errors_and_hides_bodies():
    item = binding()
    handler = ScriptedHandler([(500, {"error": "sensitive provider text"})])

    async def scenario():
        client = await client_for(handler)
        async with client:
            provider = GoogleWorkspaceMeetProvider(
                HttpxWorkspaceTransport(client),
                StaticAccessTokenSource(ACCESS_TOKEN),
            )
            with pytest.raises(MeetAutomationInvalid, match="workspace provider failed"):
                await provider.resolve_transcript(grant(), item)

    asyncio.run(scenario())
    assert len(handler.requests) == 1


def test_missing_calendar_event_is_not_found():
    handler = ScriptedHandler([(404, {"error": "missing"})])

    async def scenario():
        client = await client_for(handler)
        async with client:
            provider = GoogleWorkspaceMeetProvider(
                HttpxWorkspaceTransport(client),
                StaticAccessTokenSource(ACCESS_TOKEN),
            )
            with pytest.raises(MeetAutomationNotFound, match="eligible Meet event not found"):
                await provider.resolve_transcript(grant(), binding())

    asyncio.run(scenario())


def test_space_mismatch_and_ambiguous_transcripts_fail_closed():
    item = binding()
    mismatch = ScriptedHandler(
        [
            (200, calendar_event()),
            (200, {"conferenceRecords": [{"name": CONFERENCE, "space": "spaces/other"}]}),
        ]
    )
    ambiguous = ScriptedHandler(
        [
            (200, calendar_event()),
            (200, {"conferenceRecords": [conference_record()]}),
            (
                200,
                {
                    "transcripts": [
                        generated_transcript(),
                        {"name": f"{CONFERENCE}/transcripts/other", "state": "FILE_GENERATED"},
                    ]
                },
            ),
        ]
    )

    async def scenario(handler):
        client = await client_for(handler)
        async with client:
            provider = GoogleWorkspaceMeetProvider(
                HttpxWorkspaceTransport(client),
                StaticAccessTokenSource(ACCESS_TOKEN),
            )
            with pytest.raises(MeetAutomationInvalid, match="workspace provider failed"):
                await provider.resolve_transcript(grant(), item)

    asyncio.run(scenario(mismatch))
    asyncio.run(scenario(ambiguous))


def test_push_jwt_accepts_signed_google_claims_and_rejects_forgeries():
    private_key, pem = rsa_material()
    handler = ScriptedHandler([(200, {"test-kid": pem})])
    claims = {
        "aud": "https://push.example.test/meet",
        "email": "push@example.iam.gserviceaccount.com",
        "email_verified": True,
        "exp": int((NOW + timedelta(minutes=10)).timestamp()),
        "iat": int((NOW - timedelta(minutes=1)).timestamp()),
        "iss": "https://accounts.google.com",
        "sub": "113774264463038321964",
    }
    token = signed_jwt(private_key, claims)

    async def scenario():
        client = await client_for(handler)
        async with client:
            verifier = GooglePubSubPushTokenVerifier(
                HttpxWorkspaceTransport(client),
                expected_audience=claims["aud"],
                expected_service_account_email=claims["email"],
                clock=lambda: NOW,
            )
            accepted = await verify_push_token(verifier, token)
            assert accepted == PushTokenClaims(
                audience=claims["aud"],
                email=claims["email"],
                email_verified=True,
            )
            assert str(handler.requests[0].url) == GOOGLE_OAUTH2_CERTS_URL
            assert "authorization" not in handler.requests[0].headers
            expired = signed_jwt(
                private_key,
                {**claims, "exp": int((NOW - timedelta(minutes=1)).timestamp())},
            )
            with pytest.raises(MeetAutomationInvalid):
                await verify_push_token(verifier, expired)
            wrong_aud = signed_jwt(private_key, {**claims, "aud": "https://other.example"})
            with pytest.raises(MeetAutomationInvalid):
                await verify_push_token(verifier, wrong_aud)
            string_verified = signed_jwt(private_key, {**claims, "email_verified": "true"})
            with pytest.raises(MeetAutomationInvalid):
                await verify_push_token(verifier, string_verified)
            other_key, _pem = rsa_material()
            forged = signed_jwt(other_key, claims)
            with pytest.raises(MeetAutomationInvalid):
                await verify_push_token(verifier, forged)
            wrong_iss = signed_jwt(private_key, {**claims, "iss": "https://accounts.example.test"})
            with pytest.raises(MeetAutomationInvalid):
                await verify_push_token(verifier, wrong_iss)
            wrong_email = signed_jwt(
                private_key, {**claims, "email": "other@example.iam.gserviceaccount.com"}
            )
            with pytest.raises(MeetAutomationInvalid):
                await verify_push_token(verifier, wrong_email)

    asyncio.run(scenario())
    assert len(handler.requests) == 1
    assert REQUEST_TIMEOUT_SECONDS == 10.0
