"""Typed, content-free contracts for the G3A source/offline gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Iterable, Mapping


class Source(str, Enum):
    MICROPHONE = "microphone"
    SYSTEM_AUDIO = "system_audio"


class TransportState(str, Enum):
    DISCONNECTED = "disconnected"
    ADMITTING = "admitting"
    FORWARDING = "forwarding"
    DRAINING = "draining"
    FENCED = "fenced"
    EFFECT_QUIESCENCE_REQUIRED = "effect_quiescence_required"
    CLOSED = "closed"


class CoverageState(str, Enum):
    NOT_STARTED = "not_started"
    OPEN = "open"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    COMPLETED_WITH_GAPS = "completed_with_gaps"
    DELETE_QUIESCING = "delete_quiescing"
    DELETING = "deleting"
    DELETED = "deleted"
    DELETION_FAILED = "deletion_failed"


class EffectState(str, Enum):
    PREPARED = "prepared"
    INVOKING = "invoking"
    FORWARDED = "forwarded"
    AMBIGUOUS = "ambiguous"
    DISCARDED = "discarded"
    QUIESCED = "quiesced"
    EFFECT_QUIESCENCE_REQUIRED = "effect_quiescence_required"


class TerminalOutcome(str, Enum):
    TRANSCRIPT = "transcript"
    GAP = "gap"


class FailureCode(str, Enum):
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    REVOKED = "revoked"
    STALE_FENCE = "stale_fence"
    DISCLOSURE_REQUIRED = "disclosure_required"
    QUOTA_EXCEEDED = "quota_exceeded"
    OUT_OF_ORDER = "out_of_order"
    OVERLAP = "overlap"
    CONFLICT = "conflict"
    QUIESCENCE_REQUIRED = "quiescence_required"
    DELETION_FAILED = "deletion_failed"


class GatewayError(Exception):
    """Base exception with a bounded, non-enumerating reason code."""

    def __init__(self, code: FailureCode, message: str = "request rejected") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Disclosure:
    notice_version: str
    actor_id: str
    organization_id: str
    session_id: str
    acknowledged_at_ms: int
    legal_basis: str


@dataclass(frozen=True)
class ActorContext:
    actor_id: str
    organization_id: str
    session_id: str
    stream_id: str
    capture_generation: int
    enrollment_digest: str
    disclosure: Disclosure


@dataclass(frozen=True)
class Lease:
    owner_id: str
    runtime_epoch: int
    capture_generation: int
    revoked: bool = False


@dataclass(frozen=True)
class QuotaLimits:
    audio_events_per_second: int = 50
    audio_event_burst: int = 100
    audio_bytes_per_second: int = 192_000
    audio_bytes_burst: int = 384_000
    metadata_bytes_per_second: int = 205_000
    metadata_bytes_burst: int = 410_000
    max_pending_handshakes: int = 8
    max_receive_bytes: int = 768_000
    max_provider_attempts: int = 4
    max_resident_bytes: int = 384_000


@dataclass(frozen=True)
class AudioFrame:
    context: ActorContext
    event_id: str
    sequence: int
    first_sample: int
    last_sample_exclusive: int
    sample_rate: int
    channels: int
    captured_at_ms: int
    payload: bytes
    metadata_bytes: int = 0
    source: Source = Source.MICROPHONE

    @property
    def payload_bytes(self) -> int:
        return len(self.payload)

    @property
    def duration_ms(self) -> int:
        return ((self.last_sample_exclusive - self.first_sample) * 1000) // self.sample_rate

    @property
    def payload_digest(self) -> str:
        return sha256(self.payload).hexdigest()

    @property
    def identity(self) -> tuple[str, str, Source, int, int, int]:
        return (
            self.context.session_id,
            self.context.stream_id,
            self.source,
            self.context.capture_generation,
            self.sequence,
            self.first_sample,
        )

    @property
    def retry_identity(self) -> tuple[object, ...]:
        """Typed fields that must match before an ingress retry is accepted."""
        return (
            self.context.session_id,
            self.context.stream_id,
            self.context.capture_generation,
            self.source,
            self.event_id,
            self.sequence,
            self.first_sample,
            self.last_sample_exclusive,
            self.sample_rate,
            self.channels,
            self.payload_bytes,
            self.metadata_bytes,
        )


@dataclass(frozen=True, order=True)
class AtomicRange:
    first_sequence: int
    last_sequence_inclusive: int

    def __post_init__(self) -> None:
        if self.first_sequence < 0 or self.last_sequence_inclusive < self.first_sequence:
            raise ValueError("invalid atomic range")


@dataclass(frozen=True)
class Segment:
    segment_id: str
    start_sample: int
    end_sample_exclusive: int
    text_digest: str

    def __post_init__(self) -> None:
        if self.start_sample < 0 or self.end_sample_exclusive <= self.start_sample:
            raise ValueError("invalid segment bounds")
        if not self.segment_id or not self.text_digest:
            raise ValueError("segment identity is required")


@dataclass(frozen=True)
class TerminalClaim:
    atomic_range: AtomicRange
    outcome: TerminalOutcome
    segments: tuple[Segment, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.outcome is TerminalOutcome.TRANSCRIPT and not self.segments:
            raise ValueError("transcript claims require at least one segment")
        if self.outcome is TerminalOutcome.GAP and self.segments:
            raise ValueError("gap claims cannot contain transcript segments")

    @property
    def claim_id(self) -> str:
        parts = [
            str(self.atomic_range.first_sequence),
            str(self.atomic_range.last_sequence_inclusive),
            self.outcome.value,
            self.reason or "",
        ]
        parts.extend(
            f"{s.segment_id}:{s.start_sample}:{s.end_sample_exclusive}:{s.text_digest}"
            for s in self.segments
        )
        return sha256("\0".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EffectOwner:
    owner_id: str
    runtime_epoch: int
    effect_generation: int

    @property
    def opaque_id(self) -> str:
        return sha256(
            f"{self.owner_id}\0{self.runtime_epoch}\0{self.effect_generation}".encode()
        ).hexdigest()


@dataclass(frozen=True)
class EffectIntent:
    intent_id: str
    atomic_range: AtomicRange
    owner: EffectOwner
    state: EffectState


@dataclass(frozen=True)
class Diagnostic:
    name: str
    counters: Mapping[str, int] = field(default_factory=dict)


def intervals_are_disjoint(intervals: Iterable[tuple[int, int]]) -> bool:
    ordered = sorted(intervals)
    return all(left[1] <= right[0] for left, right in zip(ordered, ordered[1:]))
