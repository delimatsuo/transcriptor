"""Shared Pydantic models for sessions, transcripts, and WebSocket messages."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# --- Enums ---

class SessionMode(str, Enum):
    MEETING = "meeting"
    INTERVIEW = "interview"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"


class WSMessageType(str, Enum):
    TRANSCRIPT_DELTA = "transcript_delta"
    SUMMARY_UPDATE = "summary_update"
    SUGGESTION = "suggestion"
    SESSION_STATE = "session_state"
    CONNECTION_STATUS = "connection_status"
    COMPANION_HEALTH = "companion_health"
    COVERAGE_GAP = "coverage_gap"
    ERROR = "error"
    SPEAKER_RELABEL_BATCH = "speaker_relabel_batch"


class ConnectionHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"


class ErrorSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"
    FATAL = "fatal"


# --- Transcript ---

class TranscriptSegment(BaseModel):
    """A single utterance in the transcript."""
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    text: str
    speaker: str = "Speaker 1"
    speaker_override: str | None = None
    start_time: float = 0.0  # seconds from session start
    end_time: float = 0.0
    confidence: float = 0.0
    sequence_number: int = 0
    # STT sequence counters are source-local.  SessionManager normalizes
    # sequence_number for durable ordering while retaining this provenance
    # field for legacy/source-scoped reconstruction.  It is intentionally not
    # exposed in API/WebSocket model dumps.
    source_sequence_number: int | None = Field(default=None, exclude=True)
    is_final: bool = False


# --- Session ---

class ActionItem(BaseModel):
    text: str
    assignee: str | None = None
    completed: bool = False


class Session(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    mode: SessionMode = SessionMode.MEETING
    title: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    last_active: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: SessionStatus = SessionStatus.ACTIVE
    notice_given: bool = False  # candidate informed of transcription (LGPD notice)
    speaker_map: dict[str, str] = Field(default_factory=dict)
    summary: str | None = None
    action_items: list[ActionItem] = Field(default_factory=list)
    # A child-write failure is durable session metadata, not a silent absence
    # from the transcript.  "pending" remains until replay accepts every
    # final segment and the terminal parent write records completion.
    transcript_durability: str = "complete"
    transcript_failure_count: int = 0
    # Optional for backwards-compatible deserialization; HTTP-created records
    # always receive server-derived values from the authenticated principal.
    owner_id: str | None = None
    org_id: str | None = None


# --- WebSocket Messages ---

class TranscriptDelta(BaseModel):
    segment: TranscriptSegment


class SummaryUpdate(BaseModel):
    text: str
    is_final: bool = False
    covering_from: int = 0  # sequence number range
    covering_to: int = 0


class Suggestion(BaseModel):
    questions: list[str] = Field(default_factory=list)  # backward compat
    markdown: str = ""  # full structured response (preferred)
    context: str = ""  # brief explanation of why these questions


class SessionState(BaseModel):
    """Full snapshot sent on reconnect."""
    session: Session
    transcript: list[TranscriptSegment]
    latest_summary: str | None = None
    pending_suggestions: list[str] = Field(default_factory=list)


class ConnectionStatusPayload(BaseModel):
    stt_health: ConnectionHealth = ConnectionHealth.HEALTHY
    ws_health: ConnectionHealth = ConnectionHealth.HEALTHY
    message: str = ""


class SourceHealthReport(BaseModel):
    """Mirrors frontend/src/types/ws.ts SourceHealthReport verbatim."""
    microphone: str = "unknown"
    system_audio: str = "unknown"


class CompanionHealthPayload(BaseModel):
    """Mirrors frontend/src/types/ws.ts CompanionHealthPayload verbatim."""
    physical_capture: str = "unknown"
    sources: SourceHealthReport
    message: str | None = None


class CoverageGapSegment(BaseModel):
    """Mirrors frontend/src/types/ws.ts CoverageGapSegment verbatim."""
    id: str
    source: str
    start_ms: float
    end_ms: float | None = None
    reason: str = "unknown"


class CoverageGapPayload(BaseModel):
    """Mirrors frontend/src/types/ws.ts CoverageGapPayload verbatim."""
    gap: CoverageGapSegment


class ErrorPayload(BaseModel):
    severity: ErrorSeverity
    message: str
    code: str = ""


class SetContextRequest(BaseModel):
    doc_type: str
    text: str


# --- Active Speaker (Chrome Extension) ---

class ActiveSpeakerEvent(BaseModel):
    participant_name: str
    timestamp: float  # seconds from session start (clock-sync adjusted)


class ActiveSpeakerBatch(BaseModel):
    events: list[ActiveSpeakerEvent]


class SpeakerRelabelUpdate(BaseModel):
    segment_id: str
    new_speaker: str


class SpeakerRelabelBatch(BaseModel):
    updates: list[SpeakerRelabelUpdate]


class ClockSyncRequest(BaseModel):
    client_send_time: float  # epoch seconds from Date.now()/1000


class ClockSyncResponse(BaseModel):
    client_send_time: float  # echoed back
    server_time: float  # time.time() at receipt
    session_start_wall: float  # wall-clock time when session started


class ParticipantInfo(BaseModel):
    name: str
    isSelf: bool = False


class ParticipantsList(BaseModel):
    participants: list[ParticipantInfo]


class HeartbeatRequest(BaseModel):
    can_detect_speaker: bool


class WSMessage(BaseModel):
    """WebSocket message envelope."""
    type: WSMessageType
    session_id: str
    sequence_number: int = 0
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def transcript_delta(cls, session_id: str, seq: int, segment: TranscriptSegment) -> WSMessage:
        return cls(
            type=WSMessageType.TRANSCRIPT_DELTA,
            session_id=session_id,
            sequence_number=seq,
            payload=TranscriptDelta(segment=segment).model_dump(),
        )

    @classmethod
    def summary_update(cls, session_id: str, seq: int, update: SummaryUpdate) -> WSMessage:
        return cls(
            type=WSMessageType.SUMMARY_UPDATE,
            session_id=session_id,
            sequence_number=seq,
            payload=update.model_dump(),
        )

    @classmethod
    def suggestion_msg(cls, session_id: str, seq: int, suggestion: Suggestion) -> WSMessage:
        return cls(
            type=WSMessageType.SUGGESTION,
            session_id=session_id,
            sequence_number=seq,
            payload=suggestion.model_dump(),
        )

    @classmethod
    def session_state_msg(cls, session_id: str, seq: int, state: SessionState) -> WSMessage:
        return cls(
            type=WSMessageType.SESSION_STATE,
            session_id=session_id,
            sequence_number=seq,
            payload=state.model_dump(),
        )

    @classmethod
    def connection_status_msg(cls, session_id: str, seq: int, status: ConnectionStatusPayload) -> WSMessage:
        return cls(
            type=WSMessageType.CONNECTION_STATUS,
            session_id=session_id,
            sequence_number=seq,
            payload=status.model_dump(),
        )

    @classmethod
    def companion_health_msg(cls, session_id: str, seq: int, payload: CompanionHealthPayload) -> WSMessage:
        return cls(
            type=WSMessageType.COMPANION_HEALTH,
            session_id=session_id,
            sequence_number=seq,
            payload=payload.model_dump(),
        )

    @classmethod
    def coverage_gap_msg(cls, session_id: str, seq: int, payload: CoverageGapPayload) -> WSMessage:
        return cls(
            type=WSMessageType.COVERAGE_GAP,
            session_id=session_id,
            sequence_number=seq,
            payload=payload.model_dump(),
        )

    @classmethod
    def error_msg(cls, session_id: str, seq: int, error: ErrorPayload) -> WSMessage:
        return cls(
            type=WSMessageType.ERROR,
            session_id=session_id,
            sequence_number=seq,
            payload=error.model_dump(),
        )

    @classmethod
    def speaker_relabel_batch_msg(
        cls, session_id: str, seq: int, batch: "SpeakerRelabelBatch"
    ) -> "WSMessage":
        return cls(
            type=WSMessageType.SPEAKER_RELABEL_BATCH,
            session_id=session_id,
            sequence_number=seq,
            payload=batch.model_dump(),
        )
