import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from backend import main as backend_main
from backend.schemas.models import SessionStatus
from backend.sessions.review import (
    PersistedReviewError,
    RegenerationStatus,
    ReviewStatus,
    build_session_review,
    deserialize_session,
    deserialize_transcript,
)


SESSION_ID = "review-session-001"


def session_record(**overrides):
    started = datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc)
    record = {
        "id": SESSION_ID,
        "mode": "interview",
        "title": "Diretoria de Produto",
        "startedAt": started,
        "endedAt": datetime(2026, 8, 5, 15, 0, tzinfo=timezone.utc),
        "lastActive": datetime(2026, 8, 5, 15, 0, tzinfo=timezone.utc),
        "status": "completed",
        "noticeGiven": True,
        "speakerMap": {},
        "summary": "Avaliação final persistida",
        "actionItems": [],
    }
    record.update(overrides)
    return record


def transcript_records():
    return [
        {
            "id": "seg-2",
            "text": "Resposta da candidata",
            "speaker": "Candidato",
            "speakerOverride": "Candidata",
            "startTime": 4.0,
            "endTime": 7.0,
            "confidence": 0.93,
            "sequenceNumber": 2,
        },
        {
            "id": "seg-1",
            "text": "Pergunta da recrutadora",
            "speaker": "Entrevistador",
            "startTime": 1.0,
            "endTime": 3.0,
            "confidence": 0.97,
            "sequenceNumber": 1,
        },
    ]


def test_reconstructs_completed_interview_and_orders_final_transcript():
    session = deserialize_session(SESSION_ID, session_record())
    transcript = deserialize_transcript(transcript_records())
    review = build_session_review(session, transcript)

    assert session.status == SessionStatus.COMPLETED
    assert [segment.id for segment in transcript] == ["seg-1", "seg-2"]
    assert transcript[1].speaker_override == "Candidata"
    assert all(segment.is_final for segment in transcript)
    assert review.review_status == ReviewStatus.READY
    assert review.regeneration_status == RegenerationStatus.NOT_NEEDED


def test_dual_source_sequence_numbers_can_restart_per_source():
    records = transcript_records()
    records[1]["sequenceNumber"] = records[0]["sequenceNumber"]

    transcript = deserialize_transcript(records)

    assert [segment.id for segment in transcript] == ["seg-1", "seg-2"]
    assert [segment.sequence_number for segment in transcript] == [2, 2]


def test_session_scoped_sequence_numbers_must_be_unique():
    records = transcript_records()
    records[0]["sequenceScope"] = "session"
    records[1]["sequenceScope"] = "session"
    records[1]["sequenceNumber"] = records[0]["sequenceNumber"]

    with pytest.raises(PersistedReviewError, match="duplicate session"):
        deserialize_transcript(records)


def test_mixed_transcript_sequence_scopes_are_rejected():
    records = transcript_records()
    records[0]["sequenceScope"] = "session"

    with pytest.raises(PersistedReviewError, match="mixed sequence scopes"):
        deserialize_transcript(records)


def test_missing_summary_blocks_regeneration_instead_of_degrading():
    session = deserialize_session(SESSION_ID, session_record(summary=None))
    review = build_session_review(session, deserialize_transcript(transcript_records()))

    assert review.review_status == ReviewStatus.SUMMARY_UNAVAILABLE
    assert review.regeneration_status == RegenerationStatus.BLOCKED_SOURCE_CONTEXT


def test_incomplete_session_is_not_presented_as_completed_review():
    session = deserialize_session(
        SESSION_ID,
        session_record(status="incomplete", summary=None),
    )
    review = build_session_review(session, deserialize_transcript(transcript_records()))

    assert review.review_status == ReviewStatus.INCOMPLETE
    assert review.regeneration_status == RegenerationStatus.BLOCKED_SESSION_STATE


def test_active_and_missing_transcript_states_are_explicit():
    active = deserialize_session(
        SESSION_ID,
        session_record(status="active", endedAt=None, summary=None),
    )
    active_review = build_session_review(active, [])
    assert active_review.review_status == ReviewStatus.ACTIVE
    assert active_review.regeneration_status == RegenerationStatus.BLOCKED_SESSION_STATE

    completed = deserialize_session(SESSION_ID, session_record())
    missing_transcript_review = build_session_review(completed, [])
    assert missing_transcript_review.review_status == ReviewStatus.TRANSCRIPT_UNAVAILABLE
    assert (
        missing_transcript_review.regeneration_status
        == RegenerationStatus.BLOCKED_SOURCE_CONTEXT
    )


def test_corrupt_session_and_duplicate_transcript_sequences_are_rejected():
    with pytest.raises(PersistedReviewError, match="no end time"):
        deserialize_session(SESSION_ID, session_record(endedAt=None))

    duplicate = transcript_records()
    duplicate[1]["speaker"] = duplicate[0]["speaker"]
    duplicate[1]["sequenceNumber"] = 2
    with pytest.raises(PersistedReviewError, match="duplicate source"):
        deserialize_transcript(duplicate)


class EmptySessionManager:
    def get_session(self, _session_id):
        return None

    def get_transcript(self, _session_id):
        return []


class ReadOnlyFirestore:
    def __init__(self, record=None, transcript=None):
        self.record = record
        self.transcript = transcript or []
        self.reads = []

    async def get_session_record(self, session_id):
        self.reads.append(("session", session_id))
        return self.record

    async def get_session_transcript(self, session_id):
        self.reads.append(("transcript", session_id))
        return self.transcript

    async def list_sessions(self):
        self.reads.append(("list", None))
        return [self.record] if self.record else []


def test_review_endpoint_reads_firestore_after_restart_without_runtime_work(monkeypatch):
    storage = ReadOnlyFirestore(session_record(), transcript_records())
    monkeypatch.setattr(backend_main, "session_mgr", EmptySessionManager())
    monkeypatch.setattr(backend_main, "firestore_storage", storage)
    monkeypatch.setattr(backend_main, "gemini_client", None)

    response = asyncio.run(backend_main.get_session_review(SESSION_ID))

    assert response["review_status"] == "ready"
    assert response["summary"] == "Avaliação final persistida"
    assert [segment["id"] for segment in response["transcript"]] == ["seg-1", "seg-2"]
    assert storage.reads == [("session", SESSION_ID), ("transcript", SESSION_ID)]


def test_existing_read_endpoints_fall_back_to_firestore_after_restart(monkeypatch):
    storage = ReadOnlyFirestore(session_record(), transcript_records())
    monkeypatch.setattr(backend_main, "session_mgr", EmptySessionManager())
    monkeypatch.setattr(backend_main, "firestore_storage", storage)

    session = asyncio.run(backend_main.get_session(SESSION_ID))
    transcript = asyncio.run(backend_main.get_transcript(SESSION_ID))

    assert session["id"] == SESSION_ID
    assert transcript["segments"][1]["speaker_override"] == "Candidata"


def test_recent_interviews_exposes_incomplete_and_corrupt_states(monkeypatch):
    incomplete = session_record(
        id="incomplete-001",
        status="incomplete",
        summary=None,
    )
    corrupt = session_record(id="corrupt-001", endedAt=None)
    storage = ReadOnlyFirestore()

    async def list_sessions():
        return [session_record(), incomplete, corrupt, {"id": "meeting", "mode": "meeting"}]

    storage.list_sessions = list_sessions
    monkeypatch.setattr(backend_main, "firestore_storage", storage)

    response = asyncio.run(backend_main.list_recent_interviews())

    assert [item["review_status"] for item in response["interviews"]] == [
        "available",
        "incomplete",
        "corrupt",
    ]


def test_missing_review_is_explicit_404(monkeypatch):
    monkeypatch.setattr(backend_main, "firestore_storage", ReadOnlyFirestore())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(backend_main.get_session_review("missing"))

    assert exc_info.value.status_code == 404


def test_corrupt_persisted_review_is_explicit_conflict(monkeypatch):
    storage = ReadOnlyFirestore(session_record(endedAt=None), transcript_records())
    monkeypatch.setattr(backend_main, "firestore_storage", storage)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(backend_main.get_session_review(SESSION_ID))

    assert exc_info.value.status_code == 409
