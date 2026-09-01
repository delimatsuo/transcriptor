from __future__ import annotations

import asyncio
import base64
import copy
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from backend import main as backend_main
from backend.auth import AuthContext, reset_current_auth, set_current_auth
from backend.storage.firestore import FirestoreStorage
from backend.workers.google_meet_import import (
    GoogleMeetImportResult,
    GoogleMeetImportWorker,
    ImportJobStatus,
)
from backend.workers.meet_transcript_automation import (
    CALENDAR_EVENTS_READONLY_SCOPE,
    MEETINGS_SPACE_READONLY_SCOPE,
    AutomationEventClaim,
    AutomationEventState,
    AutomationEventStatus,
    EligibleMeetEventBinding,
    ManualMeetTranscriptSyncRequest,
    MeetAutomationConflict,
    MeetAutomationInvalid,
    MeetAutomationNotFound,
    MeetTranscriptAutomationOrchestrator,
    MeetTranscriptReconcileRequest,
    PushTokenClaims,
    ReconciliationLease,
    ResolvedMeetTranscript,
    WorkspaceGrant,
    automation_event_key,
    eligible_binding_key,
    grant_identity_key,
    manual_binding_lookup_key,
    parse_meet_transcript_push,
    parse_push_bearer,
    push_binding_lookup_key,
    verify_push_token,
)


NOW = datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc)
TRANSCRIPT = "conferenceRecords/record-1/transcripts/transcript-1"


def grant(**overrides):
    values = {
        "grant_id": "grant-1",
        "owner_id": "owner-1",
        "org_id": "org-1",
        "workspace_subject": "recruiter@example.com",
        "status": "active",
        "scopes": frozenset(
            {CALENDAR_EVENTS_READONLY_SCOPE, MEETINGS_SPACE_READONLY_SCOPE}
        ),
        "valid_from": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=1),
        "updated_at": NOW,
    }
    values.update(overrides)
    return WorkspaceGrant(**values)


def binding(**overrides):
    values = {
        "grant_id": "grant-1",
        "owner_id": "owner-1",
        "org_id": "org-1",
        "workspace_subject": "recruiter@example.com",
        "calendar_id": "recruiter@example.com",
        "calendar_event_id": "event-1",
        "meet_target": "//meet.googleapis.com/spaces/space-1",
        "workspace_subscription_source": (
            "//workspaceevents.googleapis.com/subscriptions/workspace-sub-1"
        ),
        "pubsub_subscription": "projects/tars-test-project/subscriptions/push-sub-1",
        "title": "Synthetic eligible interview",
        "notice_given": True,
        "notice_provenance": "Synthetic fixture notice",
        "candidate_id": "candidate-1",
        "candidate_name": "Synthetic Candidate",
        "created_at": NOW,
    }
    values.update(overrides)
    return EligibleMeetEventBinding(**values)


def numbered_binding(index: int, **overrides):
    values = {
        "calendar_event_id": f"event-{index}",
        "meet_target": f"//meet.googleapis.com/spaces/space-{index}",
        "workspace_subscription_source": (
            f"//workspaceevents.googleapis.com/subscriptions/workspace-sub-{index}"
        ),
        "pubsub_subscription": (
            f"projects/tars-test-project/subscriptions/push-sub-{index}"
        ),
    }
    values.update(overrides)
    return binding(**values)


def resolution(item=None, **overrides):
    item = item or binding()
    values = {
        "grant_id": item.grant_id,
        "workspace_subject": item.workspace_subject,
        "calendar_id": item.calendar_id,
        "calendar_event_id": item.calendar_event_id,
        "meet_target": item.meet_target,
        "transcript_name": TRANSCRIPT,
    }
    values.update(overrides)
    return ResolvedMeetTranscript(**values)


def entry(index: int, *, transcript=TRANSCRIPT, text=None):
    return {
        "name": f"{transcript}/entries/entry-{index}",
        "participant": f"conferenceRecords/record-1/participants/participant-{index}",
        "text": text or f"Synthetic transcript entry {index}",
        "languageCode": "en-US",
        "startTime": "2026-09-01T16:00:00Z",
        "endTime": "2026-09-01T16:00:01Z",
    }


def page(entries, token=None):
    value = {"transcriptEntries": entries}
    if token is not None:
        value["nextPageToken"] = token
    return json.dumps(value, separators=(",", ":")).encode()


def push_payload(**attribute_overrides):
    item = binding()
    attributes = {
        "ce-datacontenttype": "application/json",
        "ce-id": "push-event-1",
        "ce-source": item.workspace_subscription_source,
        "ce-specversion": "1.0",
        "ce-subject": item.meet_target,
        "ce-time": "2026-09-01T16:00:00Z",
        "ce-type": "google.workspace.meet.transcript.v2.fileGenerated",
    }
    attributes.update(attribute_overrides)
    data = base64.b64encode(
        json.dumps({"transcript": {"name": TRANSCRIPT}}).encode()
    ).decode()
    return {
        "message": {
            "attributes": attributes,
            "data": data,
            "messageId": "pubsub-message-1",
            "publishTime": "2026-09-01T16:00:00Z",
        },
        "subscription": item.pubsub_subscription,
    }


class MemoryAutomationStorage:
    def __init__(self, grants=None, bindings=None):
        self.grants = {item.grant_id: item for item in (grants or [])}
        self.bindings = list(bindings or [])
        self.events: dict[str, AutomationEventState] = {}
        self.reconciliation: dict[str, dict] = {}
        self.queue_calls = 0

    async def get_workspace_grant(self, grant_id, *, owner_id, org_id):
        item = self.grants.get(grant_id)
        if item is None or item.owner_id != owner_id or item.org_id != org_id:
            raise MeetAutomationNotFound("workspace authority not found")
        return item

    async def get_eligible_meet_binding_manual(self, **values):
        for item in self.bindings:
            if (
                item.owner_id == values["owner_id"]
                and item.org_id == values["org_id"]
                and item.grant_id == values["grant_id"]
                and item.calendar_id == values["calendar_id"]
                and item.calendar_event_id == values["calendar_event_id"]
            ):
                return item
        raise MeetAutomationNotFound("eligible Meet event not found")

    async def get_eligible_meet_binding_push(self, **values):
        for item in self.bindings:
            if (
                item.workspace_subscription_source
                == values["workspace_subscription_source"]
                and item.pubsub_subscription == values["pubsub_subscription"]
                and item.meet_target == values["meet_target"]
            ):
                return item
        raise MeetAutomationNotFound("eligible Meet event not found")

    async def list_eligible_meet_bindings(
        self, *, owner_id, org_id, grant_id, limit, after_binding_key
    ):
        eligible = sorted(
            [
            item
            for item in self.bindings
            if item.owner_id == owner_id
            and item.org_id == org_id
            and item.grant_id == grant_id
            ],
            key=eligible_binding_key,
        )
        if after_binding_key is not None:
            eligible = [
                item for item in eligible if eligible_binding_key(item) > after_binding_key
            ] + [
                item for item in eligible if eligible_binding_key(item) <= after_binding_key
            ]
        return eligible[:limit]

    async def queue_meet_automation_event(self, event):
        self.queue_calls += 1
        existing = self.events.get(event.event_key)
        if existing is not None:
            identity = (
                "event_id",
                "trigger",
                "owner_id",
                "org_id",
                "grant_id",
                "workspace_subject",
                "calendar_id",
                "calendar_event_id",
                "meet_target",
                "workspace_subscription_source",
                "pubsub_subscription",
                "transcript_name",
            )
            if any(getattr(existing, key) != getattr(event, key) for key in identity):
                raise MeetAutomationConflict("event identity conflict")
            return existing
        self.events[event.event_key] = event
        return event

    async def claim_meet_automation_event(
        self, *, event_key, lease_token, lease_expires_at, updated_at
    ):
        event = self.events[event_key]
        if event.status == AutomationEventStatus.COMPLETED:
            return AutomationEventClaim(event=event, idempotent_replay=True)
        if (
            event.status == AutomationEventStatus.LEASED
            and event.lease_expires_at is not None
            and event.lease_expires_at > updated_at
        ):
            raise MeetAutomationConflict("active lease")
        claimed = event.model_copy(
            update={
                "status": AutomationEventStatus.LEASED,
                "version": event.version + 1,
                "attempt_count": event.attempt_count + 1,
                "lease_token": lease_token,
                "lease_expires_at": lease_expires_at,
                "reason_code": None,
                "updated_at": updated_at,
            }
        )
        self.events[event_key] = claimed
        return AutomationEventClaim(event=claimed)

    async def complete_meet_automation_event(
        self, result, *, event_key, lease_token, version, updated_at
    ):
        event = self.events[event_key]
        if (
            event.version != version
            or event.lease_token != lease_token
            or event.lease_expires_at is None
            or event.lease_expires_at <= updated_at
        ):
            raise MeetAutomationConflict("stale lease")
        completed = event.model_copy(
            update={
                "status": AutomationEventStatus.COMPLETED,
                "lease_token": None,
                "lease_expires_at": None,
                "import_session_id": result.session_id,
                "import_source_key": result.source_key,
                "import_source_digest": result.source_digest,
                "import_segment_count": result.segment_count,
                "import_attempt_count": result.attempt_count,
                "updated_at": updated_at,
            }
        )
        self.events[event_key] = completed
        return completed

    async def fail_meet_automation_event(
        self, *, event_key, lease_token, version, reason_code, updated_at
    ):
        event = self.events[event_key]
        if (
            event.version != version
            or event.lease_token != lease_token
            or event.lease_expires_at is None
            or event.lease_expires_at <= updated_at
        ):
            raise MeetAutomationConflict("stale lease")
        failed = event.model_copy(
            update={
                "status": AutomationEventStatus.FAILED,
                "lease_token": None,
                "lease_expires_at": None,
                "reason_code": reason_code,
                "updated_at": updated_at,
            }
        )
        self.events[event_key] = failed
        return failed

    async def claim_meet_reconciliation(
        self,
        *,
        owner_id,
        org_id,
        grant_id,
        lease_token,
        lease_expires_at,
        updated_at,
    ):
        key = manual_binding_lookup_key(
            owner_id=owner_id,
            org_id=org_id,
            grant_id=grant_id,
            calendar_id="reconciliation-lease",
            calendar_event_id="active",
        )
        existing = self.reconciliation.get(key)
        if (
            existing
            and existing["lease_token"] is not None
            and existing["lease_expires_at"] > updated_at
        ):
            raise MeetAutomationConflict("active reconciliation")
        version = (existing or {}).get("version", 0) + 1
        attempts = (existing or {}).get("attempt_count", 0) + 1
        self.reconciliation[key] = {
            "version": version,
            "attempt_count": attempts,
            "lease_token": lease_token,
            "lease_expires_at": lease_expires_at,
            "cursor_binding_key": (existing or {}).get("cursor_binding_key"),
        }
        return ReconciliationLease(
            scope_key=key,
            owner_id=owner_id,
            org_id=org_id,
            grant_id=grant_id,
            version=version,
            attempt_count=attempts,
            lease_token=lease_token,
            lease_expires_at=lease_expires_at,
            updated_at=updated_at,
            cursor_binding_key=(existing or {}).get("cursor_binding_key"),
        )

    async def release_meet_reconciliation(
        self,
        *,
        scope_key,
        lease_token,
        version,
        updated_at,
        last_considered_binding_key,
    ):
        existing = self.reconciliation[scope_key]
        if existing["version"] != version or existing["lease_token"] != lease_token:
            raise MeetAutomationConflict("stale reconciliation")
        existing["lease_token"] = None
        existing["lease_expires_at"] = None
        if last_considered_binding_key is not None:
            existing["cursor_binding_key"] = last_considered_binding_key


class FakeProvider:
    def __init__(
        self,
        pages=None,
        resolved=None,
        error=None,
        *,
        never_resolve=False,
        never_fetch=False,
    ):
        self.pages = list(pages or [page([entry(1)])])
        self.resolved = resolved
        self.error = error
        self.never_resolve = never_resolve
        self.never_fetch = never_fetch
        self.resolve_calls = []
        self.fetch_calls = []

    @staticmethod
    def assert_authority(grant_item, binding_item):
        assert grant_item.grant_id == binding_item.grant_id
        assert grant_item.owner_id == binding_item.owner_id
        assert grant_item.org_id == binding_item.org_id
        assert grant_item.workspace_subject == binding_item.workspace_subject
        assert binding_item.calendar_id
        assert binding_item.calendar_event_id
        assert binding_item.meet_target

    async def resolve_transcript(self, grant_item, item):
        self.assert_authority(grant_item, item)
        self.resolve_calls.append((grant_item, item))
        if self.never_resolve:
            await asyncio.Event().wait()
        if self.error:
            raise self.error
        return self.resolved or resolution(item)

    async def fetch_transcript_entries_page(
        self, grant_item, item, transcript_name, *, page_size, page_token
    ):
        self.assert_authority(grant_item, item)
        self.fetch_calls.append(
            (grant_item, item, transcript_name, page_size, page_token)
        )
        if self.never_fetch:
            await asyncio.Event().wait()
        if self.error:
            raise self.error
        if not self.pages:
            pytest.fail("unexpected provider request")
        return self.pages.pop(0)


class CancellationResistantProvider(FakeProvider):
    def __init__(self, *, resist_on):
        super().__init__()
        self.resist_on = resist_on
        self.started = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.release = asyncio.Event()

    async def _resist_once(self):
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancel_seen.set()
            await self.release.wait()

    async def resolve_transcript(self, grant_item, item):
        self.assert_authority(grant_item, item)
        self.resolve_calls.append((grant_item, item))
        if self.resist_on == "resolve":
            await self._resist_once()
        return resolution(item)

    async def fetch_transcript_entries_page(
        self, grant_item, item, transcript_name, *, page_size, page_token
    ):
        self.assert_authority(grant_item, item)
        self.fetch_calls.append(
            (grant_item, item, transcript_name, page_size, page_token)
        )
        if self.resist_on == "fetch":
            await self._resist_once()
        return page([entry(1)])


class SpyImportWorker(GoogleMeetImportWorker):
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def run(self, request, *, owner_id, org_id, now=None):
        self.calls.append((request, owner_id, org_id))
        if self.error:
            raise self.error
        return GoogleMeetImportResult(
            session_id="meet-import-" + "1" * 32,
            source_key="2" * 64,
            source_digest="3" * 64,
            status=ImportJobStatus.COMPLETED,
            segment_count=sum(len(session.entries) for session in request.transcript_sessions),
            attempt_count=1,
            idempotent_replay=False,
        )


def orchestrator(
    storage=None,
    provider=None,
    worker=None,
    *,
    clock=None,
    monotonic=None,
    deadline_seconds=30.0,
):
    return MeetTranscriptAutomationOrchestrator(
        storage or MemoryAutomationStorage([grant()], [binding()]),
        provider or FakeProvider(),
        worker or SpyImportWorker(),
        clock=clock or (lambda: NOW),
        monotonic=monotonic or (lambda: 0.0),
        deadline_seconds=deadline_seconds,
    )


def manual_request(**overrides):
    values = {
        "grantId": "grant-1",
        "calendarId": "recruiter@example.com",
        "calendarEventId": "event-1",
    }
    values.update(overrides)
    return ManualMeetTranscriptSyncRequest.model_validate(values)


def parsed_event(payload=None):
    raw = json.dumps(payload or push_payload(), separators=(",", ":")).encode()
    return parse_meet_transcript_push(raw)


def test_grants_forbid_credentials_unknown_fields_and_require_aware_validity():
    for forbidden in ("access_token", "refreshToken", "authorizationCode", "clientSecret"):
        with pytest.raises(ValidationError):
            WorkspaceGrant(**{**grant().model_dump(), forbidden: "sensitive"})
    with pytest.raises(ValidationError, match="timezone"):
        grant(expires_at=datetime(2026, 9, 2, 16, 0))


def test_binding_context_cumulative_utf8_ceiling_is_exactly_900000_bytes():
    accepted = binding(
        resume_artifact_id="resume-1",
        resume_text="r" * 500_000,
        job_description_artifact_id="jd-1",
        job_description_text="j" * 400_000,
    )
    assert len(accepted.resume_text.encode()) + len(
        accepted.job_description_text.encode()
    ) == 900_000
    with pytest.raises(ValidationError, match="900000 UTF-8 bytes"):
        binding(
            resume_artifact_id="resume-1",
            resume_text="r" * 500_000,
            job_description_artifact_id="jd-1",
            job_description_text="j" * 400_000,
            briefing="x",
        )
    with pytest.raises(ValidationError, match="900000 UTF-8 bytes"):
        binding(
            resume_artifact_id="resume-1",
            resume_text=("r" * 499_999) + "é",
            job_description_artifact_id="jd-1",
            job_description_text="j" * 400_000,
        )


@pytest.mark.parametrize(
    "grant_override,owner,org",
    [
        ({}, "owner-2", "org-1"),
        ({}, "owner-1", "org-2"),
        ({"status": "revoked"}, "owner-1", "org-1"),
        ({"status": "expired"}, "owner-1", "org-1"),
        ({"expires_at": NOW}, "owner-1", "org-1"),
        ({"scopes": {CALENDAR_EVENTS_READONLY_SCOPE}}, "owner-1", "org-1"),
        ({"scopes": {MEETINGS_SPACE_READONLY_SCOPE}}, "owner-1", "org-1"),
        ({"scopes": {CALENDAR_EVENTS_READONLY_SCOPE, MEETINGS_SPACE_READONLY_SCOPE, "https://www.googleapis.com/auth/drive.readonly"}}, "owner-1", "org-1"),
    ],
)
def test_wrong_scope_status_expiry_or_tenant_fails_before_provider(
    grant_override, owner, org
):
    item = grant(**grant_override)
    stored_binding = binding(owner_id=owner, org_id=org) if owner != "owner-1" or org != "org-1" else binding()
    storage = MemoryAutomationStorage([item], [stored_binding])
    provider = FakeProvider()
    worker = SpyImportWorker()
    with pytest.raises(MeetAutomationNotFound):
        asyncio.run(
            orchestrator(storage, provider, worker).process_manual(
                manual_request(), owner_id=owner, org_id=org
            )
        )
    assert provider.resolve_calls == provider.fetch_calls == []
    assert worker.calls == []
    assert storage.events == {}


def test_exact_manual_and_three_way_push_selectors_do_not_use_title_or_neighbors():
    item = binding()
    storage = MemoryAutomationStorage([grant()], [item])
    exact = asyncio.run(
        storage.get_eligible_meet_binding_manual(
            owner_id="owner-1",
            org_id="org-1",
            grant_id="grant-1",
            calendar_id="recruiter@example.com",
            calendar_event_id="event-1",
        )
    )
    assert exact == item
    for changed in (
        {"calendar_event_id": "event-2"},
        {"calendar_id": "neighbor@example.com"},
        {"grant_id": "grant-2"},
    ):
        values = {
            "owner_id": "owner-1",
            "org_id": "org-1",
            "grant_id": "grant-1",
            "calendar_id": "recruiter@example.com",
            "calendar_event_id": "event-1",
        }
        values.update(changed)
        with pytest.raises(MeetAutomationNotFound):
            asyncio.run(storage.get_eligible_meet_binding_manual(**values))
    for field, bad in (
        ("workspace_subscription_source", "//workspaceevents.googleapis.com/subscriptions/neighbor"),
        ("pubsub_subscription", "projects/tars-test-project/subscriptions/neighbor"),
        ("meet_target", "//meet.googleapis.com/spaces/neighbor"),
    ):
        values = {
            "workspace_subscription_source": item.workspace_subscription_source,
            "pubsub_subscription": item.pubsub_subscription,
            "meet_target": item.meet_target,
        }
        values[field] = bad
        with pytest.raises(MeetAutomationNotFound):
            asyncio.run(storage.get_eligible_meet_binding_push(**values))


class FakeVerifier:
    expected_audience = "https://push.example.test/meet"
    expected_service_account_email = "push@example.iam.gserviceaccount.com"

    def __init__(self, claims=None, error=None):
        self.claims = claims or PushTokenClaims(
            audience=self.expected_audience,
            email=self.expected_service_account_email,
            email_verified=True,
        )
        self.error = error
        self.tokens = []

    async def verify(self, token):
        self.tokens.append(token)
        if self.error:
            raise self.error
        return self.claims


def test_push_bearer_and_verified_claim_identity_are_exact():
    assert parse_push_bearer([(b"authorization", b"Bearer header.payload.signature")]) == "header.payload.signature"
    for headers in (
        [],
        [(b"authorization", b"bearer token")],
        [(b"authorization", b"Bearer token extra")],
        [(b"authorization", b"Bearer one"), (b"Authorization", b"Bearer two")],
    ):
        with pytest.raises(MeetAutomationInvalid):
            parse_push_bearer(headers)
    for claims in (
        PushTokenClaims(audience="wrong", email=FakeVerifier.expected_service_account_email, email_verified=True),
        PushTokenClaims(audience=FakeVerifier.expected_audience, email="wrong@example.com", email_verified=True),
        PushTokenClaims(audience=FakeVerifier.expected_audience, email=FakeVerifier.expected_service_account_email, email_verified=False),
    ):
        with pytest.raises(MeetAutomationInvalid):
            asyncio.run(verify_push_token(FakeVerifier(claims), "token"))
    for invalid_verified in ("true", "yes", 1):
        with pytest.raises(MeetAutomationInvalid):
            asyncio.run(
                verify_push_token(
                    FakeVerifier(
                        {
                            "audience": FakeVerifier.expected_audience,
                            "email": FakeVerifier.expected_service_account_email,
                            "email_verified": invalid_verified,
                        }
                    ),
                    "token",
                )
            )
    accepted = asyncio.run(verify_push_token(FakeVerifier(), "token"))
    assert accepted.email_verified is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["message"]["attributes"].update({"ce-type": "wrong"}),
        lambda value: value["message"]["attributes"].update({"ce-source": "//workspaceevents.googleapis.com/subscriptions/neighbor"}),
        lambda value: value["message"]["attributes"].update({"ce-subject": "//meet.googleapis.com/spaces/neighbor"}),
        lambda value: value.update({"subscription": "projects/tars-test-project/subscriptions/neighbor"}),
        lambda value: value["message"]["attributes"].update({"ce-extra": "unknown"}),
        lambda value: value["message"].update({"data": "%%%"}),
        lambda value: value["message"].pop("messageId"),
        lambda value: value["message"].update({"publishTime": "not-a-time"}),
    ],
)
def test_push_malformed_or_unbound_envelopes_never_write_or_call_provider(mutate):
    value = push_payload()
    mutate(value)
    storage = MemoryAutomationStorage([grant()], [binding()])
    provider = FakeProvider()
    worker = SpyImportWorker()
    runner = orchestrator(storage, provider, worker)
    try:
        event = parsed_event(value)
    except MeetAutomationInvalid:
        pass
    else:
        with pytest.raises(MeetAutomationNotFound):
            asyncio.run(runner.process_webhook(event))
    assert storage.events == {}
    assert provider.fetch_calls == []
    assert worker.calls == []


def test_push_json_duplicate_keys_and_decoded_duplicate_keys_fail_closed():
    value = json.dumps(push_payload())
    duplicate_outer = value.replace('"message":', '"message":{},"message":', 1).encode()
    with pytest.raises(MeetAutomationInvalid):
        parse_meet_transcript_push(duplicate_outer)
    decoded = b'{"transcript":{"name":"' + TRANSCRIPT.encode() + b'","name":"' + TRANSCRIPT.encode() + b'"}}'
    payload = push_payload()
    payload["message"]["data"] = base64.b64encode(decoded).decode()
    with pytest.raises(MeetAutomationInvalid):
        parsed_event(payload)


def test_documented_slash_cloud_event_id_is_bounded_and_admitted():
    value = push_payload(
        **{
            "ce-id": (
                "//meet.googleapis.com/spaces/space-1/spaceEvents/event-123"
            )
        }
    )
    assert parsed_event(value).event_id.endswith("/spaceEvents/event-123")
    value["message"]["attributes"]["ce-id"] = "bad id"
    with pytest.raises(MeetAutomationInvalid):
        parsed_event(value)


def test_grant_storage_identity_is_tenant_scoped_and_event_schema_has_one_calendar_id():
    assert grant_identity_key(owner_id="owner-1", org_id="org-1", grant_id="grant-1") != grant_identity_key(
        owner_id="owner-2", org_id="org-1", grant_id="grant-1"
    )
    assert list(AutomationEventState.__annotations__).count("calendar_id") == 1


def test_webhook_first_delivery_calls_worker_once_and_completion_replays_without_calls():
    storage = MemoryAutomationStorage([grant()], [binding()])
    provider = FakeProvider(pages=[page([entry(1)])])
    worker = SpyImportWorker()
    runner = orchestrator(storage, provider, worker)
    first = asyncio.run(runner.process_webhook(parsed_event()))
    provider.pages.append(page([entry(1)]))
    replay = asyncio.run(runner.process_webhook(parsed_event()))
    assert first.status == replay.status == ImportJobStatus.COMPLETED
    assert replay.automation_replay is True
    assert len(worker.calls) == 1
    assert len(provider.resolve_calls) == 1
    assert len(provider.fetch_calls) == 1
    stored = next(iter(storage.events.values())).model_dump(mode="json")
    serialized = json.dumps(stored)
    for forbidden in ("Synthetic transcript", "Bearer", "raw", "provider exception"):
        assert forbidden not in serialized


def test_webhook_resolved_transcript_mismatch_fails_claim_before_fetch_or_import():
    storage = MemoryAutomationStorage([grant()], [binding()])
    provider = FakeProvider(
        resolved=resolution(
            transcript_name="conferenceRecords/record-1/transcripts/other"
        )
    )
    worker = SpyImportWorker()
    with pytest.raises(MeetAutomationInvalid, match="identity"):
        asyncio.run(
            orchestrator(storage, provider, worker).process_webhook(parsed_event())
        )
    assert len(provider.resolve_calls) == 1
    assert provider.fetch_calls == []
    assert worker.calls == []
    event_state = next(iter(storage.events.values()))
    assert event_state.status == AutomationEventStatus.FAILED
    assert event_state.reason_code == "automation_failed"


def test_failed_after_queue_is_immediately_reclaimable_and_active_lease_blocks():
    storage = MemoryAutomationStorage([grant()], [binding()])
    failing_provider = FakeProvider(error=RuntimeError("provider sensitive text"))
    runner = orchestrator(storage, failing_provider, SpyImportWorker())
    with pytest.raises(RuntimeError):
        asyncio.run(runner.process_webhook(parsed_event()))
    event_state = next(iter(storage.events.values()))
    assert event_state.status == AutomationEventStatus.FAILED
    assert event_state.reason_code == "automation_failed"
    assert "provider sensitive text" not in json.dumps(event_state.model_dump(mode="json"))

    good_provider = FakeProvider()
    good_worker = SpyImportWorker()
    recovered = asyncio.run(orchestrator(storage, good_provider, good_worker).process_webhook(parsed_event()))
    assert recovered.status == ImportJobStatus.COMPLETED
    assert len(good_worker.calls) == 1

    active_storage = MemoryAutomationStorage([grant()], [binding()])
    queued = runner._queued_event(
        binding=binding(),
        trigger="webhook",
        event_id="push-event-1",
        transcript_name=TRANSCRIPT,
        updated_at=NOW,
    )
    asyncio.run(active_storage.queue_meet_automation_event(queued))
    asyncio.run(
        active_storage.claim_meet_automation_event(
            event_key=queued.event_key,
            lease_token="active-token",
            lease_expires_at=NOW + timedelta(minutes=5),
            updated_at=NOW,
        )
    )
    with pytest.raises(MeetAutomationConflict, match="active"):
        asyncio.run(orchestrator(active_storage).process_webhook(parsed_event()))


def test_four_pages_of_one_hundred_succeed_with_exact_request_shape():
    pages = [
        page([entry(page_index * 100 + i) for i in range(100)], f"token-{page_index + 1}" if page_index < 3 else None)
        for page_index in range(4)
    ]
    provider = FakeProvider(pages=pages)
    worker = SpyImportWorker()
    result = asyncio.run(orchestrator(provider=provider, worker=worker).process_manual(manual_request(), owner_id="owner-1", org_id="org-1"))
    assert result.segment_count == 400
    assert [call[3:] for call in provider.fetch_calls] == [
        (100, None),
        (100, "token-1"),
        (100, "token-2"),
        (100, "token-3"),
    ]
    assert len(worker.calls) == 1
    expected_grant = grant()
    expected_binding = binding()
    assert all(
        call[0] == expected_grant and call[1] == expected_binding
        for call in provider.fetch_calls
    )


def test_mutated_grant_authority_fails_before_any_provider_or_import_call():
    class MutatedGrantStorage(MemoryAutomationStorage):
        async def get_workspace_grant(self, grant_id, *, owner_id, org_id):
            return grant(grant_id="other-grant")

    storage = MutatedGrantStorage([grant()], [binding()])
    provider = FakeProvider()
    worker = SpyImportWorker()
    with pytest.raises(MeetAutomationNotFound):
        asyncio.run(
            orchestrator(storage, provider, worker).process_manual(
                manual_request(), owner_id="owner-1", org_id="org-1"
            )
        )
    assert provider.resolve_calls == provider.fetch_calls == []
    assert worker.calls == []


def test_never_settling_provider_resolution_and_fetch_settle_finitely():
    manual_storage = MemoryAutomationStorage([grant()], [binding()])
    manual_provider = FakeProvider(never_resolve=True)
    manual_worker = SpyImportWorker()
    started = time.monotonic()
    with pytest.raises(MeetAutomationInvalid, match="deadline"):
        asyncio.run(
            orchestrator(
                manual_storage,
                manual_provider,
                manual_worker,
                deadline_seconds=0.01,
            ).process_manual(
                manual_request(), owner_id="owner-1", org_id="org-1"
            )
        )
    assert time.monotonic() - started < 0.5
    assert manual_storage.events == {}
    assert manual_worker.calls == []

    webhook_storage = MemoryAutomationStorage([grant()], [binding()])
    webhook_provider = FakeProvider(never_fetch=True)
    webhook_worker = SpyImportWorker()
    started = time.monotonic()
    with pytest.raises(MeetAutomationInvalid, match="deadline"):
        asyncio.run(
            orchestrator(
                webhook_storage,
                webhook_provider,
                webhook_worker,
                deadline_seconds=0.01,
            ).process_webhook(parsed_event())
        )
    assert time.monotonic() - started < 0.5
    queued = next(iter(webhook_storage.events.values()))
    assert queued.status == AutomationEventStatus.FAILED
    assert queued.reason_code == "automation_failed"
    assert webhook_worker.calls == []


def test_reconciliation_provider_timeout_releases_durable_lease():
    storage = MemoryAutomationStorage([grant()], [binding()])
    provider = FakeProvider(never_resolve=True)
    worker = SpyImportWorker()
    started = time.monotonic()
    result = asyncio.run(
        orchestrator(
            storage,
            provider,
            worker,
            deadline_seconds=0.01,
        ).reconcile(
            MeetTranscriptReconcileRequest(grantId="grant-1", maxEvents=1),
            owner_id="owner-1",
            org_id="org-1",
        )
    )
    assert time.monotonic() - started < 0.5
    assert result.considered == result.failed == 1
    assert result.completed == 0
    assert worker.calls == []
    lease = next(iter(storage.reconciliation.values()))
    assert lease["lease_token"] is None


def test_cancellation_resistant_webhook_provider_detaches_and_fails_within_deadline():
    async def exercise():
        storage = MemoryAutomationStorage([grant()], [binding()])
        provider = CancellationResistantProvider(resist_on="fetch")
        worker = SpyImportWorker()
        runner = orchestrator(
            storage,
            provider,
            worker,
            monotonic=time.monotonic,
            deadline_seconds=0.01,
        )
        started = time.monotonic()
        try:
            with pytest.raises(MeetAutomationInvalid, match="deadline"):
                await runner.process_webhook(parsed_event())
            assert time.monotonic() - started < 0.06
            await asyncio.wait_for(provider.cancel_seen.wait(), timeout=0.05)
            event_state = next(iter(storage.events.values()))
            assert event_state.status == AutomationEventStatus.FAILED
            assert event_state.reason_code == "automation_failed"
            assert worker.calls == []
            assert len(runner._pending_provider_tasks) == 1
        finally:
            provider.release.set()
            for _ in range(20):
                if not runner._pending_provider_tasks:
                    break
                await asyncio.sleep(0)
        assert runner._pending_provider_tasks == set()

    asyncio.run(exercise())


def test_cancellation_resistant_reconciliation_releases_lease_within_deadline():
    async def exercise():
        storage = MemoryAutomationStorage([grant()], [binding()])
        provider = CancellationResistantProvider(resist_on="resolve")
        worker = SpyImportWorker()
        runner = orchestrator(
            storage,
            provider,
            worker,
            monotonic=time.monotonic,
            deadline_seconds=0.01,
        )
        started = time.monotonic()
        try:
            result = await runner.reconcile(
                MeetTranscriptReconcileRequest(grantId="grant-1", maxEvents=1),
                owner_id="owner-1",
                org_id="org-1",
            )
            assert time.monotonic() - started < 0.06
            await asyncio.wait_for(provider.cancel_seen.wait(), timeout=0.05)
            assert result.considered == result.failed == 1
            assert result.completed == 0
            assert worker.calls == []
            lease = next(iter(storage.reconciliation.values()))
            assert lease["lease_token"] is None
            assert len(runner._pending_provider_tasks) == 1
        finally:
            provider.release.set()
            for _ in range(20):
                if not runner._pending_provider_tasks:
                    break
                await asyncio.sleep(0)
        assert runner._pending_provider_tasks == set()

    asyncio.run(exercise())


def test_pending_provider_task_cap_is_25_and_fails_closed():
    async def exercise():
        storage = MemoryAutomationStorage([grant()], [binding()])
        runner = orchestrator(
            storage,
            FakeProvider(),
            SpyImportWorker(),
            monotonic=time.monotonic,
        )
        release = asyncio.Event()

        async def hostile_provider_call():
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

        try:
            for _ in range(25):
                with pytest.raises(MeetAutomationInvalid, match="deadline"):
                    await runner._await_provider(
                        hostile_provider_call,
                        deadline=time.monotonic() + 0.001,
                    )
            assert len(runner._pending_provider_tasks) == 25
            with pytest.raises(MeetAutomationInvalid, match="capacity"):
                await runner._await_provider(
                    hostile_provider_call,
                    deadline=time.monotonic() + 1,
                )
        finally:
            release.set()
            for _ in range(50):
                if not runner._pending_provider_tasks:
                    break
                await asyncio.sleep(0)
        assert runner._pending_provider_tasks == set()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "pages",
    [
        [page([entry(i) for i in range(101)])],
        [page([entry(1)], "")],
        [page([entry(1)], "same"), page([entry(2)], "same")],
        [page([entry(1)], "one"), page([entry(2)], "two"), page([entry(3)], "three"), page([entry(4)], "five")],
        [page([entry(1, transcript="conferenceRecords/other/transcripts/other")])],
        [page([{**entry(1), "name": f"{TRANSCRIPT}/entries/nested/id"}])],
        [page([entry(1), entry(1, text="conflict")])],
        [b'{"transcriptEntries":[{"name":"bad"}]}'],
        [json.dumps({"transcriptEntries": [entry(1)], "unknown": True}).encode()],
    ],
)
def test_page_bounds_identity_and_partial_shape_fail_before_import(pages):
    provider = FakeProvider(pages=pages)
    worker = SpyImportWorker()
    with pytest.raises(MeetAutomationInvalid):
        asyncio.run(orchestrator(provider=provider, worker=worker).process_manual(manual_request(), owner_id="owner-1", org_id="org-1"))
    assert worker.calls == []


def test_cumulative_bytes_and_deadline_fail_before_import():
    oversized_pages = [
        page(
            [entry(page_index * 100 + i, text="x" * 6_000) for i in range(100)],
            f"token-{page_index + 1}" if page_index < 3 else None,
        )
        for page_index in range(4)
    ]
    provider = FakeProvider(pages=oversized_pages)
    worker = SpyImportWorker()
    with pytest.raises(MeetAutomationInvalid):
        asyncio.run(orchestrator(provider=provider, worker=worker).process_manual(manual_request(), owner_id="owner-1", org_id="org-1"))
    assert worker.calls == []

    ticks = iter([0.0, 0.0, 31.0])
    provider = FakeProvider()
    worker = SpyImportWorker()
    with pytest.raises(MeetAutomationInvalid):
        asyncio.run(orchestrator(provider=provider, worker=worker, monotonic=lambda: next(ticks)).process_manual(manual_request(), owner_id="owner-1", org_id="org-1"))
    assert worker.calls == []


def test_manual_resolution_echo_mismatch_fails_and_exact_delivery_replays():
    mismatch_provider = FakeProvider(resolved=resolution(calendar_event_id="neighbor"))
    worker = SpyImportWorker()
    with pytest.raises(MeetAutomationNotFound):
        asyncio.run(orchestrator(provider=mismatch_provider, worker=worker).process_manual(manual_request(), owner_id="owner-1", org_id="org-1"))
    assert mismatch_provider.fetch_calls == []
    assert worker.calls == []

    storage = MemoryAutomationStorage([grant()], [binding()])
    provider = FakeProvider(pages=[page([entry(1)]), page([entry(1)])])
    worker = SpyImportWorker()
    runner = orchestrator(storage, provider, worker)
    first = asyncio.run(runner.process_manual(manual_request(), owner_id="owner-1", org_id="org-1"))
    replay = asyncio.run(runner.process_manual(manual_request(), owner_id="owner-1", org_id="org-1"))
    assert first.automation_replay is False
    assert replay.automation_replay is True
    assert len(provider.resolve_calls) == 2
    assert len(provider.fetch_calls) == len(worker.calls) == 1


def test_reconciliation_lists_only_stored_scope_obeys_limit_and_releases_lease():
    eligible = [binding(calendar_event_id=f"event-{i}") for i in range(1, 4)]
    unrelated = binding(calendar_event_id="unrelated", owner_id="owner-2")
    storage = MemoryAutomationStorage([grant()], [*eligible, unrelated])
    provider = FakeProvider(pages=[page([entry(1)]), page([entry(2)])])
    worker = SpyImportWorker()
    result = asyncio.run(
        orchestrator(storage, provider, worker).reconcile(
            MeetTranscriptReconcileRequest(grantId="grant-1", maxEvents=2),
            owner_id="owner-1",
            org_id="org-1",
        )
    )
    assert result.considered == result.completed == 2
    assert result.failed == 0
    assert len(provider.resolve_calls) == len(provider.fetch_calls) == len(worker.calls) == 2
    lease = next(iter(storage.reconciliation.values()))
    assert lease["lease_token"] is None


def test_reconciliation_active_lease_conflicts_and_expired_lease_recovers():
    storage = MemoryAutomationStorage([grant()], [binding()])
    scope = manual_binding_lookup_key(
        owner_id="owner-1",
        org_id="org-1",
        grant_id="grant-1",
        calendar_id="reconciliation-lease",
        calendar_event_id="active",
    )
    storage.reconciliation[scope] = {
        "version": 1,
        "attempt_count": 1,
        "lease_token": "active",
        "lease_expires_at": NOW + timedelta(minutes=1),
    }
    with pytest.raises(MeetAutomationConflict, match="active"):
        asyncio.run(orchestrator(storage).reconcile(MeetTranscriptReconcileRequest(grantId="grant-1", maxEvents=1), owner_id="owner-1", org_id="org-1"))
    storage.reconciliation[scope]["lease_expires_at"] = NOW - timedelta(seconds=1)
    result = asyncio.run(orchestrator(storage).reconcile(MeetTranscriptReconcileRequest(grantId="grant-1", maxEvents=1), owner_id="owner-1", org_id="org-1"))
    assert result.completed == 1
    assert storage.reconciliation[scope]["version"] == 2


def test_reconciliation_cursor_prevents_starvation_across_two_max_25_runs():
    items = [numbered_binding(index) for index in range(1, 27)]
    storage = MemoryAutomationStorage([grant()], items)
    provider = FakeProvider(pages=[page([entry(index)]) for index in range(26)])
    worker = SpyImportWorker()
    runner = orchestrator(storage, provider, worker)
    request = MeetTranscriptReconcileRequest(grantId="grant-1", maxEvents=25)
    first = asyncio.run(
        runner.reconcile(request, owner_id="owner-1", org_id="org-1")
    )
    second = asyncio.run(
        runner.reconcile(request, owner_id="owner-1", org_id="org-1")
    )
    assert first.considered == second.considered == 25
    resolved_event_ids = {
        call[1].calendar_event_id for call in provider.resolve_calls
    }
    assert resolved_event_ids == {item.calendar_event_id for item in items}
    assert len(worker.calls) == 26
    assert second.replayed == 24


class AtomicSnapshot:
    def __init__(self, ref):
        self.id = ref.path[-1]
        self.exists = ref.path in ref.db.data
        self._value = copy.deepcopy(ref.db.data.get(ref.path, {}))

    def to_dict(self):
        return copy.deepcopy(self._value)


class AtomicDocument:
    def __init__(self, db, path):
        self.db = db
        self.path = path

    def collection(self, name):
        return AtomicCollection(self.db, self.path + (name,))

    async def get(self, transaction=None):
        return AtomicSnapshot(self)


class AtomicCollection:
    def __init__(self, db, path, *, after=None, through=None, maximum=None):
        self.db = db
        self.path = path
        self.after = after
        self.through = through
        self.maximum = maximum

    def document(self, item):
        return AtomicDocument(self.db, self.path + (item,))

    def order_by(self, field):
        assert field == "bindingKey"
        return AtomicCollection(self.db, self.path)

    def start_after(self, value):
        return AtomicCollection(
            self.db,
            self.path,
            after=value["bindingKey"],
            through=self.through,
            maximum=self.maximum,
        )

    def end_at(self, value):
        return AtomicCollection(
            self.db,
            self.path,
            after=self.after,
            through=value["bindingKey"],
            maximum=self.maximum,
        )

    def limit(self, maximum):
        return AtomicCollection(
            self.db,
            self.path,
            after=self.after,
            through=self.through,
            maximum=maximum,
        )

    def stream(self):
        collection = self

        async def generate():
            paths = [
                path
                for path in collection.db.data
                if len(path) == len(collection.path) + 1
                and path[: len(collection.path)] == collection.path
            ]
            paths.sort(
                key=lambda path: collection.db.data[path].get("bindingKey", "")
            )
            yielded = 0
            for path in paths:
                binding_key = collection.db.data[path].get("bindingKey", "")
                if collection.after is not None and binding_key <= collection.after:
                    continue
                if collection.through is not None and binding_key > collection.through:
                    continue
                if collection.maximum is not None and yielded >= collection.maximum:
                    break
                yielded += 1
                yield AtomicSnapshot(AtomicDocument(collection.db, path))

        return generate()


class AtomicTransaction:
    def __init__(self, db):
        self.db = db
        self.operations = []

    def create(self, ref, value):
        self.operations.append(("create", ref.path, copy.deepcopy(value)))

    def set(self, ref, value):
        self.operations.append(("set", ref.path, copy.deepcopy(value)))

    def commit(self):
        for operation, path, value in self.operations:
            if operation == "create" and path in self.db.data:
                raise RuntimeError("exists")
            self.db.data[path] = value


class AtomicDB:
    def __init__(self):
        self.data = {}

    def collection(self, name):
        return AtomicCollection(self, (name,))

    def transaction(self):
        return AtomicTransaction(self)


def firestore_storage(monkeypatch):
    db = AtomicDB()
    storage = object.__new__(FirestoreStorage)

    async def get_db():
        return db

    def transactional(function):
        async def run(transaction):
            result = await function(transaction)
            transaction.commit()
            return result
        return run

    monkeypatch.setattr(storage, "_get_db", get_db)
    monkeypatch.setattr("backend.storage.firestore.firestore.async_transactional", transactional)
    return storage, db


def test_firestore_binding_indexes_are_identity_only_and_canonical(monkeypatch):
    storage, db = firestore_storage(monkeypatch)
    item = numbered_binding(1)
    asyncio.run(storage.store_eligible_meet_binding(item))
    binding_id = eligible_binding_key(item)
    canonical = db.data[("workspace_meet_eligible_events", binding_id)]
    assert canonical["bindingKey"] == binding_id
    assert canonical["title"] == item.title
    scope_records = [
        value
        for path, value in db.data.items()
        if len(path) == 4
        and path[0] == "workspace_meet_grant_scopes"
        and path[2] == "eligible_events"
    ]
    assert scope_records == [{"bindingKey": binding_id}]
    assert all(
        set(value) == {"bindingKey"}
        for path, value in db.data.items()
        if path[0] == "workspace_meet_manual_index"
    )
    push_records = [
        value
        for path, value in db.data.items()
        if path[0] == "workspace_meet_push_index"
    ]
    assert push_records == [
        {
            "bindingKey": binding_id,
            "ownerId": item.owner_id,
            "orgId": item.org_id,
            "grantId": item.grant_id,
            "workspaceSubject": item.workspace_subject,
            "calendarId": item.calendar_id,
            "calendarEventId": item.calendar_event_id,
        }
    ]
    assert (
        asyncio.run(
            storage.get_eligible_meet_binding_push(
                workspace_subscription_source=item.workspace_subscription_source,
                pubsub_subscription=item.pubsub_subscription,
                meet_target=item.meet_target,
            )
        )
        == item
    )


def test_firestore_corrupt_indexes_and_noncanonical_binding_ids_fail_closed(monkeypatch):
    storage, db = firestore_storage(monkeypatch)
    first = numbered_binding(1)
    other = numbered_binding(
        2,
        owner_id="owner-2",
        org_id="org-2",
        grant_id="grant-2",
        workspace_subject="other@example.com",
        calendar_id="other@example.com",
        workspace_subscription_source=first.workspace_subscription_source,
        pubsub_subscription=first.pubsub_subscription,
        meet_target=first.meet_target,
    )
    asyncio.run(storage.store_eligible_meet_binding(first))
    other_binding_id = eligible_binding_key(other)
    db.data[("workspace_meet_eligible_events", other_binding_id)] = (
        storage._eligible_meet_binding_record(other)
    )
    first_push_id = push_binding_lookup_key(
        workspace_subscription_source=first.workspace_subscription_source,
        pubsub_subscription=first.pubsub_subscription,
        meet_target=first.meet_target,
    )
    db.data[("workspace_meet_push_index", first_push_id)]["bindingKey"] = (
        other_binding_id
    )
    with pytest.raises(MeetAutomationNotFound):
        asyncio.run(
            storage.get_eligible_meet_binding_push(
                workspace_subscription_source=first.workspace_subscription_source,
                pubsub_subscription=first.pubsub_subscription,
                meet_target=first.meet_target,
            )
        )

    first_manual_id = manual_binding_lookup_key(
        owner_id=first.owner_id,
        org_id=first.org_id,
        grant_id=first.grant_id,
        calendar_id=first.calendar_id,
        calendar_event_id=first.calendar_event_id,
    )
    noncanonical_id = "f" * 64
    corrupt_record = copy.deepcopy(
        db.data[("workspace_meet_eligible_events", eligible_binding_key(first))]
    )
    corrupt_record["bindingKey"] = noncanonical_id
    db.data[("workspace_meet_eligible_events", noncanonical_id)] = corrupt_record
    db.data[("workspace_meet_manual_index", first_manual_id)] = {
        "bindingKey": noncanonical_id
    }
    with pytest.raises(MeetAutomationNotFound):
        asyncio.run(
            storage.get_eligible_meet_binding_manual(
                owner_id=first.owner_id,
                org_id=first.org_id,
                grant_id=first.grant_id,
                calendar_id=first.calendar_id,
                calendar_event_id=first.calendar_event_id,
            )
        )


def test_firestore_raw_unknown_sensitive_and_heuristic_fields_are_rejected(monkeypatch):
    storage, db = firestore_storage(monkeypatch)
    authority = grant()
    item = numbered_binding(1)
    asyncio.run(storage.store_workspace_grant(authority))
    asyncio.run(storage.store_eligible_meet_binding(item))

    grant_path = (
        "workspace_grants",
        grant_identity_key(
            owner_id=authority.owner_id,
            org_id=authority.org_id,
            grant_id=authority.grant_id,
        ),
    )
    db.data[grant_path]["accessToken"] = "must-not-project"
    with pytest.raises(MeetAutomationNotFound):
        asyncio.run(
            storage.get_workspace_grant(
                authority.grant_id,
                owner_id=authority.owner_id,
                org_id=authority.org_id,
            )
        )

    binding_id = eligible_binding_key(item)
    db.data[("workspace_meet_eligible_events", binding_id)][
        "meetingTitleHeuristic"
    ] = "must-not-project"
    with pytest.raises(MeetAutomationNotFound):
        asyncio.run(
            storage.get_eligible_meet_binding_push(
                workspace_subscription_source=item.workspace_subscription_source,
                pubsub_subscription=item.pubsub_subscription,
                meet_target=item.meet_target,
            )
        )

    event_state = orchestrator()._queued_event(
        binding=binding(),
        trigger="webhook",
        event_id="push-event-1",
        transcript_name=TRANSCRIPT,
        updated_at=NOW,
    )
    asyncio.run(storage.queue_meet_automation_event(event_state))
    event_path = ("workspace_meet_automation_events", event_state.event_key)
    db.data[event_path]["rawPayload"] = "must-not-project"
    with pytest.raises(MeetAutomationConflict, match="record"):
        asyncio.run(
            storage.claim_meet_automation_event(
                event_key=event_state.event_key,
                lease_token="lease-token",
                lease_expires_at=NOW + timedelta(minutes=5),
                updated_at=NOW,
            )
        )


def test_firestore_reconciliation_cursor_preserves_advances_and_wraps(monkeypatch):
    storage, db = firestore_storage(monkeypatch)
    items = [numbered_binding(index) for index in range(1, 4)]
    for item in items:
        asyncio.run(storage.store_eligible_meet_binding(item))
    ordered = sorted(items, key=eligible_binding_key)

    async def exercise():
        first_lease = await storage.claim_meet_reconciliation(
            owner_id="owner-1",
            org_id="org-1",
            grant_id="grant-1",
            lease_token="lease-1",
            lease_expires_at=NOW + timedelta(minutes=5),
            updated_at=NOW,
        )
        assert first_lease.cursor_binding_key is None
        first_page = await storage.list_eligible_meet_bindings(
            owner_id="owner-1",
            org_id="org-1",
            grant_id="grant-1",
            limit=2,
            after_binding_key=first_lease.cursor_binding_key,
        )
        assert first_page == ordered[:2]
        cursor = eligible_binding_key(first_page[-1])
        await storage.release_meet_reconciliation(
            scope_key=first_lease.scope_key,
            lease_token=first_lease.lease_token,
            version=first_lease.version,
            updated_at=NOW + timedelta(seconds=1),
            last_considered_binding_key=cursor,
        )
        second_lease = await storage.claim_meet_reconciliation(
            owner_id="owner-1",
            org_id="org-1",
            grant_id="grant-1",
            lease_token="lease-2",
            lease_expires_at=NOW + timedelta(minutes=5),
            updated_at=NOW + timedelta(seconds=2),
        )
        assert second_lease.cursor_binding_key == cursor
        wrapped = await storage.list_eligible_meet_bindings(
            owner_id="owner-1",
            org_id="org-1",
            grant_id="grant-1",
            limit=3,
            after_binding_key=second_lease.cursor_binding_key,
        )
        assert wrapped == [ordered[2], ordered[0], ordered[1]]
        await storage.release_meet_reconciliation(
            scope_key=second_lease.scope_key,
            lease_token=second_lease.lease_token,
            version=second_lease.version,
            updated_at=NOW + timedelta(seconds=3),
            last_considered_binding_key=None,
        )
        third_lease = await storage.claim_meet_reconciliation(
            owner_id="owner-1",
            org_id="org-1",
            grant_id="grant-1",
            lease_token="lease-3",
            lease_expires_at=NOW + timedelta(minutes=5),
            updated_at=NOW + timedelta(seconds=4),
        )
        return cursor, third_lease

    cursor, third_lease = asyncio.run(exercise())
    assert third_lease.cursor_binding_key == cursor
    lease_record = db.data[
        ("workspace_meet_reconciliation_leases", third_lease.scope_key)
    ]
    assert set(lease_record) == {
        "scopeKey",
        "ownerId",
        "orgId",
        "grantId",
        "version",
        "attemptCount",
        "leaseToken",
        "leaseExpiresAt",
        "updatedAt",
        "cursorBindingKey",
    }


def test_firestore_corrupt_reconciliation_scope_entry_is_rejected(monkeypatch):
    storage, db = firestore_storage(monkeypatch)
    item = numbered_binding(1)
    asyncio.run(storage.store_eligible_meet_binding(item))
    scope_path = next(
        path
        for path in db.data
        if len(path) == 4
        and path[0] == "workspace_meet_grant_scopes"
        and path[2] == "eligible_events"
    )
    db.data[scope_path]["rawTranscript"] = "must-not-project"
    with pytest.raises(MeetAutomationNotFound):
        asyncio.run(
            storage.list_eligible_meet_bindings(
                owner_id="owner-1",
                org_id="org-1",
                grant_id="grant-1",
                limit=1,
                after_binding_key=None,
            )
        )


def test_failure_reason_allowlist_rejects_hostile_text_before_firestore_access(monkeypatch):
    event_state = orchestrator()._queued_event(
        binding=binding(),
        trigger="webhook",
        event_id="push-event-1",
        transcript_name=TRANSCRIPT,
        updated_at=NOW,
    )
    with pytest.raises(ValidationError):
        AutomationEventState.model_validate(
            {**event_state.model_dump(), "reason_code": "provider said secret"}
        )

    storage = object.__new__(FirestoreStorage)

    async def forbidden_db_access():
        pytest.fail("invalid reason must fail before Firestore access")

    monkeypatch.setattr(storage, "_get_db", forbidden_db_access)
    with pytest.raises(MeetAutomationConflict, match="reason"):
        asyncio.run(
            storage.fail_meet_automation_event(
                event_key=event_state.event_key,
                lease_token="lease-token",
                version=2,
                reason_code="provider said secret",
                updated_at=NOW,
            )
        )


def test_firestore_event_transactions_queue_claim_fail_reclaim_complete_and_fence(monkeypatch):
    storage, db = firestore_storage(monkeypatch)
    event_state = orchestrator()._queued_event(
        binding=binding(),
        trigger="webhook",
        event_id="push-event-1",
        transcript_name=TRANSCRIPT,
        updated_at=NOW,
    )
    result = GoogleMeetImportResult(
        session_id="meet-import-" + "1" * 32,
        source_key="2" * 64,
        source_digest="3" * 64,
        status="completed",
        segment_count=1,
        attempt_count=1,
        idempotent_replay=False,
    )

    async def exercise():
        await storage.queue_meet_automation_event(event_state)
        first = await storage.claim_meet_automation_event(
            event_key=event_state.event_key,
            lease_token="old-token",
            lease_expires_at=NOW + timedelta(seconds=1),
            updated_at=NOW,
        )
        with pytest.raises(MeetAutomationConflict, match="stale"):
            await storage.fail_meet_automation_event(
                event_key=event_state.event_key,
                lease_token="old-token",
                version=first.event.version,
                reason_code="automation_failed",
                updated_at=NOW + timedelta(seconds=1),
            )
        reclaimed = await storage.claim_meet_automation_event(
            event_key=event_state.event_key,
            lease_token="new-token",
            lease_expires_at=NOW + timedelta(minutes=5),
            updated_at=NOW + timedelta(seconds=2),
        )
        with pytest.raises(MeetAutomationConflict, match="stale"):
            await storage.complete_meet_automation_event(
                result,
                event_key=event_state.event_key,
                lease_token="old-token",
                version=first.event.version,
                updated_at=NOW + timedelta(seconds=3),
            )
        failed = await storage.fail_meet_automation_event(
            event_key=event_state.event_key,
            lease_token="new-token",
            version=reclaimed.event.version,
            reason_code="automation_failed",
            updated_at=NOW + timedelta(seconds=3),
        )
        assert failed.status == AutomationEventStatus.FAILED
        assert db.data[
            ("workspace_meet_automation_events", event_state.event_key)
        ]["reasonCode"] == "automation_failed"
        final_claim = await storage.claim_meet_automation_event(
            event_key=event_state.event_key,
            lease_token="final-token",
            lease_expires_at=NOW + timedelta(minutes=5),
            updated_at=NOW + timedelta(seconds=4),
        )
        completed = await storage.complete_meet_automation_event(
            result,
            event_key=event_state.event_key,
            lease_token="final-token",
            version=final_claim.event.version,
            updated_at=NOW + timedelta(seconds=5),
        )
        with pytest.raises(MeetAutomationConflict, match="stale"):
            await storage.fail_meet_automation_event(
                event_key=event_state.event_key,
                lease_token="final-token",
                version=final_claim.event.version,
                reason_code="automation_failed",
                updated_at=NOW + timedelta(seconds=6),
            )
        replay = await storage.claim_meet_automation_event(
            event_key=event_state.event_key,
            lease_token="unused",
            lease_expires_at=NOW + timedelta(minutes=6),
            updated_at=NOW + timedelta(seconds=7),
        )
        return completed, replay

    completed, replay = asyncio.run(exercise())
    assert completed.status == AutomationEventStatus.COMPLETED
    assert replay.idempotent_replay is True
    record = db.data[("workspace_meet_automation_events", event_state.event_key)]
    assert not ({"transcriptText", "rawPayload", "bearerToken", "providerException"} & set(record))
    assert TRANSCRIPT == record["transcriptName"]


def http_request(path, raw, *, headers=None):
    delivered = False
    normalized = [(b"content-type", b"application/json"), *(headers or [])]

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": normalized,
            "client": ("127.0.0.1", 1),
            "server": ("test", 443),
        },
        receive,
    )


def test_routes_webhook_uses_push_auth_api_routes_use_firebase_and_missing_dependencies_503(monkeypatch):
    raw_push = json.dumps(push_payload()).encode()
    monkeypatch.setattr(backend_main, "workspace_push_token_verifier", None)
    monkeypatch.setattr(backend_main, "meet_transcript_automation_orchestrator", None)
    with pytest.raises(HTTPException) as unavailable:
        asyncio.run(
            backend_main.receive_google_workspace_meet_transcript(
                http_request("/webhooks/google-workspace/meet-transcripts", raw_push)
            )
        )
    assert unavailable.value.status_code == 503

    storage = MemoryAutomationStorage([grant()], [binding()])
    runner = orchestrator(storage)
    monkeypatch.setattr(backend_main, "workspace_push_token_verifier", FakeVerifier())
    monkeypatch.setattr(backend_main, "meet_transcript_automation_orchestrator", runner)
    with pytest.raises(HTTPException) as push_unauthorized:
        asyncio.run(
            backend_main.receive_google_workspace_meet_transcript(
                http_request("/webhooks/google-workspace/meet-transcripts", raw_push)
            )
        )
    assert push_unauthorized.value.status_code == 401

    valid_push = http_request(
        "/webhooks/google-workspace/meet-transcripts",
        raw_push,
        headers=[(b"authorization", b"Bearer header.payload.signature")],
    )
    result = asyncio.run(backend_main.receive_google_workspace_meet_transcript(valid_push))
    assert result["status"] == "completed"

    manual_raw = json.dumps(
        {"grantId": "grant-1", "calendarId": "recruiter@example.com", "calendarEventId": "event-1"}
    ).encode()
    token = set_current_auth(None)
    try:
        with pytest.raises(HTTPException) as unauthenticated:
            asyncio.run(
                backend_main.sync_eligible_google_meet_transcript(
                    http_request("/api/workspace/meet-transcripts/sync", manual_raw)
                )
            )
        assert unauthenticated.value.status_code == 401
    finally:
        reset_current_auth(token)

    token = set_current_auth(AuthContext("owner-1", "owner@example.com", "org-1"))
    try:
        monkeypatch.setattr(backend_main, "meet_transcript_automation_orchestrator", None)
        with pytest.raises(HTTPException) as missing:
            asyncio.run(
                backend_main.sync_eligible_google_meet_transcript(
                    http_request("/api/workspace/meet-transcripts/sync", manual_raw)
                )
            )
        assert missing.value.status_code == 503
    finally:
        reset_current_auth(token)


def test_asgi_middleware_bypasses_firebase_only_for_webhook_and_protects_api(monkeypatch):
    storage = MemoryAutomationStorage([grant()], [binding()])
    runner = orchestrator(storage)
    firebase_calls = []

    def reject_firebase(header, settings):
        firebase_calls.append(header)
        raise ValueError("fixed synthetic rejection")

    monkeypatch.setattr(backend_main, "workspace_push_token_verifier", FakeVerifier())
    monkeypatch.setattr(backend_main, "meet_transcript_automation_orchestrator", runner)
    monkeypatch.setattr(backend_main, "verify_bearer_token", reject_firebase)
    monkeypatch.setattr(backend_main, "settings", SimpleNamespace(auth_bypass=False))
    monkeypatch.setattr(backend_main.app.state, "ready", True)

    async def exercise():
        transport = httpx.ASGITransport(app=backend_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            webhook = await client.post(
                "/webhooks/google-workspace/meet-transcripts",
                content=json.dumps(push_payload()).encode(),
                headers={
                    "Authorization": "Bearer header.payload.signature",
                    "Content-Type": "application/json",
                },
            )
            assert webhook.status_code == 200
            assert firebase_calls == []
            protected = await client.post(
                "/api/workspace/meet-transcripts/sync",
                json={
                    "grantId": "grant-1",
                    "calendarId": "recruiter@example.com",
                    "calendarEventId": "event-1",
                },
            )
            assert protected.status_code == 401
            assert firebase_calls == [None]

    asyncio.run(exercise())
