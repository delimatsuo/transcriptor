import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from google.cloud import firestore
from pydantic import ValidationError

from backend import main as backend_main
from backend.config import Settings
from backend.schemas.models import Session, SessionMode, SessionStatus, TranscriptSegment
from backend.sessions.notes import (
    CreateRecruiterNoteRequest,
    NoteKind,
    RecruiterNoteConflict,
    RecruiterNoteError,
    build_recruiter_note,
    deserialize_recruiter_notes,
)
from backend.storage.firestore import FirestoreStorage


SESSION_ID = "note-session-001"


def active_interview(**overrides):
    values = {
        "id": SESSION_ID,
        "mode": SessionMode.INTERVIEW,
        "status": SessionStatus.ACTIVE,
    }
    values.update(overrides)
    return Session(**values)


def final_segment(segment_id="seg-final", end_time=7.25):
    return TranscriptSegment(
        id=segment_id,
        text="Resposta final",
        speaker="Candidato",
        end_time=end_time,
        sequence_number=2,
        is_final=True,
    )


def request(**overrides):
    values = {
        "client_note_id": "note-client-001",
        "kind": "strength",
        "transcript_segment_id": "seg-final",
    }
    values.update(overrides)
    return CreateRecruiterNoteRequest(**values)


def test_builds_wordless_recruiter_note_with_server_derived_offset():
    created_at = datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc)
    note = build_recruiter_note(
        active_interview(),
        final_segment(),
        request(),
        created_at=created_at,
    )

    assert note.id == "note-client-001"
    assert note.kind == NoteKind.STRENGTH
    assert note.text == ""
    assert note.source == "recruiter"
    assert note.transcript_segment_id == "seg-final"
    assert note.transcript_offset_ms == 7250
    assert note.created_at == created_at


@pytest.mark.parametrize(
    ("session", "segment", "message"),
    [
        (active_interview(mode=SessionMode.MEETING), final_segment(), "interview"),
        (active_interview(status=SessionStatus.COMPLETED), final_segment(), "active"),
        (active_interview(), final_segment("seg-other"), "not found"),
        (
            active_interview(),
            final_segment("seg-final").model_copy(update={"is_final": False}),
            "not found",
        ),
        (active_interview(), final_segment(end_time=float("nan")), "invalid offset"),
    ],
)
def test_rejects_unsafe_note_anchors(session, segment, message):
    with pytest.raises(RecruiterNoteError, match=message):
        build_recruiter_note(
            session,
            segment,
            request(),
        )


def test_request_forbids_generic_notes_and_unknown_fields():
    with pytest.raises(ValidationError):
        request(kind="note")
    with pytest.raises(ValidationError):
        CreateRecruiterNoteRequest(
            client_note_id="note-client-001",
            kind="strength",
            transcript_segment_id="seg-final",
            unexpected=True,
        )


class LiveSessionManager:
    def __init__(self, session=None, segments=None):
        self.session = session
        self.segments = segments or []

    def get_session(self, _session_id):
        return self.session

    def get_transcript(self, _session_id):
        return self.segments


class NoteStorage:
    def __init__(self, records=None, session_record=None):
        self.saved = []
        self.records = records or []
        self.session_record = session_record

    async def save_recruiter_note(self, session, note_request):
        note = build_recruiter_note(session, final_segment(), note_request)
        self.saved.append(note)
        return note

    async def get_session_notes(self, _session_id):
        return self.records

    async def get_session_record(self, _session_id):
        return self.session_record


def test_endpoint_persists_once_without_provider_or_websocket_work(monkeypatch):
    storage = NoteStorage()
    manager = LiveSessionManager(active_interview(), [final_segment()])
    monkeypatch.setattr(backend_main, "session_mgr", manager)
    monkeypatch.setattr(backend_main, "firestore_storage", storage)
    monkeypatch.setattr(backend_main, "gemini_client", None)

    response = asyncio.run(backend_main.create_recruiter_note(SESSION_ID, request()))

    assert response["id"] == "note-client-001"
    assert response["transcript_offset_ms"] == 7250
    assert len(storage.saved) == 1
    assert storage.saved[0].source == "recruiter"


def test_endpoint_rejects_missing_runtime_session(monkeypatch):
    monkeypatch.setattr(backend_main, "session_mgr", LiveSessionManager())
    monkeypatch.setattr(backend_main, "firestore_storage", NoteStorage())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(backend_main.create_recruiter_note(SESSION_ID, request()))

    assert exc_info.value.status_code == 404


def test_notes_read_after_restart_and_sort_by_transcript_offset(monkeypatch):
    session_record = {
        "mode": "interview",
        "title": "Diretoria de Produto",
        "startedAt": datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc),
        "endedAt": datetime(2026, 8, 5, 15, 0, tzinfo=timezone.utc),
        "lastActive": datetime(2026, 8, 5, 15, 0, tzinfo=timezone.utc),
        "status": "completed",
        "noticeGiven": True,
    }
    later = datetime(2026, 8, 5, 14, 1, tzinfo=timezone.utc)
    records = [
        {
            "id": "note-later",
            "kind": "concern",
            "text": "",
            "transcriptSegmentId": "seg-2",
            "transcriptOffsetMs": 9000,
            "source": "recruiter",
            "createdAt": later,
        },
        {
            "id": "note-earlier",
            "kind": "bookmark",
            "text": "",
            "transcriptSegmentId": "seg-1",
            "transcriptOffsetMs": 3000,
            "source": "recruiter",
            "createdAt": later,
        },
    ]
    storage = NoteStorage(records, session_record)
    monkeypatch.setattr(backend_main, "session_mgr", LiveSessionManager())
    monkeypatch.setattr(backend_main, "firestore_storage", storage)

    response = asyncio.run(backend_main.get_recruiter_notes(SESSION_ID))

    assert [note["id"] for note in response["notes"]] == [
        "note-earlier",
        "note-later",
    ]


def test_corrupt_persisted_note_is_rejected():
    with pytest.raises(RecruiterNoteError, match="invalid"):
        deserialize_recruiter_notes(
            SESSION_ID,
            [{"id": "bad", "kind": "strength"}],
        )


class FakeNoteSnapshot:
    def __init__(self, document_id, data):
        self.id = document_id
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return dict(self._data or {})


class FakeDocument:
    def __init__(self, document_id):
        self.id = document_id
        self.data = None

    async def get(self, transaction=None):
        return FakeNoteSnapshot(self.id, self.data)


class FakeCollection:
    def __init__(self):
        self.documents = {}

    def document(self, note_id):
        return self.documents.setdefault(note_id, FakeDocument(note_id))


class FakeSessionDocument:
    def __init__(self):
        self.notes = FakeCollection()
        self.transcript = FakeCollection()

    def collection(self, name):
        if name == "notes":
            return self.notes
        if name == "transcript":
            return self.transcript
        raise AssertionError(name)


class FakeSessionsCollection:
    def __init__(self):
        self.sessions = {}

    def document(self, session_id):
        return self.sessions.setdefault(session_id, FakeSessionDocument())


class FakeNoteDB:
    def __init__(self):
        self.sessions = FakeSessionsCollection()
        self.transactions = []

    def collection(self, name):
        assert name == "sessions"
        return self.sessions

    def transaction(self):
        transaction = FakeTransaction()
        self.transactions.append(transaction)
        return transaction


class FakeTransaction:
    def __init__(self):
        self.creates = []

    def create(self, document, data):
        assert document.data is None
        document.data = dict(data)
        self.creates.append((document, dict(data)))


def configure_fake_firestore(storage, db, monkeypatch):
    async def get_db():
        return db

    monkeypatch.setattr(storage, "_get_db", get_db)
    monkeypatch.setattr(firestore, "async_transactional", lambda function: function)


def add_durable_segment(db, segment=None):
    segment = segment or final_segment()
    document = (
        db.sessions.document(SESSION_ID)
        .transcript.document(segment.id)
    )
    document.data = {
        "text": segment.text,
        "speaker": segment.speaker,
        "startTime": segment.start_time,
        "endTime": segment.end_time,
        "confidence": segment.confidence,
        "sequenceNumber": segment.sequence_number,
    }


def test_firestore_note_create_is_idempotent_and_rejects_identity_reuse(monkeypatch):
    storage = FirestoreStorage(Settings(google_cloud_project="fixture-project"))
    db = FakeNoteDB()
    configure_fake_firestore(storage, db, monkeypatch)
    add_durable_segment(db)

    first = asyncio.run(storage.save_recruiter_note(active_interview(), request()))
    second = asyncio.run(storage.save_recruiter_note(active_interview(), request()))

    assert second == first
    assert second.created_at == first.created_at
    assert sum(len(transaction.creates) for transaction in db.transactions) == 1

    with pytest.raises(RecruiterNoteConflict, match="different evidence"):
        asyncio.run(
            storage.save_recruiter_note(
                active_interview(),
                request(kind="concern"),
            )
        )


def test_endpoint_rejects_in_memory_final_when_anchor_is_not_durable(monkeypatch):
    storage = FirestoreStorage(Settings(google_cloud_project="fixture-project"))
    db = FakeNoteDB()
    configure_fake_firestore(storage, db, monkeypatch)
    monkeypatch.setattr(
        backend_main,
        "session_mgr",
        LiveSessionManager(active_interview(), [final_segment()]),
    )
    monkeypatch.setattr(backend_main, "firestore_storage", storage)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(backend_main.create_recruiter_note(SESSION_ID, request()))

    assert exc_info.value.status_code == 409
    assert "not durable" in exc_info.value.detail
    note_document = db.sessions.document(SESSION_ID).notes.document("note-client-001")
    assert note_document.data is None
    assert all(not transaction.creates for transaction in db.transactions)
