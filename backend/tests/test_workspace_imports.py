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
from backend.sessions.reports import (
    ApproveInterviewReportRequest,
    UpdateInterviewReportRequest,
    approved_client_report,
    parse_generated_report,
)
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
from backend.workers.interview_report import (
    DurableInterviewReportWorker,
    ReportGenerationConflict,
    ReportGenerationNotFound,
    ReportGenerationStatus,
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


def test_report_sources_use_utf8_byte_bounds_not_character_counts():
    ordinary = payload()
    ordinary["resumeText"] = "Experiência internacional — produto e liderança."
    assert request(ordinary).resume_text == ordinary["resumeText"]

    oversized = payload()
    oversized["resumeText"] = "😀" * 300_000
    raw = json.dumps(oversized, ensure_ascii=False).encode("utf-8")
    assert len(raw) < MAX_REQUEST_BYTES
    with pytest.raises(ValidationError, match="UTF-8 bytes"):
        GoogleMeetImportRequest.from_json_bytes(raw)


def test_raw_json_rejects_recursive_duplicate_keys_and_epoch_strings():
    raw = FIXTURE.read_text(encoding="utf-8")
    duplicate = raw.replace(
        '"text": "Welcome to the synthetic interview."',
        '"text": "first", "text": "Welcome to the synthetic interview."',
    )
    with pytest.raises(ValueError, match="duplicate JSON object key: text"):
        GoogleMeetImportRequest.from_json_bytes(duplicate.encode("utf-8"))

    epoch = payload()
    epoch["transcriptSessions"][0]["entries"][1]["startTime"] = "1788271200"
    with pytest.raises(ValidationError, match="RFC3339"):
        request(epoch)

    offset = payload()
    offset["transcriptSessions"][0]["entries"][1]["startTime"] = (
        "2026-09-01T10:00:00-04:00"
    )
    assert request(offset).transcript_sessions[0].entries[1].start_time == datetime(
        2026, 9, 1, 10, 0, tzinfo=timezone(timedelta(hours=-4))
    )


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


def test_same_provider_artifact_is_tenant_scoped_and_imports_independently():
    storage = MemoryImportStorage()
    worker = GoogleMeetImportWorker(storage)
    owner_one = asyncio.run(
        worker.run(request(), owner_id="owner-1", org_id="org-1", now=NOW)
    )
    owner_two = asyncio.run(
        worker.run(request(), owner_id="owner-2", org_id="org-1", now=NOW)
    )
    org_two = asyncio.run(
        worker.run(request(), owner_id="owner-1", org_id="org-2", now=NOW)
    )

    assert len({owner_one.source_key, owner_two.source_key, org_two.source_key}) == 3
    assert len({owner_one.session_id, owner_two.session_id, org_two.session_id}) == 3
    assert len(storage.sessions) == 3


def test_firestore_import_job_and_tombstone_probes_are_non_enumerating(monkeypatch):
    db = AtomicDB()
    storage = firestore_import_storage(monkeypatch, db)
    owner_item = normalized()
    other_item = normalized(owner="owner-2")

    async def exercise():
        await storage.queue_transcript_import(owner_item, updated_at=NOW)

        with pytest.raises(TranscriptImportNotFound):
            await storage.claim_transcript_import(
                source_key=owner_item.source_key,
                source_digest=owner_item.source_digest,
                session_id=owner_item.session.id,
                owner_id="owner-2",
                org_id="org-1",
                lease_token="cross-scope-token",
                lease_expires_at=NOW + timedelta(minutes=5),
                updated_at=NOW,
            )
        live_state = copy.deepcopy(db.data)
        assert db.data == live_state

        db.data[("session_tombstones", owner_item.session.id)] = {
            "sessionId": owner_item.session.id,
            "ownerId": "owner-1",
            "orgId": "org-1",
        }
        deleted_state = copy.deepcopy(db.data)
        with pytest.raises(TranscriptImportNotFound):
            await storage.claim_transcript_import(
                source_key=owner_item.source_key,
                source_digest=owner_item.source_digest,
                session_id=owner_item.session.id,
                owner_id="owner-2",
                org_id="org-1",
                lease_token="cross-scope-token",
                lease_expires_at=NOW + timedelta(minutes=5),
                updated_at=NOW,
            )
        assert db.data == deleted_state

        db.data[("session_tombstones", other_item.session.id)] = {
            "sessionId": other_item.session.id,
            "ownerId": "owner-1",
            "orgId": "org-1",
        }
        preclaim_state = copy.deepcopy(db.data)
        with pytest.raises(TranscriptImportNotFound):
            await storage.queue_transcript_import(other_item, updated_at=NOW)
        assert db.data == preclaim_state

    asyncio.run(exercise())


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

    def order_by(self, *_args, **_kwargs):
        return self

    def stream(self):
        collection = self

        async def generate():
            children = sorted(
                path
                for path in collection.db.data
                if len(path) == len(collection.path) + 1
                and path[: len(collection.path)] == collection.path
            )
            for path in children:
                yield AtomicSnapshot(AtomicDocument(collection.db, path))

        return generate()


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


def imported_report_json(item):
    segment_id = item.segments[0].id
    return json.dumps(
        {
            "internal_sections": [
                {
                    "id": "summary",
                    "title": "Summary",
                    "body": "Synthetic imported interview evidence.",
                    "rating": None,
                    "evidence": [
                        {"source": "transcript", "evidence_id": segment_id}
                    ],
                }
            ],
            "client_narrative": {
                "trajectory": "Synthetic career context was discussed.",
                "assessment": "The evidence describes the imported interview.",
                "trajectory_evidence": [
                    {"source": "context", "evidence_id": "resume"}
                ],
                "assessment_evidence": [
                    {"source": "transcript", "evidence_id": segment_id}
                ],
            },
        }
    )


class FakeReportGenerator:
    def __init__(self, raw, *, started=None, release=None):
        self.raw = raw
        self.started = started
        self.release = release
        self.calls = 0

    async def generate(self, **_kwargs):
        self.calls += 1
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        return self.raw


def test_import_report_edit_approve_export_and_duplicate_delivery_are_exactly_once(monkeypatch):
    db = AtomicDB()
    storage = firestore_import_storage(monkeypatch, db)
    item = normalized()
    generator = FakeReportGenerator(imported_report_json(item))

    async def exercise():
        imported = await GoogleMeetImportWorker(storage).run(
            request(), owner_id="owner-1", org_id="org-1", now=NOW
        )
        worker = DurableInterviewReportWorker(storage, generator)
        draft = await worker.run(
            imported.session_id,
            owner_id="owner-1",
            org_id="org-1",
            now=NOW + timedelta(seconds=1),
        )
        replay = await worker.run(
            imported.session_id,
            owner_id="owner-1",
            org_id="org-1",
            now=NOW + timedelta(seconds=2),
        )
        assert replay == draft
        assert generator.calls == 1
        updated = await storage.update_interview_report(
            imported.session_id,
            UpdateInterviewReportRequest(
                expected_version=1,
                sections=[
                    {"id": "summary", "body": "Reviewed imported evidence."}
                ],
                client_narrative={
                    "trajectory": "Reviewed synthetic career context.",
                    "assessment": "Reviewed evidence describes the interview.",
                },
            ),
            owner_id="owner-1",
            org_id="org-1",
        )
        approved = await storage.approve_interview_report(
            imported.session_id,
            ApproveInterviewReportRequest(expected_version=updated.version),
            owner_id="owner-1",
            org_id="org-1",
        )
        return imported, approved

    imported, approved = asyncio.run(exercise())
    exported = approved_client_report(approved)
    assert exported.version == 2
    assert approved.status.value == "approved"
    assert db.data[("sessions", imported.session_id)]["summary"].startswith(
        "## Rascunho gerado por IA"
    )
    generation = db.data[
        ("sessions", imported.session_id, "reports", "generation")
    ]
    assert generation["status"] == "ready"
    assert generation["attemptCount"] == 1


def test_active_report_lease_allows_at_most_one_provider_invocation(monkeypatch):
    db = AtomicDB()
    storage = firestore_import_storage(monkeypatch, db)
    item = normalized()
    asyncio.run(
        GoogleMeetImportWorker(storage).run(
            request(), owner_id="owner-1", org_id="org-1", now=NOW
        )
    )

    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()
        generator = FakeReportGenerator(
            imported_report_json(item), started=started, release=release
        )
        first_worker = DurableInterviewReportWorker(storage, generator)
        second_worker = DurableInterviewReportWorker(storage, generator)
        first = asyncio.create_task(
            first_worker.run(
                item.session.id,
                owner_id="owner-1",
                org_id="org-1",
                now=NOW + timedelta(seconds=1),
            )
        )
        await started.wait()
        with pytest.raises(ReportGenerationConflict, match="active lease"):
            await second_worker.run(
                item.session.id,
                owner_id="owner-1",
                org_id="org-1",
                now=NOW + timedelta(seconds=2),
            )
        assert generator.calls == 1
        release.set()
        await first
        return generator.calls

    assert asyncio.run(exercise()) == 1


def test_report_completion_and_failure_after_deletion_perform_zero_writes(monkeypatch):
    db = AtomicDB()
    storage = firestore_import_storage(monkeypatch, db)
    item = normalized()
    asyncio.run(
        GoogleMeetImportWorker(storage).run(
            request(), owner_id="owner-1", org_id="org-1", now=NOW
        )
    )

    async def exercise():
        claim = await storage.claim_report_generation(
            item.session.id,
            owner_id="owner-1",
            org_id="org-1",
            lease_token="paused-token",
            lease_expires_at=NOW + timedelta(minutes=5),
            updated_at=NOW + timedelta(seconds=1),
        )
        report = parse_generated_report(
            item.session.id,
            imported_report_json(item),
            transcript_ids={segment.id for segment in item.segments},
            note_ids=set(),
            context_ids=set(item.report_sources),
            owner_id="owner-1",
            org_id="org-1",
            now=NOW,
        )
        db.data = {
            path: value
            for path, value in db.data.items()
            if path[0] != "sessions"
        }
        db.data[("session_tombstones", item.session.id)] = {
            "sessionId": item.session.id,
            "ownerId": "owner-1",
            "orgId": "org-1",
        }
        fenced = copy.deepcopy(db.data)
        with pytest.raises(ReportGenerationNotFound):
            await storage.complete_report_generation(
                report,
                lease_token="paused-token",
                version=claim.job.version,
                updated_at=NOW + timedelta(seconds=2),
            )
        assert db.data == fenced
        with pytest.raises(ReportGenerationNotFound):
            await storage.fail_report_generation(
                item.session.id,
                owner_id="owner-1",
                org_id="org-1",
                lease_token="paused-token",
                version=claim.job.version,
                reason_code="provider_or_validation_failure",
                updated_at=NOW + timedelta(seconds=2),
            )
        assert db.data == fenced

    asyncio.run(exercise())


def test_expired_report_lease_reclaims_and_old_claim_cannot_publish(monkeypatch):
    db = AtomicDB()
    storage = firestore_import_storage(monkeypatch, db)
    item = normalized()
    asyncio.run(
        GoogleMeetImportWorker(storage).run(
            request(), owner_id="owner-1", org_id="org-1", now=NOW
        )
    )
    report = parse_generated_report(
        item.session.id,
        imported_report_json(item),
        transcript_ids={segment.id for segment in item.segments},
        note_ids=set(),
        context_ids=set(item.report_sources),
        owner_id="owner-1",
        org_id="org-1",
        now=NOW,
    )

    async def exercise():
        old = await storage.claim_report_generation(
            item.session.id,
            owner_id="owner-1",
            org_id="org-1",
            lease_token="old-report-token",
            lease_expires_at=NOW + timedelta(seconds=1),
            updated_at=NOW,
        )
        with pytest.raises(ReportGenerationConflict, match="stale"):
            await storage.fail_report_generation(
                item.session.id,
                owner_id="owner-1",
                org_id="org-1",
                lease_token="old-report-token",
                version=old.job.version,
                reason_code="provider_or_validation_failure",
                updated_at=NOW + timedelta(seconds=2),
            )
        assert db.data[
            ("sessions", item.session.id, "reports", "generation")
        ]["status"] == "generating"
        reclaimed = await storage.claim_report_generation(
            item.session.id,
            owner_id="owner-1",
            org_id="org-1",
            lease_token="new-report-token",
            lease_expires_at=NOW + timedelta(minutes=5),
            updated_at=NOW + timedelta(seconds=2),
        )
        assert reclaimed.job.version == old.job.version + 1
        with pytest.raises(ReportGenerationConflict, match="stale"):
            await storage.complete_report_generation(
                report,
                lease_token="old-report-token",
                version=old.job.version,
                updated_at=NOW + timedelta(seconds=3),
            )
        assert ("sessions", item.session.id, "reports", "current") not in db.data
        await storage.complete_report_generation(
            report,
            lease_token="new-report-token",
            version=reclaimed.job.version,
            updated_at=NOW + timedelta(seconds=3),
        )

    asyncio.run(exercise())
    assert db.data[
        ("sessions", item.session.id, "reports", "generation")
    ]["status"] == "ready"


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
    monkeypatch.setattr(backend_main, "gemini_client", object())
    monkeypatch.setattr(backend_main, "settings", object())
    report_calls = []

    async def fail_durable_report(session_id, *, owner_id, org_id):
        report_calls.append((session_id, owner_id, org_id))
        raise RuntimeError("synthetic report failure")

    monkeypatch.setattr(
        backend_main, "_run_durable_interview_report", fail_durable_report
    )
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
        assert report_calls == [(result["session_id"], "owner-1", "org-1")]
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


def test_import_route_generic_failures_never_log_hostile_content(monkeypatch):
    sentinel = "HOSTILE_TRANSCRIPT_OR_REPORT_CONTENT_SENTINEL"

    class ContentFreeLogger:
        def __init__(self):
            self.errors = []

        def exception(self, *_args, **_kwargs):
            pytest.fail("manual import route must not attach an exception traceback")

        def error(self, event, **kwargs):
            assert sentinel not in repr((event, kwargs))
            self.errors.append((event, kwargs))

    class HostileCommitStorage(MemoryImportStorage):
        async def commit_transcript_import(self, *_args, **_kwargs):
            try:
                raise ValueError(sentinel)
            except ValueError as cause:
                raise RuntimeError(sentinel) from cause

    logger = ContentFreeLogger()
    monkeypatch.setattr(backend_main, "logger", logger)
    monkeypatch.setattr(backend_main, "gemini_client", None)
    monkeypatch.setattr(backend_main, "settings", None)
    raw = json.dumps(payload()).encode("utf-8")
    token = set_current_auth(AuthContext("owner-1", "owner@example.com", "org-1"))
    try:
        failed_storage = HostileCommitStorage()
        monkeypatch.setattr(backend_main, "firestore_storage", failed_storage)
        with pytest.raises(HTTPException) as failed_import:
            asyncio.run(
                backend_main.import_google_meet_transcript(http_json_request(raw))
            )
        assert failed_import.value.status_code == 503
        assert failed_import.value.detail == "Transcript import failed and can be retried"
        failed_job = next(iter(failed_storage.jobs.values()))
        assert failed_job.status == ImportJobStatus.FAILED
        assert not failed_storage.sessions

        completed_storage = MemoryImportStorage()
        monkeypatch.setattr(backend_main, "firestore_storage", completed_storage)
        monkeypatch.setattr(backend_main, "gemini_client", object())
        monkeypatch.setattr(backend_main, "settings", object())

        async def hostile_report_failure(*_args, **_kwargs):
            try:
                raise ValueError(sentinel)
            except ValueError as cause:
                raise RuntimeError(sentinel) from cause

        monkeypatch.setattr(
            backend_main,
            "_run_durable_interview_report",
            hostile_report_failure,
        )
        completed = asyncio.run(
            backend_main.import_google_meet_transcript(http_json_request(raw))
        )
        assert completed["status"] == "completed"
        assert completed["session_id"] in completed_storage.sessions
        assert len(completed_storage.report_obligations) == 1
    finally:
        reset_current_auth(token)

    assert logger.errors == [
        (
            "google_meet_transcript_import_failed",
            {"reason_code": "import_failed"},
        ),
        (
            "google_meet_report_generation_failed",
            {
                "session_id": completed["session_id"],
                "reason_code": "report_generation_failed",
            },
        ),
    ]


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
