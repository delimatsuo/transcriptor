import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from fastapi import HTTPException
from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore
from starlette.requests import Request

from backend import main as backend_main
from backend.auth import AuthContext, reset_current_auth, set_current_auth
from backend.config import Settings
from backend.sessions.reports import parse_generated_report
from backend.sessions.workspace_imports import (
    GoogleMeetImportRequest,
    MAX_ENTRY_CHARS,
    MAX_REQUEST_BYTES,
    normalize_google_meet_import,
)
from backend.workers.google_meet_import import (
    GoogleMeetImportWorker,
    ImportClaim,
    ImportJobState,
    ImportJobStatus,
    TranscriptImportConflict,
    TranscriptImportNotFound,
)
from backend.storage.firestore import FirestoreStorage


FIXTURE = Path(__file__).parent / "fixtures" / "google_meet_import.json"
NOW = datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc)


def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def request(value=None):
    return GoogleMeetImportRequest.model_validate(value or payload())


def normalized(value=None, *, owner="owner-1", org="org-1"):
    return normalize_google_meet_import(
        request(value), owner_id=owner, org_id=org, imported_at=NOW
    )


def test_normalization_is_deterministic_order_independent_and_provenance_complete():
    original = payload()
    reordered = copy.deepcopy(original)
    reordered["transcriptSessions"].reverse()
    reordered["transcriptSessions"][1]["entries"].reverse()
    reordered["transcriptSessions"].append(
        {
            "name": reordered["transcriptSessions"][1]["name"],
            "entries": [copy.deepcopy(reordered["transcriptSessions"][1]["entries"][0])],
        }
    )

    first = normalized(original)
    second = normalized(reordered)

    assert first.source_key == second.source_key
    assert first.source_digest == second.source_digest
    assert first.session.id == second.session.id
    assert [segment.model_dump() for segment in first.segments] == [
        segment.model_dump() for segment in second.segments
    ]
    assert len(first.segments) == 3
    assert first.segments[0].start_time == 0.0
    assert first.segments[1].start_time == 4.0
    assert first.segments[2].speaker == "Unknown speaker"
    assert first.segments[2].confidence == 0.0
    assert first.segments[2].start_time == 0.0
    missing = first.segment_provenance[first.segments[2].id]
    assert missing["startTimeMissing"] is True
    assert missing["endTimeMissing"] is True
    assert missing["participantNameMissing"] is True
    assert missing["confidenceMissing"] is True
    assert all(segment.is_final for segment in first.segments)
    assert set(first.report_sources) == {"candidate_name", "resume", "jd", "briefing"}


def test_digest_changes_with_content_or_notice_but_source_identity_stays_stable():
    base = normalized()
    changed = payload()
    changed["transcriptSessions"][0]["entries"][0]["text"] += " changed"
    changed_notice = payload()
    changed_notice["noticeProvenance"] += " amended"

    assert normalized(changed).source_key == base.source_key
    assert normalized(changed).source_digest != base.source_digest
    assert normalized(changed_notice).source_digest != base.source_digest


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value["transcriptSessions"][0]["entries"][0].update(
            {"startTime": "2026-09-01T14:00:00"}
        ),
        lambda value: value["transcriptSessions"][0]["entries"][0].update(
            {"startTime": 1788271200}
        ),
        lambda value: value["transcriptSessions"][0]["entries"][0].update(
            {
                "startTime": "2026-09-01T14:00:05Z",
                "endTime": "2026-09-01T14:00:04Z",
            }
        ),
        lambda value: value.update({"resumeArtifactId": None}),
        lambda value: value["transcriptSessions"][0]["entries"][0].update(
            {"text": "x" * (MAX_ENTRY_CHARS + 1)}
        ),
    ],
)
def test_malformed_or_unbounded_payloads_fail_closed(mutate):
    value = payload()
    mutate(value)
    with pytest.raises(ValidationError):
        request(value)


def test_conflicting_duplicate_identity_is_rejected_but_identical_delivery_collapses():
    value = payload()
    duplicate = copy.deepcopy(value["transcriptSessions"][0]["entries"][0])
    value["transcriptSessions"][0]["entries"].append(copy.deepcopy(duplicate))
    assert len(normalized(value).segments) == 3
    value["transcriptSessions"][0]["entries"][-1]["text"] = "different"
    with pytest.raises(ValidationError, match="different content"):
        request(value)


def test_exact_raw_json_byte_limit_is_checked_before_parsing():
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        GoogleMeetImportRequest.from_json_bytes(b" " * (MAX_REQUEST_BYTES + 1))


def test_imported_segment_ids_are_accepted_by_strict_report_evidence_parser():
    item = normalized()
    segment_id = item.segments[0].id
    raw = json.dumps(
        {
            "internal_sections": [
                {
                    "id": "summary",
                    "title": "Summary",
                    "body": "Synthetic evidence summary.",
                    "rating": None,
                    "evidence": [{"source": "transcript", "evidence_id": segment_id}],
                }
            ],
            "client_narrative": {
                "trajectory": "Synthetic career trajectory was discussed.",
                "assessment": "The available evidence describes the synthetic interview.",
                "trajectory_evidence": [
                    {"source": "context", "evidence_id": "resume"}
                ],
                "assessment_evidence": [
                    {"source": "transcript", "evidence_id": segment_id}
                ],
            },
        }
    )
    report = parse_generated_report(
        item.session.id,
        raw,
        transcript_ids={segment.id for segment in item.segments},
        note_ids=set(),
        context_ids=set(item.report_sources),
        owner_id="owner-1",
        org_id="org-1",
        now=NOW,
    )
    assert report.internal_sections[0].evidence[0].evidence_id == segment_id


class MemoryImportStorage:
    def __init__(self):
        self.jobs = {}
        self.sessions = {}
        self.transcripts = {}
        self.report_obligations = {}
        self.tombstones = set()
        self.fail_commit = False

    async def queue_transcript_import(self, item, *, updated_at):
        existing = self.jobs.get(item.source_key)
        if existing:
            if existing.owner_id != item.session.owner_id or existing.org_id != item.session.org_id:
                raise TranscriptImportNotFound("not found")
            if existing.source_digest != item.source_digest:
                raise TranscriptImportConflict("digest")
            return existing
        job = ImportJobState(
            source_key=item.source_key,
            source_digest=item.source_digest,
            session_id=item.session.id,
            owner_id=item.session.owner_id,
            org_id=item.session.org_id,
            status="queued",
            version=1,
            attempt_count=0,
            updated_at=updated_at,
        )
        self.jobs[item.source_key] = job
        return job

    async def claim_transcript_import(self, **values):
        job = self.jobs[values["source_key"]]
        if job.owner_id != values["owner_id"] or job.org_id != values["org_id"]:
            raise TranscriptImportNotFound("not found")
        if job.source_digest != values["source_digest"]:
            raise TranscriptImportConflict("digest")
        if job.status == ImportJobStatus.COMPLETED:
            return ImportClaim(job=job, idempotent_replay=True)
        if job.status == ImportJobStatus.LEASED and job.lease_expires_at > values["updated_at"]:
            raise TranscriptImportConflict("active lease")
        job = job.model_copy(update={
            "status": ImportJobStatus.LEASED,
            "version": job.version + 1,
            "attempt_count": job.attempt_count + 1,
            "lease_token": values["lease_token"],
            "lease_expires_at": values["lease_expires_at"],
            "updated_at": values["updated_at"],
        })
        self.jobs[job.source_key] = job
        return ImportClaim(job=job)

    async def commit_transcript_import(self, item, *, lease_token, version, updated_at):
        if self.fail_commit:
            raise RuntimeError("synthetic commit failure")
        job = self.jobs[item.source_key]
        if item.session.id in self.tombstones:
            raise TranscriptImportConflict("deleted")
        if (
            job.lease_token != lease_token
            or job.version != version
            or job.lease_expires_at is None
            or job.lease_expires_at <= updated_at
        ):
            raise TranscriptImportConflict("stale")
        assert item.session.id not in self.sessions
        self.sessions[item.session.id] = item.session
        self.transcripts[item.session.id] = list(item.segments)
        self.report_obligations[item.session.id] = "queued"
        job = job.model_copy(update={
            "status": ImportJobStatus.COMPLETED,
            "lease_token": None,
            "lease_expires_at": None,
            "segment_count": len(item.segments),
            "updated_at": updated_at,
        })
        self.jobs[item.source_key] = job
        return job

    async def fail_transcript_import(self, **values):
        job = self.jobs[values["source_key"]]
        if job.version == values["version"] and job.lease_token == values["lease_token"]:
            job = job.model_copy(update={
                "status": ImportJobStatus.FAILED,
                "lease_token": None,
                "lease_expires_at": None,
                "reason_code": values["reason_code"],
                "updated_at": values["updated_at"],
            })
            self.jobs[job.source_key] = job
        return job

    async def get_transcript_import_job(self, source_key, *, owner_id, org_id):
        job = self.jobs.get(source_key)
        if not job or job.owner_id != owner_id or job.org_id != org_id:
            raise TranscriptImportNotFound("not found")
        return job


def test_worker_is_durable_idempotent_and_never_constructs_runtime_manager(monkeypatch):
    import backend.sessions.manager as manager_module

    monkeypatch.setattr(
        manager_module.SessionManager,
        "create_session",
        lambda *args, **kwargs: pytest.fail("SessionManager must not be touched"),
    )
    storage = MemoryImportStorage()
    worker = GoogleMeetImportWorker(storage)
    first = asyncio.run(worker.run(request(), owner_id="owner-1", org_id="org-1", now=NOW))
    replay = asyncio.run(
        worker.run(request(), owner_id="owner-1", org_id="org-1", now=NOW + timedelta(seconds=1))
    )
    assert first.status == ImportJobStatus.COMPLETED
    assert replay.idempotent_replay is True
    assert len(storage.sessions) == len(storage.transcripts) == len(storage.report_obligations) == 1


def test_failed_commit_publishes_nothing_and_expired_or_failed_work_retries():
    storage = MemoryImportStorage()
    storage.fail_commit = True
    worker = GoogleMeetImportWorker(storage)
    with pytest.raises(RuntimeError, match="synthetic"):
        asyncio.run(worker.run(request(), owner_id="owner-1", org_id="org-1", now=NOW))
    job = next(iter(storage.jobs.values()))
    assert job.status == ImportJobStatus.FAILED
    assert job.reason_code == "atomic_commit_failed"
    assert not storage.sessions and not storage.transcripts and not storage.report_obligations
    first_attempt = job.attempt_count
    storage.fail_commit = False
    result = asyncio.run(
        worker.run(request(), owner_id="owner-1", org_id="org-1", now=NOW + timedelta(minutes=6))
    )
    assert result.status == ImportJobStatus.COMPLETED
    assert result.attempt_count == first_attempt + 1


def test_worker_samples_fresh_commit_and_failure_times_when_clock_is_injected():
    storage = MemoryImportStorage()
    ticks = iter([NOW, NOW + timedelta(minutes=6), NOW + timedelta(minutes=6, seconds=1)])
    worker = GoogleMeetImportWorker(storage, clock=lambda: next(ticks))

    with pytest.raises(TranscriptImportConflict, match="stale"):
        asyncio.run(worker.run(request(), owner_id="owner-1", org_id="org-1"))

    job = next(iter(storage.jobs.values()))
    assert job.status == ImportJobStatus.FAILED
    assert job.updated_at == NOW + timedelta(minutes=6, seconds=1)
    assert not storage.sessions and not storage.transcripts and not storage.report_obligations


def test_worker_conflict_and_scope_are_fail_closed_and_tombstone_blocks_publish():
    storage = MemoryImportStorage()
    worker = GoogleMeetImportWorker(storage)
    asyncio.run(worker.run(request(), owner_id="owner-1", org_id="org-1", now=NOW))
    with pytest.raises(TranscriptImportNotFound):
        asyncio.run(worker.status(next(iter(storage.jobs)), owner_id="owner-2", org_id="org-1"))

    conflict = payload()
    conflict["title"] = "Changed provenance"
    with pytest.raises(TranscriptImportConflict):
        asyncio.run(worker.run(request(conflict), owner_id="owner-1", org_id="org-1", now=NOW))

    blocked_storage = MemoryImportStorage()
    item = normalized()
    blocked_storage.tombstones.add(item.session.id)
    with pytest.raises(TranscriptImportConflict):
        asyncio.run(
            GoogleMeetImportWorker(blocked_storage).run(
                request(), owner_id="owner-1", org_id="org-1", now=NOW
            )
        )
    assert not blocked_storage.sessions


class AtomicSnapshot:
    def __init__(self, reference):
        self.id = reference.path[-1]
        self.exists = reference.path in reference.db.data
        self._data = copy.deepcopy(reference.db.data.get(reference.path, {}))

    def to_dict(self):
        return copy.deepcopy(self._data)


class AtomicDocument:
    def __init__(self, db, path):
        self.db = db
        self.path = path
        self.id = path[-1]

    def collection(self, name):
        return AtomicCollection(self.db, self.path + (name,))

    async def get(self, transaction=None):
        return AtomicSnapshot(self)


class AtomicCollection:
    def __init__(self, db, path):
        self.db = db
        self.path = path

    def document(self, document_id):
        return AtomicDocument(self.db, self.path + (document_id,))


class AtomicTransaction:
    def __init__(self, db):
        self.db = db
        self.operations = []

    def create(self, reference, data):
        self.operations.append(("create", reference.path, copy.deepcopy(data)))

    def set(self, reference, data, merge=False):
        self.operations.append(("merge" if merge else "set", reference.path, copy.deepcopy(data)))

    def commit(self):
        if self.db.fail_session_commit and any(path[0] == "sessions" for _, path, _ in self.operations):
            self.db.fail_session_commit = False
            raise RuntimeError("synthetic atomic commit failure")
        for operation, path, _ in self.operations:
            if operation == "create" and path in self.db.data:
                raise AlreadyExists("exists")
        next_data = copy.deepcopy(self.db.data)
        for operation, path, value in self.operations:
            if operation == "merge":
                next_data[path] = {**next_data.get(path, {}), **value}
            else:
                next_data[path] = value
        self.db.data = next_data


class AtomicDB:
    def __init__(self):
        self.data = {}
        self.fail_session_commit = False

    def collection(self, name):
        return AtomicCollection(self, (name,))

    def transaction(self):
        return AtomicTransaction(self)


def firestore_import_storage(monkeypatch, db):
    storage = FirestoreStorage(Settings(google_cloud_project="fixture-project"))

    async def get_db():
        return db

    def transactional(function):
        async def execute(transaction):
            result = await function(transaction)
            transaction.commit()
            return result

        return execute

    monkeypatch.setattr(storage, "_get_db", get_db)
    monkeypatch.setattr(firestore, "async_transactional", transactional)
    return storage


def test_firestore_worker_commit_is_atomic_exactly_once_and_preserves_provenance(monkeypatch):
    db = AtomicDB()
    worker = GoogleMeetImportWorker(firestore_import_storage(monkeypatch, db))
    first = asyncio.run(worker.run(request(), owner_id="owner-1", org_id="org-1", now=NOW))
    replay = asyncio.run(
        worker.run(request(), owner_id="owner-1", org_id="org-1", now=NOW + timedelta(seconds=2))
    )

    session_path = ("sessions", first.session_id)
    transcript_paths = [
        path for path in db.data if path[:3] == session_path + ("transcript",)
    ]
    assert first.status == replay.status == ImportJobStatus.COMPLETED
    assert replay.idempotent_replay is True
    assert len(transcript_paths) == 3
    assert all("importProvenance" in db.data[path] for path in transcript_paths)
    assert db.data[session_path + ("reports", "generation")]["status"] == "queued"
    assert session_path + ("reports", "current") not in db.data
    assert db.data[("transcript_import_jobs", first.source_key)]["segmentCount"] == 3

    changed = payload()
    changed["briefing"] += " changed"
    with pytest.raises(TranscriptImportConflict, match="different content"):
        asyncio.run(
            worker.run(request(changed), owner_id="owner-1", org_id="org-1", now=NOW)
        )
    with pytest.raises(TranscriptImportNotFound):
        asyncio.run(worker.status(first.source_key, owner_id="owner-2", org_id="org-1"))

    db.data[("session_tombstones", first.session_id)] = {
        "sessionId": first.session_id,
        "ownerId": "owner-1",
        "orgId": "org-1",
    }
    with pytest.raises(TranscriptImportConflict, match="fenced"):
        asyncio.run(
            worker.run(request(), owner_id="owner-1", org_id="org-1", now=NOW)
        )


def test_firestore_atomic_failure_and_tombstone_never_publish_completed_session(monkeypatch):
    db = AtomicDB()
    db.fail_session_commit = True
    worker = GoogleMeetImportWorker(firestore_import_storage(monkeypatch, db))
    item = normalized()
    with pytest.raises(RuntimeError, match="atomic commit"):
        asyncio.run(worker.run(request(), owner_id="owner-1", org_id="org-1", now=NOW))
    assert not any(path[0] == "sessions" for path in db.data)
    assert db.data[("transcript_import_jobs", item.source_key)]["status"] == "failed"

    fenced_db = AtomicDB()
    fenced_db.data[("session_tombstones", item.session.id)] = {
        "sessionId": item.session.id,
        "ownerId": "owner-1",
        "orgId": "org-1",
    }
    fenced_worker = GoogleMeetImportWorker(
        firestore_import_storage(monkeypatch, fenced_db)
    )
    with pytest.raises(TranscriptImportConflict, match="fenced"):
        asyncio.run(
            fenced_worker.run(request(), owner_id="owner-1", org_id="org-1", now=NOW)
        )
    assert not any(path[0] == "sessions" for path in fenced_db.data)


def test_firestore_expired_lease_cannot_commit_and_old_claim_loses_after_reclaim(monkeypatch):
    db = AtomicDB()
    storage = firestore_import_storage(monkeypatch, db)
    item = normalized()

    async def exercise():
        await storage.queue_transcript_import(item, updated_at=NOW)
        old = await storage.claim_transcript_import(
            source_key=item.source_key,
            source_digest=item.source_digest,
            session_id=item.session.id,
            owner_id="owner-1",
            org_id="org-1",
            lease_token="old-token",
            lease_expires_at=NOW + timedelta(seconds=1),
            updated_at=NOW,
        )
        with pytest.raises(TranscriptImportConflict, match="stale"):
            await storage.commit_transcript_import(
                item,
                lease_token="old-token",
                version=old.job.version,
                updated_at=NOW + timedelta(seconds=1),
            )
        assert db.data[("transcript_import_jobs", item.source_key)]["status"] == "leased"
        assert not any(path[0] == "sessions" for path in db.data)

        reclaimed = await storage.claim_transcript_import(
            source_key=item.source_key,
            source_digest=item.source_digest,
            session_id=item.session.id,
            owner_id="owner-1",
            org_id="org-1",
            lease_token="new-token",
            lease_expires_at=NOW + timedelta(minutes=10),
            updated_at=NOW + timedelta(seconds=2),
        )
        assert reclaimed.job.version == old.job.version + 1
        assert reclaimed.job.attempt_count == old.job.attempt_count + 1
        with pytest.raises(TranscriptImportConflict, match="stale"):
            await storage.commit_transcript_import(
                item,
                lease_token="old-token",
                version=old.job.version,
                updated_at=NOW + timedelta(seconds=3),
            )
        assert not any(path[0] == "sessions" for path in db.data)

        completed = await storage.commit_transcript_import(
            item,
            lease_token="new-token",
            version=reclaimed.job.version,
            updated_at=NOW + timedelta(seconds=3),
        )
        assert completed.status == ImportJobStatus.COMPLETED

    asyncio.run(exercise())
    assert ("sessions", item.session.id) in db.data


def http_json_request(raw: bytes, *, content_type=b"application/json"):
    delivered = False

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
            "path": "/api/transcript-imports/google-meet",
            "raw_path": b"/api/transcript-imports/google-meet",
            "query_string": b"",
            "headers": [(b"content-type", content_type)],
            "client": ("127.0.0.1", 1),
            "server": ("test", 443),
        },
        receive,
    )


def test_import_route_requires_real_principal_and_maps_typed_failures(monkeypatch):
    storage = MemoryImportStorage()
    monkeypatch.setattr(backend_main, "firestore_storage", storage)
    raw = json.dumps(payload()).encode("utf-8")

    token = set_current_auth(None)
    try:
        with pytest.raises(HTTPException) as unauthenticated:
            asyncio.run(backend_main.import_google_meet_transcript(http_json_request(raw)))
        assert unauthenticated.value.status_code == 401
    finally:
        reset_current_auth(token)

    token = set_current_auth(AuthContext("owner-1", "owner@example.com", "org-1"))
    try:
        result = asyncio.run(
            backend_main.import_google_meet_transcript(http_json_request(raw))
        )
        assert result["status"] == "completed"
        assert set(result) == {
            "session_id",
            "source_key",
            "source_digest",
            "status",
            "segment_count",
            "attempt_count",
            "idempotent_replay",
        }
        with pytest.raises(HTTPException) as malformed:
            asyncio.run(
                backend_main.import_google_meet_transcript(
                    http_json_request(b'{"sourceType":"GOOGLE_MEET"}')
                )
            )
        assert malformed.value.status_code == 422
        with pytest.raises(HTTPException) as oversized:
            asyncio.run(
                backend_main.import_google_meet_transcript(
                    http_json_request(b" " * (MAX_REQUEST_BYTES + 1))
                )
            )
        assert oversized.value.status_code == 422
    finally:
        reset_current_auth(token)


def test_import_status_route_is_content_free_and_cross_scope_is_not_found(monkeypatch):
    storage = MemoryImportStorage()
    worker = GoogleMeetImportWorker(storage)
    completed = asyncio.run(
        worker.run(request(), owner_id="owner-1", org_id="org-1", now=NOW)
    )
    monkeypatch.setattr(backend_main, "firestore_storage", storage)
    token = set_current_auth(AuthContext("owner-1", "owner@example.com", "org-1"))
    try:
        result = asyncio.run(backend_main.get_transcript_import_status(completed.source_key))
        assert set(result) == {
            "session_id",
            "source_key",
            "source_digest",
            "status",
            "segment_count",
            "attempt_count",
            "idempotent_replay",
        }
    finally:
        reset_current_auth(token)
    other = set_current_auth(AuthContext("owner-2", "other@example.com", "org-1"))
    try:
        with pytest.raises(HTTPException) as hidden:
            asyncio.run(backend_main.get_transcript_import_status(completed.source_key))
        assert hidden.value.status_code == 404
    finally:
        reset_current_auth(other)
