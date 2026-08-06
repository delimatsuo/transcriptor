"""Simple durable recruiter notes anchored to final transcript segments."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.schemas.models import Session, SessionMode, SessionStatus, TranscriptSegment


IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


class RecruiterNoteError(ValueError):
    """Raised when a note cannot be anchored or reconstructed safely."""


class RecruiterNoteConflict(RecruiterNoteError):
    """Raised when one stable note ID is reused for different evidence."""


class NoteKind(str, Enum):
    NOTE = "note"
    BOOKMARK = "bookmark"
    CONCERN = "concern"
    STRENGTH = "strength"
    FOLLOW_UP = "follow_up"


class CreateRecruiterNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_note_id: str = Field(pattern=IDENTIFIER_PATTERN)
    kind: Literal[
        NoteKind.BOOKMARK,
        NoteKind.CONCERN,
        NoteKind.STRENGTH,
        NoteKind.FOLLOW_UP,
    ]
    transcript_segment_id: str = Field(pattern=IDENTIFIER_PATTERN)


class RecruiterNote(BaseModel):
    id: str
    session_id: str
    kind: NoteKind
    text: str = ""
    transcript_segment_id: str
    transcript_offset_ms: int = Field(ge=0)
    source: Literal["recruiter"] = "recruiter"
    created_at: datetime


def build_recruiter_note(
    session: Session,
    segment: TranscriptSegment,
    request: CreateRecruiterNoteRequest,
    *,
    created_at: datetime | None = None,
) -> RecruiterNote:
    """Validate a live evidence anchor and derive its offset server-side."""
    if session.mode != SessionMode.INTERVIEW:
        raise RecruiterNoteError("recruiter notes require an interview session")
    if session.status != SessionStatus.ACTIVE:
        raise RecruiterNoteError("recruiter notes require an active session")

    if segment.id != request.transcript_segment_id or not segment.is_final:
        raise RecruiterNoteError("final transcript segment not found in session")
    if not math.isfinite(segment.end_time) or segment.end_time < 0:
        raise RecruiterNoteError("transcript segment has an invalid offset")

    return RecruiterNote(
        id=request.client_note_id,
        session_id=session.id,
        kind=request.kind,
        transcript_segment_id=segment.id,
        transcript_offset_ms=round(segment.end_time * 1000),
        created_at=created_at or datetime.now(timezone.utc),
    )


def deserialize_recruiter_notes(
    session_id: str,
    records: list[dict[str, Any]],
) -> list[RecruiterNote]:
    """Translate Firestore note documents into the stable API model."""
    try:
        notes = [
            RecruiterNote(
                id=record["id"],
                session_id=session_id,
                kind=record["kind"],
                text=record.get("text", ""),
                transcript_segment_id=record["transcriptSegmentId"],
                transcript_offset_ms=record["transcriptOffsetMs"],
                source=record["source"],
                created_at=record["createdAt"],
            )
            for record in records
        ]
    except (KeyError, TypeError, ValidationError) as exc:
        raise RecruiterNoteError("persisted recruiter note is invalid") from exc

    note_ids = [note.id for note in notes]
    if len(note_ids) != len(set(note_ids)):
        raise RecruiterNoteError("persisted recruiter notes contain duplicate ids")
    if any(
        note.created_at.tzinfo is None or note.created_at.utcoffset() is None
        for note in notes
    ):
        raise RecruiterNoteError("persisted recruiter note timestamp is invalid")

    return sorted(
        notes,
        key=lambda note: (note.transcript_offset_ms, note.created_at, note.id),
    )


def same_note_payload(left: RecruiterNote, right: RecruiterNote) -> bool:
    """Compare immutable note content while ignoring persistence time."""
    return (
        left.id,
        left.session_id,
        left.kind,
        left.text,
        left.transcript_segment_id,
        left.transcript_offset_ms,
        left.source,
    ) == (
        right.id,
        right.session_id,
        right.kind,
        right.text,
        right.transcript_segment_id,
        right.transcript_offset_ms,
        right.source,
    )
