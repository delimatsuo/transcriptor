"""Live-shaped Calendar/Meet HTTP adapter and Pub/Sub push JWT verifier.

The runtime does not construct this adapter from process environment or
Google client libraries. Tests inject an httpx client with a mock transport.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Protocol
from urllib.parse import quote, urlencode

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.hashes import SHA256

from backend.workers.meet_transcript_automation import (
    EligibleMeetEventBinding,
    MAX_PROVIDER_BYTES,
    MeetAutomationInvalid,
    MeetAutomationNotFound,
    PROVIDER_PAGE_SIZE,
    PushTokenClaims,
    ResolvedMeetTranscript,
    WorkspaceGrant,
    _TRANSCRIPT_RESOURCE,
)


GOOGLE_OAUTH2_CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"
CALENDAR_API_ROOT = "https://www.googleapis.com/calendar/v3"
MEET_API_ROOT = "https://meet.googleapis.com/v2"
REQUEST_TIMEOUT_SECONDS = 10.0
GOOGLE_PUSH_ISSUER = "https://accounts.google.com"
_MEETING_CODE = re.compile(r"^[A-Za-z0-9_-]{1,512}$")
_JWT_TOKEN = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


class WorkspaceHttpTransport(Protocol):
    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> tuple[int, bytes]: ...


class WorkspaceAccessTokenSource(Protocol):
    async def bearer_token(self, grant: WorkspaceGrant) -> str: ...


class HttpxWorkspaceTransport:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> tuple[int, bytes]:
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
            or timeout_seconds > REQUEST_TIMEOUT_SECONDS
        ):
            raise MeetAutomationInvalid("workspace provider failed")
        try:
            response = await self._client.get(
                url,
                headers=headers,
                timeout=timeout_seconds,
                follow_redirects=False,
            )
        except Exception as exc:
            raise MeetAutomationInvalid("workspace provider failed") from exc
        content = bytes(getattr(response, "content", b""))
        status = int(getattr(response, "status_code", 0))
        if len(content) > MAX_PROVIDER_BYTES:
            raise MeetAutomationInvalid("workspace provider failed")
        return status, content


class StaticAccessTokenSource:
    def __init__(self, token: str) -> None:
        if not isinstance(token, str) or not token or token != token.strip() or len(token) > 4096:
            raise MeetAutomationInvalid("workspace provider failed")
        self._token = token

    async def bearer_token(self, grant: WorkspaceGrant) -> str:
        if not isinstance(grant, WorkspaceGrant):
            raise MeetAutomationNotFound("workspace authority not found")
        return self._token


def create_meet_transcript_provider_runtime(
    *,
    transport: WorkspaceHttpTransport | None = None,
    token_source: WorkspaceAccessTokenSource | None = None,
    expected_audience: str | None = None,
    expected_service_account_email: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple["GoogleWorkspaceMeetProvider", "GooglePubSubPushTokenVerifier"] | None:
    if (
        transport is None
        or token_source is None
        or not isinstance(expected_audience, str)
        or not expected_audience
        or not isinstance(expected_service_account_email, str)
        or not expected_service_account_email
    ):
        return None
    return (
        GoogleWorkspaceMeetProvider(transport, token_source),
        GooglePubSubPushTokenVerifier(
            transport,
            expected_audience=expected_audience,
            expected_service_account_email=expected_service_account_email,
            clock=clock,
        ),
    )


def _json_object(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_PROVIDER_BYTES:
        raise MeetAutomationInvalid("workspace provider failed")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: set[str] = set()
        value: dict[str, Any] = {}
        for key, item in items:
            if not isinstance(key, str) or key in seen:
                raise ValueError("duplicate")
            seen.add(key)
            value[key] = item
        return value

    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except Exception as exc:
        raise MeetAutomationInvalid("workspace provider failed") from exc
    if not isinstance(parsed, dict):
        raise MeetAutomationInvalid("workspace provider failed")
    return parsed


def _subset(value: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    if not set(value).issubset(allowed):
        raise MeetAutomationInvalid("workspace provider failed")
    return value


def _space_name(meet_target: str) -> str:
    prefix = "//meet.googleapis.com/"
    if not meet_target.startswith(prefix):
        raise MeetAutomationInvalid("workspace provider failed")
    return meet_target[len(prefix) :]


class GoogleWorkspaceMeetProvider:
    def __init__(
        self,
        transport: WorkspaceHttpTransport,
        token_source: WorkspaceAccessTokenSource,
    ) -> None:
        self._transport = transport
        self._token_source = token_source

    @staticmethod
    def _assert_authority(grant: WorkspaceGrant, binding: EligibleMeetEventBinding) -> None:
        if (
            grant.grant_id != binding.grant_id
            or grant.owner_id != binding.owner_id
            or grant.org_id != binding.org_id
            or grant.workspace_subject != binding.workspace_subject
        ):
            raise MeetAutomationNotFound("workspace authority not found")

    async def _authorized_get(self, grant: WorkspaceGrant, url: str) -> tuple[int, bytes]:
        token = await self._token_source.bearer_token(grant)
        if not isinstance(token, str) or not token or token != token.strip():
            raise MeetAutomationInvalid("workspace provider failed")
        return await self._transport.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )

    async def resolve_transcript(
        self,
        grant: WorkspaceGrant,
        binding: EligibleMeetEventBinding,
    ) -> ResolvedMeetTranscript:
        self._assert_authority(grant, binding)
        calendar_url = (
            f"{CALENDAR_API_ROOT}/calendars/"
            f"{quote(binding.calendar_id, safe='')}/events/"
            f"{quote(binding.calendar_event_id, safe='')}"
        )
        status, body = await self._authorized_get(grant, calendar_url)
        if status == 404:
            raise MeetAutomationNotFound("eligible Meet event not found")
        if status != 200:
            raise MeetAutomationInvalid("workspace provider failed")
        event = _subset(_json_object(body), {"id", "conferenceData"})
        if event.get("id") != binding.calendar_event_id:
            raise MeetAutomationInvalid("workspace provider failed")
        conference = event.get("conferenceData")
        if not isinstance(conference, dict):
            raise MeetAutomationInvalid("workspace provider failed")
        conference = _subset(conference, {"conferenceId", "conferenceSolution"})
        solution = conference.get("conferenceSolution")
        if not isinstance(solution, dict):
            raise MeetAutomationInvalid("workspace provider failed")
        solution = _subset(solution, {"key"})
        key = solution.get("key")
        if not isinstance(key, dict):
            raise MeetAutomationInvalid("workspace provider failed")
        key = _subset(key, {"type"})
        conference_id = conference.get("conferenceId")
        if (
            key.get("type") != "hangoutsMeet"
            or not isinstance(conference_id, str)
            or _MEETING_CODE.fullmatch(conference_id) is None
        ):
            raise MeetAutomationInvalid("workspace provider failed")
        filter_value = f'space.meeting_code = "{conference_id}"'
        records_url = (
            f"{MEET_API_ROOT}/conferenceRecords?"
            + urlencode({"filter": filter_value, "pageSize": str(PROVIDER_PAGE_SIZE)})
        )
        status, body = await self._authorized_get(grant, records_url)
        if status != 200:
            raise MeetAutomationInvalid("workspace provider failed")
        payload = _subset(_json_object(body), {"conferenceRecords", "nextPageToken"})
        if "nextPageToken" in payload:
            raise MeetAutomationInvalid("workspace provider failed")
        records = payload.get("conferenceRecords")
        if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
            raise MeetAutomationInvalid("workspace provider failed")
        record = _subset(records[0], {"name", "space", "startTime", "endTime", "expireTime"})
        space = record.get("space")
        record_name = record.get("name")
        if space != _space_name(binding.meet_target) or not isinstance(record_name, str):
            raise MeetAutomationInvalid("workspace provider failed")
        if not re.fullmatch(r"conferenceRecords/[A-Za-z0-9_-]{1,512}", record_name):
            raise MeetAutomationInvalid("workspace provider failed")
        transcripts_url = (
            f"{MEET_API_ROOT}/{record_name}/transcripts?"
            + urlencode({"pageSize": str(PROVIDER_PAGE_SIZE)})
        )
        status, body = await self._authorized_get(grant, transcripts_url)
        if status != 200:
            raise MeetAutomationInvalid("workspace provider failed")
        payload = _subset(_json_object(body), {"transcripts", "nextPageToken"})
        if "nextPageToken" in payload:
            raise MeetAutomationInvalid("workspace provider failed")
        transcripts = payload.get("transcripts")
        if not isinstance(transcripts, list):
            raise MeetAutomationInvalid("workspace provider failed")
        generated = []
        for item in transcripts:
            if not isinstance(item, dict):
                raise MeetAutomationInvalid("workspace provider failed")
            item = _subset(
                item, {"name", "state", "startTime", "endTime", "docsDestination"}
            )
            if item.get("state") == "FILE_GENERATED":
                generated.append(item)
        if len(generated) != 1:
            raise MeetAutomationInvalid("workspace provider failed")
        transcript_name = generated[0].get("name")
        if (
            not isinstance(transcript_name, str)
            or _TRANSCRIPT_RESOURCE.fullmatch(transcript_name) is None
            or not transcript_name.startswith(f"{record_name}/transcripts/")
        ):
            raise MeetAutomationInvalid("workspace provider failed")
        return ResolvedMeetTranscript(
            grant_id=binding.grant_id,
            workspace_subject=binding.workspace_subject,
            calendar_id=binding.calendar_id,
            calendar_event_id=binding.calendar_event_id,
            meet_target=binding.meet_target,
            transcript_name=transcript_name,
        )

    async def fetch_transcript_entries_page(
        self,
        grant: WorkspaceGrant,
        binding: EligibleMeetEventBinding,
        transcript_name: str,
        *,
        page_size: int,
        page_token: str | None,
    ) -> bytes:
        self._assert_authority(grant, binding)
        if page_size != PROVIDER_PAGE_SIZE or _TRANSCRIPT_RESOURCE.fullmatch(transcript_name) is None:
            raise MeetAutomationInvalid("workspace provider failed")
        params = {"pageSize": str(PROVIDER_PAGE_SIZE)}
        if page_token is not None:
            if (
                not isinstance(page_token, str)
                or not page_token
                or page_token != page_token.strip()
                or len(page_token) > 2048
            ):
                raise MeetAutomationInvalid("workspace provider failed")
            params["pageToken"] = page_token
        url = f"{MEET_API_ROOT}/{transcript_name}/entries?" + urlencode(params)
        status, body = await self._authorized_get(grant, url)
        if status != 200 or not body:
            raise MeetAutomationInvalid("workspace provider failed")
        return body


class GooglePubSubPushTokenVerifier:
    def __init__(
        self,
        transport: WorkspaceHttpTransport,
        *,
        expected_audience: str,
        expected_service_account_email: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.expected_audience = expected_audience
        self.expected_service_account_email = expected_service_account_email
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._certs: dict[str, str] | None = None

    async def _certs_by_kid(self) -> dict[str, str]:
        if self._certs is not None:
            return self._certs
        status, body = await self._transport.get(
            GOOGLE_OAUTH2_CERTS_URL,
            headers={"Accept": "application/json"},
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )
        if status != 200:
            raise MeetAutomationInvalid("push authentication is invalid")
        payload = _json_object(body)
        certs: dict[str, str] = {}
        for kid, pem in payload.items():
            if not isinstance(kid, str) or not kid or not isinstance(pem, str) or "BEGIN" not in pem:
                raise MeetAutomationInvalid("push authentication is invalid")
            certs[kid] = pem
        if not certs:
            raise MeetAutomationInvalid("push authentication is invalid")
        self._certs = certs
        return certs

    async def verify(self, token: str) -> PushTokenClaims:
        if not isinstance(token, str) or _JWT_TOKEN.fullmatch(token) is None:
            raise MeetAutomationInvalid("push authentication is invalid")
        header_part, payload_part, signature_part = token.split(".")
        try:
            header = json.loads(_b64url_decode(header_part))
            payload = json.loads(_b64url_decode(payload_part))
            signature = _b64url_decode(signature_part)
        except Exception as exc:
            raise MeetAutomationInvalid("push authentication is invalid") from exc
        if not isinstance(header, dict) or not isinstance(payload, dict):
            raise MeetAutomationInvalid("push authentication is invalid")
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid:
            raise MeetAutomationInvalid("push authentication is invalid")
        certs = await self._certs_by_kid()
        pem = certs.get(kid)
        if pem is None:
            raise MeetAutomationInvalid("push authentication is invalid")
        try:
            public_key = x509.load_pem_x509_certificate(pem.encode("ascii")).public_key()
            public_key.verify(
                signature,
                f"{header_part}.{payload_part}".encode("ascii"),
                padding.PKCS1v15(),
                SHA256(),
            )
        except Exception as exc:
            raise MeetAutomationInvalid("push authentication is invalid") from exc
        email_verified = payload.get("email_verified")
        exp = payload.get("exp")
        now = self._clock()
        if (
            payload.get("iss") != GOOGLE_PUSH_ISSUER
            or not isinstance(exp, int)
            or exp <= int(now.timestamp())
            or email_verified is not True
        ):
            raise MeetAutomationInvalid("push authentication is invalid")
        try:
            return PushTokenClaims(
                audience=payload.get("aud"),
                email=payload.get("email"),
                email_verified=email_verified,
            )
        except Exception as exc:
            raise MeetAutomationInvalid("push authentication is invalid") from exc


def _b64url_decode(segment: str) -> bytes:
    padding_chars = "=" * ((4 - len(segment) % 4) % 4)
    return base64.urlsafe_b64decode(segment + padding_chars)
