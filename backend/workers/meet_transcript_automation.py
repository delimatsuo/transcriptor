"""Provider-independent Google Meet transcript automation contracts and worker."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Literal, Protocol, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from backend.sessions.workspace_imports import (
    GoogleMeetImportRequest,
    MeetTranscriptEntry,
    MeetTranscriptSession,
)
from backend.workers.google_meet_import import (
    GoogleMeetImportResult,
    GoogleMeetImportWorker,
    ImportJobStatus,
)


CALENDAR_EVENTS_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/calendar.events.readonly"
)
MEETINGS_SPACE_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/meetings.space.readonly"
)
REQUIRED_WORKSPACE_SCOPES = frozenset(
    {CALENDAR_EVENTS_READONLY_SCOPE, MEETINGS_SPACE_READONLY_SCOPE}
)
MEET_TRANSCRIPT_EVENT_TYPE = "google.workspace.meet.transcript.v2.fileGenerated"
MAX_PUSH_BYTES = 64_000
MAX_PROVIDER_BYTES = 2_000_000
MAX_PROVIDER_PAGES = 4
MAX_PROVIDER_ENTRIES = 400
PROVIDER_PAGE_SIZE = 100
AUTOMATION_DEADLINE_SECONDS = 30.0
MAX_PENDING_PROVIDER_TASKS = 25
MAX_BINDING_CONTEXT_BYTES = 900_000
AUTOMATION_LEASE_DURATION = timedelta(minutes=5)
RECONCILIATION_LEASE_DURATION = timedelta(minutes=5)
AUTOMATION_FAILURE_REASONS = frozenset({"automation_failed"})
AutomationFailureReason = Literal["automation_failed"]

_ProviderResult = TypeVar("_ProviderResult")

_EXACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,511}$")
_CLOUD_EVENT_ID = re.compile(r"^[^\s\x00-\x1f\x7f]{1,1024}$")
_CALENDAR_ID = re.compile(r"^[^\s/][^\s]{0,1023}$")
_MEET_TARGET = re.compile(r"^//meet\.googleapis\.com/spaces/[A-Za-z0-9_-]{1,512}$")
_WORKSPACE_SOURCE = re.compile(
    r"^//workspaceevents\.googleapis\.com/subscriptions/[A-Za-z0-9_-]{1,512}$"
)
_PUBSUB_SUBSCRIPTION = re.compile(
    r"^projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/subscriptions/[A-Za-z0-9._~-]{1,255}$"
)
_TRANSCRIPT_RESOURCE = re.compile(
    r"^conferenceRecords/[A-Za-z0-9_-]{1,512}/transcripts/[A-Za-z0-9_-]{1,512}$"
)
_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class MeetAutomationError(ValueError):
    """Base class for fixed, content-free automation failures."""


class MeetAutomationInvalid(MeetAutomationError):
    """The supplied synthetic envelope or provider page is invalid."""


class MeetAutomationNotFound(MeetAutomationError):
    """The exact tenant-scoped authority or binding was not found."""


class MeetAutomationConflict(MeetAutomationError):
    """Durable identity or lease state conflicts with this attempt."""


class MeetAutomationUnavailable(MeetAutomationError):
    """An offline dependency is not configured."""


def _exact_text(value: str, *, label: str, pattern: re.Pattern[str] | None = None) -> str:
    if not value or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{label} is invalid")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _aware(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value


def _canonical_hash(*parts: str) -> str:
    encoded = json.dumps(list(parts), ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def grant_identity_key(*, owner_id: str, org_id: str, grant_id: str) -> str:
    return _canonical_hash("workspace-grant-v1", owner_id, org_id, grant_id)


def eligible_binding_key(binding: "EligibleMeetEventBinding") -> str:
    return _canonical_hash(
        "eligible-meet-event-v1",
        binding.owner_id,
        binding.org_id,
        binding.grant_id,
        binding.workspace_subject,
        binding.calendar_id,
        binding.calendar_event_id,
        binding.meet_target,
        binding.workspace_subscription_source,
        binding.pubsub_subscription,
    )


def manual_binding_lookup_key(
    *, owner_id: str, org_id: str, grant_id: str, calendar_id: str, calendar_event_id: str
) -> str:
    return _canonical_hash(
        "eligible-meet-manual-v1",
        owner_id,
        org_id,
        grant_id,
        calendar_id,
        calendar_event_id,
    )


def push_binding_lookup_key(
    *, workspace_subscription_source: str, pubsub_subscription: str, meet_target: str
) -> str:
    return _canonical_hash(
        "eligible-meet-push-v1",
        workspace_subscription_source,
        pubsub_subscription,
        meet_target,
    )


def automation_event_key(*, workspace_subscription_source: str, event_id: str) -> str:
    return _canonical_hash(
        "meet-automation-event-v1", workspace_subscription_source, event_id
    )


class WorkspaceGrantStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class WorkspaceGrant(BaseModel):
    """Credential-free delegated Workspace authority metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    grant_id: str = Field(min_length=1, max_length=512)
    owner_id: str = Field(min_length=1, max_length=512)
    org_id: str = Field(min_length=1, max_length=512)
    workspace_subject: str = Field(min_length=3, max_length=512)
    status: WorkspaceGrantStatus
    scopes: frozenset[str] = Field(min_length=1, max_length=8)
    valid_from: datetime
    expires_at: datetime
    updated_at: datetime

    @field_validator("grant_id", "owner_id", "org_id")
    @classmethod
    def exact_ids(cls, value: str, info) -> str:
        return _exact_text(value, label=info.field_name, pattern=_EXACT_ID)

    @field_validator("workspace_subject")
    @classmethod
    def exact_subject(cls, value: str) -> str:
        value = _exact_text(value, label="workspace_subject")
        if value.count("@") != 1:
            raise ValueError("workspace_subject is invalid")
        return value

    @field_validator("scopes")
    @classmethod
    def exact_scopes(cls, value: frozenset[str]) -> frozenset[str]:
        for scope in value:
            _exact_text(scope, label="scope")
        return value

    @field_validator("valid_from", "expires_at", "updated_at")
    @classmethod
    def aware_times(cls, value: datetime, info) -> datetime:
        return _aware(value, label=info.field_name)

    @model_validator(mode="after")
    def ordered_validity(self) -> "WorkspaceGrant":
        if self.expires_at <= self.valid_from:
            raise ValueError("grant validity is invalid")
        return self

    def assert_authorized(self, *, owner_id: str, org_id: str, now: datetime) -> None:
        if self.owner_id != owner_id or self.org_id != org_id:
            raise MeetAutomationNotFound("workspace authority not found")
        if (
            self.status != WorkspaceGrantStatus.ACTIVE
            or now < self.valid_from
            or now >= self.expires_at
            or self.scopes != REQUIRED_WORKSPACE_SCOPES
        ):
            raise MeetAutomationNotFound("workspace authority not found")


class EligibleMeetEventBinding(BaseModel):
    """One explicit, tenant-scoped Calendar event admitted for Meet import."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    grant_id: str = Field(min_length=1, max_length=512)
    owner_id: str = Field(min_length=1, max_length=512)
    org_id: str = Field(min_length=1, max_length=512)
    workspace_subject: str = Field(min_length=3, max_length=512)
    calendar_id: str = Field(min_length=1, max_length=1024)
    calendar_event_id: str = Field(min_length=1, max_length=512)
    meet_target: str = Field(min_length=1, max_length=1024)
    workspace_subscription_source: str = Field(min_length=1, max_length=1024)
    pubsub_subscription: str = Field(min_length=1, max_length=1024)
    title: str = Field(min_length=1, max_length=240)
    notice_given: bool
    notice_provenance: str = Field(min_length=1, max_length=1000)
    candidate_id: str | None = Field(default=None, max_length=512)
    candidate_name: str | None = Field(default=None, max_length=300)
    resume_artifact_id: str | None = Field(default=None, max_length=512)
    resume_text: str | None = Field(default=None, max_length=500_000)
    job_description_artifact_id: str | None = Field(default=None, max_length=512)
    job_description_text: str | None = Field(default=None, max_length=500_000)
    briefing: str | None = Field(default=None, max_length=100_000)
    created_at: datetime

    @field_validator("grant_id", "owner_id", "org_id", "calendar_event_id")
    @classmethod
    def exact_ids(cls, value: str, info) -> str:
        return _exact_text(value, label=info.field_name, pattern=_EXACT_ID)

    @field_validator("workspace_subject")
    @classmethod
    def subject(cls, value: str) -> str:
        value = _exact_text(value, label="workspace_subject")
        if value.count("@") != 1:
            raise ValueError("workspace_subject is invalid")
        return value

    @field_validator("calendar_id")
    @classmethod
    def calendar(cls, value: str) -> str:
        return _exact_text(value, label="calendar_id", pattern=_CALENDAR_ID)

    @field_validator("meet_target")
    @classmethod
    def target(cls, value: str) -> str:
        return _exact_text(value, label="meet_target", pattern=_MEET_TARGET)

    @field_validator("workspace_subscription_source")
    @classmethod
    def source(cls, value: str) -> str:
        return _exact_text(value, label="workspace_subscription_source", pattern=_WORKSPACE_SOURCE)

    @field_validator("pubsub_subscription")
    @classmethod
    def subscription(cls, value: str) -> str:
        return _exact_text(value, label="pubsub_subscription", pattern=_PUBSUB_SUBSCRIPTION)

    @field_validator("title", "notice_provenance", "candidate_id", "candidate_name", "resume_artifact_id", "job_description_artifact_id")
    @classmethod
    def exact_context_ids(cls, value: str | None, info) -> str | None:
        return None if value is None else _exact_text(value, label=info.field_name)

    @field_validator("resume_text", "job_description_text", "briefing")
    @classmethod
    def visible_context(cls, value: str | None, info) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError(f"{info.field_name} is invalid")
        return value

    @field_validator("created_at")
    @classmethod
    def aware_created(cls, value: datetime) -> datetime:
        return _aware(value, label="created_at")

    @model_validator(mode="after")
    def paired_context(self) -> "EligibleMeetEventBinding":
        if (self.resume_artifact_id is None) != (self.resume_text is None):
            raise ValueError("resume context is incomplete")
        if (self.job_description_artifact_id is None) != (
            self.job_description_text is None
        ):
            raise ValueError("job description context is incomplete")
        context_bytes = sum(
            len((value or "").encode("utf-8"))
            for value in (
                self.resume_text,
                self.job_description_text,
                self.briefing,
            )
        )
        if context_bytes > MAX_BINDING_CONTEXT_BYTES:
            raise ValueError(
                f"binding context exceeds {MAX_BINDING_CONTEXT_BYTES} UTF-8 bytes"
            )
        return self


class ResolvedMeetTranscript(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    grant_id: str
    workspace_subject: str
    calendar_id: str
    calendar_event_id: str
    meet_target: str
    transcript_name: str

    @field_validator("transcript_name")
    @classmethod
    def transcript_resource(cls, value: str) -> str:
        return _exact_text(value, label="transcript_name", pattern=_TRANSCRIPT_RESOURCE)


class WorkspaceTranscriptProvider(Protocol):
    async def resolve_transcript(
        self,
        grant: WorkspaceGrant,
        binding: EligibleMeetEventBinding,
    ) -> ResolvedMeetTranscript: ...

    async def fetch_transcript_entries_page(
        self,
        grant: WorkspaceGrant,
        binding: EligibleMeetEventBinding,
        transcript_name: str,
        *,
        page_size: int,
        page_token: str | None,
    ) -> bytes: ...


class PushTokenClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    audience: str
    email: str
    email_verified: StrictBool


class WorkspacePushTokenVerifier(Protocol):
    expected_audience: str
    expected_service_account_email: str

    async def verify(self, token: str) -> PushTokenClaims | dict[str, Any]: ...


class ParsedMeetTranscriptEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    event_time: datetime
    workspace_subscription_source: str
    meet_target: str
    pubsub_subscription: str
    transcript_name: str

    @field_validator("event_id")
    @classmethod
    def event_identity(cls, value: str) -> str:
        return _exact_text(value, label="event_id", pattern=_CLOUD_EVENT_ID)

    @field_validator("event_time")
    @classmethod
    def event_timestamp(cls, value: datetime) -> datetime:
        return _aware(value, label="event_time")


class ManualMeetTranscriptSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    grant_id: str = Field(alias="grantId", min_length=1, max_length=512)
    calendar_id: str = Field(alias="calendarId", min_length=1, max_length=1024)
    calendar_event_id: str = Field(alias="calendarEventId", min_length=1, max_length=512)

    @field_validator("grant_id", "calendar_event_id")
    @classmethod
    def ids(cls, value: str, info) -> str:
        return _exact_text(value, label=info.field_name, pattern=_EXACT_ID)

    @field_validator("calendar_id")
    @classmethod
    def calendar(cls, value: str) -> str:
        return _exact_text(value, label="calendar_id", pattern=_CALENDAR_ID)


class MeetTranscriptReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    grant_id: str = Field(alias="grantId", min_length=1, max_length=512)
    max_events: int = Field(alias="maxEvents", ge=1, le=25)

    @field_validator("grant_id")
    @classmethod
    def grant(cls, value: str) -> str:
        return _exact_text(value, label="grant_id", pattern=_EXACT_ID)


class AutomationEventStatus(str, Enum):
    QUEUED = "queued"
    LEASED = "leased"
    COMPLETED = "completed"
    FAILED = "failed"


class AutomationEventState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_key: str
    event_id: str
    trigger: Literal["webhook", "manual", "reconcile"]
    owner_id: str
    org_id: str
    grant_id: str
    workspace_subject: str
    calendar_id: str
    calendar_event_id: str
    meet_target: str
    workspace_subscription_source: str
    pubsub_subscription: str
    transcript_name: str
    status: AutomationEventStatus
    version: int = Field(ge=1)
    attempt_count: int = Field(ge=0)
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    reason_code: AutomationFailureReason | None = None
    import_session_id: str | None = None
    import_source_key: str | None = None
    import_source_digest: str | None = None
    import_segment_count: int = Field(default=0, ge=0)
    import_attempt_count: int = Field(default=0, ge=0)
    updated_at: datetime


class AutomationEventClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: AutomationEventState
    idempotent_replay: bool = False


class AutomationRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    source_key: str
    source_digest: str
    status: ImportJobStatus
    segment_count: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    idempotent_replay: bool
    automation_replay: bool


class ReconciliationLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_key: str
    owner_id: str
    org_id: str
    grant_id: str
    version: int = Field(ge=1)
    attempt_count: int = Field(ge=1)
    lease_token: str
    lease_expires_at: datetime
    updated_at: datetime
    cursor_binding_key: str | None = None

    @field_validator("cursor_binding_key")
    @classmethod
    def canonical_cursor(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("reconciliation cursor is invalid")
        return value


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    considered: int = Field(ge=0, le=25)
    completed: int = Field(ge=0, le=25)
    replayed: int = Field(ge=0, le=25)
    failed: int = Field(ge=0, le=25)
    reason_codes: list[str] = Field(max_length=4)


class MeetTranscriptAutomationStorage(Protocol):
    async def get_workspace_grant(
        self, grant_id: str, *, owner_id: str, org_id: str
    ) -> WorkspaceGrant: ...

    async def get_eligible_meet_binding_manual(
        self,
        *,
        owner_id: str,
        org_id: str,
        grant_id: str,
        calendar_id: str,
        calendar_event_id: str,
    ) -> EligibleMeetEventBinding: ...

    async def get_eligible_meet_binding_push(
        self,
        *,
        workspace_subscription_source: str,
        pubsub_subscription: str,
        meet_target: str,
    ) -> EligibleMeetEventBinding: ...

    async def list_eligible_meet_bindings(
        self,
        *,
        owner_id: str,
        org_id: str,
        grant_id: str,
        limit: int,
        after_binding_key: str | None,
    ) -> list[EligibleMeetEventBinding]: ...

    async def queue_meet_automation_event(
        self, event: AutomationEventState
    ) -> AutomationEventState: ...

    async def claim_meet_automation_event(
        self,
        *,
        event_key: str,
        lease_token: str,
        lease_expires_at: datetime,
        updated_at: datetime,
    ) -> AutomationEventClaim: ...

    async def complete_meet_automation_event(
        self,
        result: GoogleMeetImportResult,
        *,
        event_key: str,
        lease_token: str,
        version: int,
        updated_at: datetime,
    ) -> AutomationEventState: ...

    async def fail_meet_automation_event(
        self,
        *,
        event_key: str,
        lease_token: str,
        version: int,
        reason_code: AutomationFailureReason,
        updated_at: datetime,
    ) -> AutomationEventState: ...

    async def claim_meet_reconciliation(
        self,
        *,
        owner_id: str,
        org_id: str,
        grant_id: str,
        lease_token: str,
        lease_expires_at: datetime,
        updated_at: datetime,
    ) -> ReconciliationLease: ...

    async def release_meet_reconciliation(
        self,
        *,
        scope_key: str,
        lease_token: str,
        version: int,
        updated_at: datetime,
        last_considered_binding_key: str | None,
    ) -> None: ...


def _strict_json(raw: bytes, *, max_bytes: int, label: str) -> Any:
    if len(raw) > max_bytes:
        raise MeetAutomationInvalid(f"{label} is invalid")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MeetAutomationInvalid(f"{label} is invalid") from exc

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise MeetAutomationInvalid(f"{label} is invalid")
            result[key] = value
        return result

    def constant(_value: str) -> None:
        raise MeetAutomationInvalid(f"{label} is invalid")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MeetAutomationInvalid(f"{label} is invalid") from exc


def parse_push_bearer(raw_headers: list[tuple[bytes, bytes]]) -> str:
    values = [value for key, value in raw_headers if key.lower() == b"authorization"]
    if len(values) != 1 or len(values[0]) > 4103:
        raise MeetAutomationInvalid("push authentication is invalid")
    try:
        value = values[0].decode("ascii")
    except UnicodeDecodeError as exc:
        raise MeetAutomationInvalid("push authentication is invalid") from exc
    match = re.fullmatch(r"Bearer ([A-Za-z0-9._~-]{1,4096})", value)
    if match is None:
        raise MeetAutomationInvalid("push authentication is invalid")
    return match.group(1)


async def verify_push_token(
    verifier: WorkspacePushTokenVerifier, token: str
) -> PushTokenClaims:
    try:
        raw_claims = await verifier.verify(token)
        claims = PushTokenClaims.model_validate(raw_claims)
    except Exception as exc:
        raise MeetAutomationInvalid("push authentication is invalid") from exc
    try:
        expected_audience = _exact_text(
            verifier.expected_audience, label="expected push audience"
        )
        expected_email = _exact_text(
            verifier.expected_service_account_email,
            label="expected push service account",
        )
    except Exception as exc:
        raise MeetAutomationInvalid("push authentication is invalid") from exc
    if (
        claims.audience != expected_audience
        or claims.email != expected_email
        or claims.email_verified is not True
    ):
        raise MeetAutomationInvalid("push authentication is invalid")
    return claims


def parse_meet_transcript_push(raw: bytes) -> ParsedMeetTranscriptEvent:
    value = _strict_json(raw, max_bytes=MAX_PUSH_BYTES, label="push envelope")
    if not isinstance(value, dict) or set(value) != {"message", "subscription"}:
        raise MeetAutomationInvalid("push envelope is invalid")
    message = value["message"]
    if not isinstance(message, dict) or not set(message).issubset(
        {"attributes", "data", "messageId", "publishTime", "orderingKey"}
    ) or not {"attributes", "data", "messageId", "publishTime"}.issubset(message):
        raise MeetAutomationInvalid("push envelope is invalid")
    subscription = value["subscription"]
    if not isinstance(subscription, str) or _PUBSUB_SUBSCRIPTION.fullmatch(subscription) is None:
        raise MeetAutomationInvalid("push envelope is invalid")
    message_id = message["messageId"]
    publish_time = message["publishTime"]
    ordering_key = message.get("orderingKey")
    if (
        not isinstance(message_id, str)
        or _CLOUD_EVENT_ID.fullmatch(message_id) is None
        or not isinstance(publish_time, str)
        or _RFC3339.fullmatch(publish_time) is None
        or (
            ordering_key is not None
            and (
                not isinstance(ordering_key, str)
                or len(ordering_key) > 1024
                or ordering_key != ordering_key.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in ordering_key)
            )
        )
    ):
        raise MeetAutomationInvalid("push envelope is invalid")
    try:
        _aware(
            datetime.fromisoformat(publish_time.replace("Z", "+00:00")),
            label="publish_time",
        )
    except ValueError as exc:
        raise MeetAutomationInvalid("push envelope is invalid") from exc
    attributes = message["attributes"]
    expected_attributes = {
        "ce-datacontenttype",
        "ce-id",
        "ce-source",
        "ce-specversion",
        "ce-subject",
        "ce-time",
        "ce-type",
    }
    if not isinstance(attributes, dict) or set(attributes) != expected_attributes:
        raise MeetAutomationInvalid("push envelope is invalid")
    if (
        attributes["ce-specversion"] != "1.0"
        or attributes["ce-datacontenttype"] != "application/json"
        or attributes["ce-type"] != MEET_TRANSCRIPT_EVENT_TYPE
        or not all(isinstance(item, str) for item in attributes.values())
        or _WORKSPACE_SOURCE.fullmatch(attributes["ce-source"]) is None
        or _MEET_TARGET.fullmatch(attributes["ce-subject"]) is None
        or _CLOUD_EVENT_ID.fullmatch(attributes["ce-id"]) is None
        or _RFC3339.fullmatch(attributes["ce-time"]) is None
    ):
        raise MeetAutomationInvalid("push envelope is invalid")
    try:
        event_time = datetime.fromisoformat(attributes["ce-time"].replace("Z", "+00:00"))
        event_time = _aware(event_time, label="event_time")
    except ValueError as exc:
        raise MeetAutomationInvalid("push envelope is invalid") from exc
    data = message["data"]
    if not isinstance(data, str) or not data:
        raise MeetAutomationInvalid("push envelope is invalid")
    try:
        decoded = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MeetAutomationInvalid("push envelope is invalid") from exc
    payload = _strict_json(decoded, max_bytes=16_000, label="push event data")
    if (
        not isinstance(payload, dict)
        or set(payload) != {"transcript"}
        or not isinstance(payload["transcript"], dict)
        or set(payload["transcript"]) != {"name"}
        or not isinstance(payload["transcript"]["name"], str)
        or _TRANSCRIPT_RESOURCE.fullmatch(payload["transcript"]["name"]) is None
    ):
        raise MeetAutomationInvalid("push event data is invalid")
    return ParsedMeetTranscriptEvent(
        event_id=attributes["ce-id"],
        event_time=event_time,
        workspace_subscription_source=attributes["ce-source"],
        meet_target=attributes["ce-subject"],
        pubsub_subscription=subscription,
        transcript_name=payload["transcript"]["name"],
    )


def parse_manual_sync_request(raw: bytes) -> ManualMeetTranscriptSyncRequest:
    return ManualMeetTranscriptSyncRequest.model_validate(
        _strict_json(raw, max_bytes=8_000, label="manual sync request")
    )


def parse_reconciliation_request(raw: bytes) -> MeetTranscriptReconcileRequest:
    return MeetTranscriptReconcileRequest.model_validate(
        _strict_json(raw, max_bytes=8_000, label="reconciliation request")
    )


class MeetTranscriptAutomationOrchestrator:
    """Converge webhook, manual, and reconcile triggers on one import worker."""

    def __init__(
        self,
        storage: MeetTranscriptAutomationStorage,
        provider: WorkspaceTranscriptProvider,
        import_worker: GoogleMeetImportWorker,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        deadline_seconds: float = AUTOMATION_DEADLINE_SECONDS,
    ) -> None:
        if (
            not isinstance(deadline_seconds, (int, float))
            or isinstance(deadline_seconds, bool)
            or deadline_seconds <= 0
            or deadline_seconds > AUTOMATION_DEADLINE_SECONDS
        ):
            raise ValueError("automation deadline is invalid")
        self._storage = storage
        self._provider = provider
        self._import_worker = import_worker
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._deadline_seconds = float(deadline_seconds)
        self._pending_provider_tasks: set[asyncio.Task[Any]] = set()

    def _provider_task_done(self, task: asyncio.Task[Any]) -> None:
        self._pending_provider_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.exception()
        except BaseException:
            pass

    async def _await_provider(
        self,
        operation: Callable[[], Awaitable[_ProviderResult]],
        *,
        deadline: float,
    ) -> _ProviderResult:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise MeetAutomationInvalid("workspace provider deadline exceeded")
        if len(self._pending_provider_tasks) >= MAX_PENDING_PROVIDER_TASKS:
            raise MeetAutomationInvalid("workspace provider capacity exceeded")
        task = asyncio.create_task(operation())
        self._pending_provider_tasks.add(task)
        task.add_done_callback(self._provider_task_done)
        try:
            done, _pending = await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            task.cancel()
            raise
        if not done:
            task.cancel()
            raise MeetAutomationInvalid("workspace provider deadline exceeded")
        if task.cancelled():
            raise MeetAutomationInvalid("workspace provider failed")
        return task.result()

    async def _authorized_grant(
        self, binding: EligibleMeetEventBinding, *, owner_id: str, org_id: str
    ) -> WorkspaceGrant:
        now = self._clock()
        grant = await self._storage.get_workspace_grant(
            binding.grant_id, owner_id=owner_id, org_id=org_id
        )
        grant.assert_authorized(owner_id=owner_id, org_id=org_id, now=now)
        if (
            grant.grant_id != binding.grant_id
            or grant.workspace_subject != binding.workspace_subject
        ):
            raise MeetAutomationNotFound("workspace authority not found")
        return grant

    @staticmethod
    def _assert_resolution(
        binding: EligibleMeetEventBinding, resolution: ResolvedMeetTranscript
    ) -> None:
        if (
            resolution.grant_id != binding.grant_id
            or resolution.workspace_subject != binding.workspace_subject
            or resolution.calendar_id != binding.calendar_id
            or resolution.calendar_event_id != binding.calendar_event_id
            or resolution.meet_target != binding.meet_target
        ):
            raise MeetAutomationNotFound("eligible Meet event not found")

    async def _fetch_entries(
        self,
        grant: WorkspaceGrant,
        binding: EligibleMeetEventBinding,
        transcript_name: str,
        *,
        deadline: float,
    ) -> list[MeetTranscriptEntry]:
        entries: dict[str, MeetTranscriptEntry] = {}
        page_token: str | None = None
        seen_tokens: set[str] = set()
        total_bytes = 0
        entry_resource = re.compile(
            rf"^{re.escape(transcript_name)}/entries/[A-Za-z0-9_-]{{1,512}}$"
        )
        for page_number in range(1, MAX_PROVIDER_PAGES + 1):
            if self._monotonic() >= deadline:
                raise MeetAutomationInvalid("transcript pagination is invalid")
            raw = await self._await_provider(
                lambda: self._provider.fetch_transcript_entries_page(
                    grant,
                    binding,
                    transcript_name,
                    page_size=PROVIDER_PAGE_SIZE,
                    page_token=page_token,
                ),
                deadline=deadline,
            )
            if not isinstance(raw, bytes):
                raise MeetAutomationInvalid("transcript pagination is invalid")
            total_bytes += len(raw)
            if total_bytes > MAX_PROVIDER_BYTES or self._monotonic() >= deadline:
                raise MeetAutomationInvalid("transcript pagination is invalid")
            value = _strict_json(raw, max_bytes=MAX_PROVIDER_BYTES, label="transcript page")
            if not isinstance(value, dict) or not set(value).issubset(
                {"transcriptEntries", "nextPageToken"}
            ) or "transcriptEntries" not in value:
                raise MeetAutomationInvalid("transcript pagination is invalid")
            page_entries = value["transcriptEntries"]
            if not isinstance(page_entries, list) or len(page_entries) > PROVIDER_PAGE_SIZE:
                raise MeetAutomationInvalid("transcript pagination is invalid")
            for raw_entry in page_entries:
                try:
                    entry = MeetTranscriptEntry.model_validate(raw_entry)
                except Exception as exc:
                    raise MeetAutomationInvalid("transcript pagination is invalid") from exc
                if entry_resource.fullmatch(entry.name) is None:
                    raise MeetAutomationInvalid("transcript pagination is invalid")
                existing = entries.get(entry.name)
                if existing is not None and existing != entry:
                    raise MeetAutomationInvalid("transcript pagination is invalid")
                entries.setdefault(entry.name, entry)
                if len(entries) > MAX_PROVIDER_ENTRIES:
                    raise MeetAutomationInvalid("transcript pagination is invalid")
            next_token = value.get("nextPageToken")
            if next_token is None:
                break
            if (
                not isinstance(next_token, str)
                or not next_token
                or next_token != next_token.strip()
                or len(next_token) > 2048
                or next_token in seen_tokens
                or page_number == MAX_PROVIDER_PAGES
            ):
                raise MeetAutomationInvalid("transcript pagination is invalid")
            seen_tokens.add(next_token)
            page_token = next_token
        if not entries:
            raise MeetAutomationInvalid("transcript pagination is invalid")
        return list(entries.values())

    @staticmethod
    def _queued_event(
        *,
        binding: EligibleMeetEventBinding,
        trigger: Literal["webhook", "manual", "reconcile"],
        event_id: str,
        transcript_name: str,
        updated_at: datetime,
    ) -> AutomationEventState:
        return AutomationEventState(
            event_key=automation_event_key(
                workspace_subscription_source=binding.workspace_subscription_source,
                event_id=event_id,
            ),
            event_id=event_id,
            trigger=trigger,
            owner_id=binding.owner_id,
            org_id=binding.org_id,
            grant_id=binding.grant_id,
            workspace_subject=binding.workspace_subject,
            calendar_id=binding.calendar_id,
            calendar_event_id=binding.calendar_event_id,
            meet_target=binding.meet_target,
            workspace_subscription_source=binding.workspace_subscription_source,
            pubsub_subscription=binding.pubsub_subscription,
            transcript_name=transcript_name,
            status=AutomationEventStatus.QUEUED,
            version=1,
            attempt_count=0,
            updated_at=updated_at,
        )

    @staticmethod
    def _replay_result(event: AutomationEventState) -> AutomationRunResult:
        if not all(
            (event.import_session_id, event.import_source_key, event.import_source_digest)
        ):
            raise MeetAutomationConflict("automation completion is invalid")
        return AutomationRunResult(
            session_id=event.import_session_id or "",
            source_key=event.import_source_key or "",
            source_digest=event.import_source_digest or "",
            status=ImportJobStatus.COMPLETED,
            segment_count=event.import_segment_count,
            attempt_count=event.import_attempt_count,
            idempotent_replay=True,
            automation_replay=True,
        )

    async def _run_exact_transcript(
        self,
        *,
        grant: WorkspaceGrant,
        binding: EligibleMeetEventBinding,
        trigger: Literal["webhook", "manual", "reconcile"],
        event_id: str,
        transcript_name: str,
        deadline: float,
        resolve_after_claim: bool = False,
    ) -> AutomationRunResult:
        queued = self._queued_event(
            binding=binding,
            trigger=trigger,
            event_id=event_id,
            transcript_name=transcript_name,
            updated_at=self._clock(),
        )
        await self._storage.queue_meet_automation_event(queued)
        token = secrets.token_urlsafe(32)
        claim_time = self._clock()
        claim = await self._storage.claim_meet_automation_event(
            event_key=queued.event_key,
            lease_token=token,
            lease_expires_at=claim_time + AUTOMATION_LEASE_DURATION,
            updated_at=claim_time,
        )
        if claim.event.status == AutomationEventStatus.COMPLETED:
            return self._replay_result(claim.event)
        try:
            if resolve_after_claim:
                resolution = await self._await_provider(
                    lambda: self._provider.resolve_transcript(grant, binding),
                    deadline=deadline,
                )
                self._assert_resolution(binding, resolution)
                if resolution.transcript_name != transcript_name:
                    raise MeetAutomationInvalid("webhook transcript identity mismatch")
            entries = await self._fetch_entries(
                grant, binding, transcript_name, deadline=deadline
            )
            if self._monotonic() >= deadline:
                raise MeetAutomationInvalid("transcript automation deadline exceeded")
            request = GoogleMeetImportRequest(
                sourceType="GOOGLE_MEET",
                sourceArtifactId=transcript_name,
                title=binding.title,
                noticeGiven=binding.notice_given,
                noticeProvenance=binding.notice_provenance,
                transcriptSessions=[
                    MeetTranscriptSession(name=transcript_name, entries=entries)
                ],
                candidateId=binding.candidate_id,
                candidateName=binding.candidate_name,
                resumeArtifactId=binding.resume_artifact_id,
                resumeText=binding.resume_text,
                jobDescriptionArtifactId=binding.job_description_artifact_id,
                jobDescriptionText=binding.job_description_text,
                briefing=binding.briefing,
            )
            result = await self._import_worker.run(
                request,
                owner_id=binding.owner_id,
                org_id=binding.org_id,
            )
            if self._monotonic() >= deadline:
                raise MeetAutomationInvalid("transcript automation deadline exceeded")
            if result.status != ImportJobStatus.COMPLETED:
                raise MeetAutomationConflict("transcript import did not complete")
            completed = await self._storage.complete_meet_automation_event(
                result,
                event_key=queued.event_key,
                lease_token=token,
                version=claim.event.version,
                updated_at=self._clock(),
            )
            return AutomationRunResult(
                **result.model_dump(),
                automation_replay=False,
            )
        except Exception:
            try:
                await self._storage.fail_meet_automation_event(
                    event_key=queued.event_key,
                    lease_token=token,
                    version=claim.event.version,
                    reason_code="automation_failed",
                    updated_at=self._clock(),
                )
            except Exception:
                pass
            raise

    async def process_webhook(
        self, event: ParsedMeetTranscriptEvent
    ) -> AutomationRunResult:
        deadline = self._monotonic() + self._deadline_seconds
        binding = await self._storage.get_eligible_meet_binding_push(
            workspace_subscription_source=event.workspace_subscription_source,
            pubsub_subscription=event.pubsub_subscription,
            meet_target=event.meet_target,
        )
        grant = await self._authorized_grant(
            binding, owner_id=binding.owner_id, org_id=binding.org_id
        )
        return await self._run_exact_transcript(
            grant=grant,
            binding=binding,
            trigger="webhook",
            event_id=event.event_id,
            transcript_name=event.transcript_name,
            deadline=deadline,
            resolve_after_claim=True,
        )

    async def process_manual(
        self,
        request: ManualMeetTranscriptSyncRequest,
        *,
        owner_id: str,
        org_id: str,
    ) -> AutomationRunResult:
        deadline = self._monotonic() + self._deadline_seconds
        binding = await self._storage.get_eligible_meet_binding_manual(
            owner_id=owner_id,
            org_id=org_id,
            grant_id=request.grant_id,
            calendar_id=request.calendar_id,
            calendar_event_id=request.calendar_event_id,
        )
        grant = await self._authorized_grant(binding, owner_id=owner_id, org_id=org_id)
        resolution = await self._await_provider(
            lambda: self._provider.resolve_transcript(grant, binding),
            deadline=deadline,
        )
        if self._monotonic() >= deadline:
            raise MeetAutomationInvalid("transcript automation deadline exceeded")
        self._assert_resolution(binding, resolution)
        event_id = _canonical_hash(
            "manual-v1", eligible_binding_key(binding), resolution.transcript_name
        )
        return await self._run_exact_transcript(
            grant=grant,
            binding=binding,
            trigger="manual",
            event_id=event_id,
            transcript_name=resolution.transcript_name,
            deadline=deadline,
        )

    async def reconcile(
        self,
        request: MeetTranscriptReconcileRequest,
        *,
        owner_id: str,
        org_id: str,
    ) -> ReconciliationResult:
        deadline = self._monotonic() + self._deadline_seconds
        now = self._clock()
        grant = await self._storage.get_workspace_grant(
            request.grant_id, owner_id=owner_id, org_id=org_id
        )
        grant.assert_authorized(owner_id=owner_id, org_id=org_id, now=now)
        lease_token = secrets.token_urlsafe(32)
        lease = await self._storage.claim_meet_reconciliation(
            owner_id=owner_id,
            org_id=org_id,
            grant_id=request.grant_id,
            lease_token=lease_token,
            lease_expires_at=now + RECONCILIATION_LEASE_DURATION,
            updated_at=now,
        )
        considered = completed = replayed = failed = 0
        last_considered_binding_key: str | None = None
        reasons: set[str] = set()
        try:
            bindings = await self._storage.list_eligible_meet_bindings(
                owner_id=owner_id,
                org_id=org_id,
                grant_id=request.grant_id,
                limit=request.max_events,
                after_binding_key=lease.cursor_binding_key,
            )
            for binding in bindings:
                if considered >= request.max_events or self._monotonic() >= deadline:
                    reasons.add("deadline_reached")
                    break
                considered += 1
                last_considered_binding_key = eligible_binding_key(binding)
                try:
                    binding_grant = await self._authorized_grant(
                        binding, owner_id=owner_id, org_id=org_id
                    )
                    resolution = await self._await_provider(
                        lambda: self._provider.resolve_transcript(
                            binding_grant, binding
                        ),
                        deadline=deadline,
                    )
                    self._assert_resolution(binding, resolution)
                    event_id = _canonical_hash(
                        "reconcile-v1",
                        eligible_binding_key(binding),
                        resolution.transcript_name,
                    )
                    result = await self._run_exact_transcript(
                        grant=binding_grant,
                        binding=binding,
                        trigger="reconcile",
                        event_id=event_id,
                        transcript_name=resolution.transcript_name,
                        deadline=deadline,
                    )
                    completed += 1
                    if result.automation_replay:
                        replayed += 1
                except Exception:
                    failed += 1
                    reasons.add("event_failed")
            return ReconciliationResult(
                considered=considered,
                completed=completed,
                replayed=replayed,
                failed=failed,
                reason_codes=sorted(reasons),
            )
        finally:
            await self._storage.release_meet_reconciliation(
                scope_key=lease.scope_key,
                lease_token=lease.lease_token,
                version=lease.version,
                updated_at=self._clock(),
                last_considered_binding_key=last_considered_binding_key,
            )
