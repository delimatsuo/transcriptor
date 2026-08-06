"""Typed reconstruction of persisted interviews for restart-safe review."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError

from backend.schemas.models import Session, SessionMode, SessionStatus, TranscriptSegment


class PersistedReviewError(ValueError):
    """Raised when durable session data cannot be reconstructed safely."""


class ReviewStatus(str, Enum):
    AVAILABLE = "available"
    READY = "ready"
    SUMMARY_UNAVAILABLE = "summary_unavailable"
    TRANSCRIPT_UNAVAILABLE = "transcript_unavailable"
    INCOMPLETE = "incomplete"
    ACTIVE = "active"
    CORRUPT = "corrupt"


class RegenerationStatus(str, Enum):
    NOT_NEEDED = "not_needed"
    BLOCKED_SOURCE_CONTEXT = "blocked_source_context"
    BLOCKED_SESSION_STATE = "blocked_session_state"


class RecentInterview(BaseModel):
    id: str
    title: str
    started_at: str | None = None
    ended_at: str | None = None
    session_status: SessionStatus | None = None
    review_status: ReviewStatus


class SessionReview(BaseModel):
    session: Session
    transcript: list[TranscriptSegment]
    summary: str | None
    review_status: ReviewStatus
    regeneration_status: RegenerationStatus


def deserialize_session(session_id: str, record: dict[str, Any]) -> Session:
    """Translate a Firestore session document into the canonical model."""
    try:
        session = Session(
            id=session_id,
            mode=record["mode"],
            title=record.get("title", ""),
            started_at=record["startedAt"],
            ended_at=record.get("endedAt"),
            last_active=record.get("lastActive", record["startedAt"]),
            status=record["status"],
            notice_given=record.get("noticeGiven", False),
            speaker_map=record.get("speakerMap", {}),
            summary=record.get("summary"),
            action_items=record.get("actionItems", []),
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise PersistedReviewError("persisted session is invalid") from exc

    if session.mode == SessionMode.INTERVIEW:
        if session.status == SessionStatus.COMPLETED and session.ended_at is None:
            raise PersistedReviewError("completed interview has no end time")
        if session.status == SessionStatus.ACTIVE and session.ended_at is not None:
            raise PersistedReviewError("active interview has an end time")
    return session


def deserialize_transcript(records: list[dict[str, Any]]) -> list[TranscriptSegment]:
    """Translate ordered Firestore transcript documents into final segments."""
    segments: list[TranscriptSegment] = []
    try:
        for record in records:
            segments.append(
                TranscriptSegment(
                    id=record["id"],
                    text=record["text"],
                    speaker=record["speaker"],
                    speaker_override=record.get("speakerOverride"),
                    start_time=record["startTime"],
                    end_time=record["endTime"],
                    confidence=record["confidence"],
                    sequence_number=record["sequenceNumber"],
                    is_final=True,
                )
            )
    except (KeyError, TypeError, ValidationError) as exc:
        raise PersistedReviewError("persisted transcript is invalid") from exc

    segments.sort(key=lambda segment: segment.sequence_number)
    sequence_numbers = [segment.sequence_number for segment in segments]
    if len(sequence_numbers) != len(set(sequence_numbers)):
        raise PersistedReviewError("persisted transcript has duplicate sequence numbers")
    return segments


def build_session_review(
    session: Session,
    transcript: list[TranscriptSegment],
) -> SessionReview:
    """Build a truthful review state without invoking providers or mutating storage."""
    if session.status == SessionStatus.ACTIVE:
        review_status = ReviewStatus.ACTIVE
        regeneration_status = RegenerationStatus.BLOCKED_SESSION_STATE
    elif session.status == SessionStatus.INCOMPLETE:
        review_status = ReviewStatus.INCOMPLETE
        regeneration_status = RegenerationStatus.BLOCKED_SESSION_STATE
    elif not transcript:
        review_status = ReviewStatus.TRANSCRIPT_UNAVAILABLE
        regeneration_status = RegenerationStatus.BLOCKED_SOURCE_CONTEXT
    elif not session.summary:
        review_status = ReviewStatus.SUMMARY_UNAVAILABLE
        regeneration_status = RegenerationStatus.BLOCKED_SOURCE_CONTEXT
    else:
        review_status = ReviewStatus.READY
        regeneration_status = RegenerationStatus.NOT_NEEDED

    return SessionReview(
        session=session,
        transcript=transcript,
        summary=session.summary,
        review_status=review_status,
        regeneration_status=regeneration_status,
    )


def build_recent_interview(session: Session) -> RecentInterview:
    """Build list metadata without claiming transcript availability before reading it."""
    if session.status == SessionStatus.COMPLETED:
        review_status = ReviewStatus.AVAILABLE
    elif session.status == SessionStatus.INCOMPLETE:
        review_status = ReviewStatus.INCOMPLETE
    else:
        review_status = ReviewStatus.ACTIVE

    return RecentInterview(
        id=session.id,
        title=session.title,
        started_at=session.started_at.isoformat(),
        ended_at=session.ended_at.isoformat() if session.ended_at else None,
        session_status=session.status,
        review_status=review_status,
    )


def corrupt_recent_interview(session_id: str) -> RecentInterview:
    """Represent a malformed durable record without hiding it or exposing its content."""
    return RecentInterview(
        id=session_id,
        title="Entrevista indisponível",
        review_status=ReviewStatus.CORRUPT,
    )
