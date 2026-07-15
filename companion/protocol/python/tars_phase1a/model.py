"""Canonical Phase 1A protocol bindings and terminal-coverage invariants."""

import hashlib
import json
import re
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union


MAX_CONTROL_BYTES = 65_536
MAX_AUDIO_PAYLOAD_BYTES = 64_000
MIN_AUDIO_DURATION_MS = 20
MAX_AUDIO_DURATION_MS = 1_000
MIN_SAMPLE_RATE_HERTZ = 8_000
MAX_SAMPLE_RATE_HERTZ = 48_000
MAX_CHANNEL_COUNT = 2

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class ProtocolViolation(ValueError):
    """Raised when protocol data violates the canonical Phase 1A contract."""


class Source(str, Enum):
    MICROPHONE = "microphone"
    SYSTEM_AUDIO = "system_audio"


class TerminalKind(str, Enum):
    TRANSCRIPT = "transcript"
    GAP = "gap"


def _validate_identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ProtocolViolation(name + " is not a valid protocol identifier")


def _validate_sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ProtocolViolation(name + " must be a lowercase SHA-256 digest")


def _validate_non_negative_integer(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolViolation(name + " must be a non-negative integer")


def _validate_positive_integer(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ProtocolViolation(name + " must be a positive integer")


def _validate_event_type(value: str) -> None:
    _validate_identifier("eventType", value)
    if len(value) > 64:
        raise ProtocolViolation("eventType exceeds 64 characters")


def _validate_reason_code(value: str) -> None:
    _validate_identifier("reasonCode", value)
    if len(value) > 64:
        raise ProtocolViolation("reasonCode exceeds 64 characters")


def _digest_fields(prefix: str, *fields: str) -> str:
    encoded_fields = (prefix,) + fields
    if any("\0" in field for field in encoded_fields):
        raise ProtocolViolation("canonical identity fields may not contain NUL")
    payload = "\0".join(encoded_fields).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class StreamKey:
    session_id: str
    stream_id: str
    capture_generation: int
    source: Source

    def __post_init__(self) -> None:
        _validate_identifier("sessionId", self.session_id)
        _validate_identifier("streamId", self.stream_id)
        _validate_non_negative_integer("captureGeneration", self.capture_generation)
        if not isinstance(self.source, Source):
            raise ProtocolViolation("source must be a Source value")


@dataclass(frozen=True)
class KnownCoverage:
    key: StreamKey
    first_sequence: int
    last_sequence_inclusive: int
    first_sample: int
    last_sample_exclusive: int
    first_captured_at_monotonic_ns: int = 0
    last_captured_at_monotonic_ns: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.key, StreamKey):
            raise ProtocolViolation("coverage key must be a StreamKey")
        _validate_non_negative_integer("firstSequence", self.first_sequence)
        _validate_non_negative_integer(
            "lastSequenceInclusive", self.last_sequence_inclusive
        )
        if self.last_sequence_inclusive < self.first_sequence:
            raise ProtocolViolation("sequence coverage must not be reversed")
        _validate_non_negative_integer("firstSample", self.first_sample)
        _validate_positive_integer("lastSampleExclusive", self.last_sample_exclusive)
        if self.last_sample_exclusive <= self.first_sample:
            raise ProtocolViolation("sample coverage must be non-empty and half-open")
        _validate_non_negative_integer(
            "firstCapturedAtMonotonicNs", self.first_captured_at_monotonic_ns
        )
        _validate_non_negative_integer(
            "lastCapturedAtMonotonicNs", self.last_captured_at_monotonic_ns
        )
        if self.last_captured_at_monotonic_ns < self.first_captured_at_monotonic_ns:
            raise ProtocolViolation("capture-time coverage must not be reversed")

    @property
    def coverage_id(self) -> str:
        digest = _digest_fields(
            "tars-coverage-v1",
            self.key.session_id,
            self.key.stream_id,
            str(self.key.capture_generation),
            self.key.source.value,
            str(self.first_sequence),
            str(self.last_sequence_inclusive),
            str(self.first_sample),
            str(self.last_sample_exclusive),
        )
        return "cov_" + digest

    def to_dict(self) -> Dict[str, object]:
        return {
            "boundaryStatus": "known",
            "coverageId": self.coverage_id,
            "source": self.key.source.value,
            "firstSequence": self.first_sequence,
            "lastSequenceInclusive": self.last_sequence_inclusive,
            "firstSample": self.first_sample,
            "lastSampleExclusive": self.last_sample_exclusive,
            "firstCapturedAtMonotonicNs": self.first_captured_at_monotonic_ns,
            "lastCapturedAtMonotonicNs": self.last_captured_at_monotonic_ns,
        }


@dataclass(frozen=True)
class UnknownEndCoverage:
    key: StreamKey
    first_sequence: int
    first_sample: int
    first_captured_at_monotonic_ns: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.key, StreamKey):
            raise ProtocolViolation("coverage key must be a StreamKey")
        _validate_non_negative_integer("firstSequence", self.first_sequence)
        _validate_non_negative_integer("firstSample", self.first_sample)
        _validate_non_negative_integer(
            "firstCapturedAtMonotonicNs", self.first_captured_at_monotonic_ns
        )

    @property
    def identity_token(self) -> str:
        return "unknown:{}:{}:{}:{}:{}:{}".format(
            self.key.session_id,
            self.key.stream_id,
            self.key.capture_generation,
            self.key.source.value,
            self.first_sequence,
            self.first_sample,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "boundaryStatus": "unknown_end",
            "source": self.key.source.value,
            "firstSequence": self.first_sequence,
            "firstSample": self.first_sample,
            "firstCapturedAtMonotonicNs": self.first_captured_at_monotonic_ns,
        }


Coverage = Union[KnownCoverage, UnknownEndCoverage]


def deterministic_event_id(event_type: str, key: StreamKey, identity: str) -> str:
    _validate_event_type(event_type)
    if not isinstance(key, StreamKey):
        raise ProtocolViolation("event key must be a StreamKey")
    _validate_identifier("eventIdentity", identity)
    digest = _digest_fields(
        "tars-event-v1",
        key.session_id,
        key.stream_id,
        str(key.capture_generation),
        key.source.value,
        event_type,
        identity,
    )
    return "evt_" + digest


def _validate_wall_clock(value: str) -> None:
    if not isinstance(value, str) or len(value) > 40:
        raise ProtocolViolation("capturedAtWallClock must be an ISO-8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ProtocolViolation("capturedAtWallClock must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ProtocolViolation("capturedAtWallClock must include a timezone")


@dataclass(frozen=True)
class AudioChunk:
    key: StreamKey
    sequence: int
    first_sample: int
    last_sample_exclusive: int
    captured_at_monotonic_ns: int
    captured_at_wall_clock: str
    sample_rate_hertz: int
    channel_count: int
    duration_ms: int
    payload: bytes
    device_id: str = "device_phase1a"
    encoding: str = "pcm_s16le"

    def __post_init__(self) -> None:
        if not isinstance(self.key, StreamKey):
            raise ProtocolViolation("audio key must be a StreamKey")
        _validate_identifier("deviceId", self.device_id)
        _validate_non_negative_integer("sequence", self.sequence)
        _validate_non_negative_integer("firstSample", self.first_sample)
        _validate_positive_integer("lastSampleExclusive", self.last_sample_exclusive)
        if self.last_sample_exclusive <= self.first_sample:
            raise ProtocolViolation("audio sample range must be non-empty and half-open")
        _validate_non_negative_integer(
            "capturedAtMonotonicNs", self.captured_at_monotonic_ns
        )
        _validate_wall_clock(self.captured_at_wall_clock)
        _validate_positive_integer("sampleRateHertz", self.sample_rate_hertz)
        if not MIN_SAMPLE_RATE_HERTZ <= self.sample_rate_hertz <= MAX_SAMPLE_RATE_HERTZ:
            raise ProtocolViolation("sampleRateHertz is outside the approved bound")
        _validate_positive_integer("channelCount", self.channel_count)
        if not 1 <= self.channel_count <= MAX_CHANNEL_COUNT:
            raise ProtocolViolation("channelCount is outside the approved bound")
        if self.encoding != "pcm_s16le":
            raise ProtocolViolation("only pcm_s16le is permitted in Phase 1A")
        _validate_positive_integer("durationMs", self.duration_ms)
        if not MIN_AUDIO_DURATION_MS <= self.duration_ms <= MAX_AUDIO_DURATION_MS:
            raise ProtocolViolation("durationMs is outside the approved bound")
        if not isinstance(self.payload, bytes):
            raise ProtocolViolation("payload must be immutable bytes")
        if not 1 <= len(self.payload) <= MAX_AUDIO_PAYLOAD_BYTES:
            raise ProtocolViolation("payloadBytes is outside the approved bound")

        bytes_per_frame = 2 * self.channel_count
        if len(self.payload) % bytes_per_frame:
            raise ProtocolViolation("payload is not aligned to pcm_s16le frames")
        frame_count = len(self.payload) // bytes_per_frame
        if frame_count != self.last_sample_exclusive - self.first_sample:
            raise ProtocolViolation("payload frames do not match the sample range")
        if frame_count * 1000 != self.duration_ms * self.sample_rate_hertz:
            raise ProtocolViolation("durationMs does not exactly match the sample range")

    @property
    def event_id(self) -> str:
        return deterministic_event_id("audio.chunk", self.key, str(self.sequence))

    @property
    def payload_digest_sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    @property
    def coverage(self) -> KnownCoverage:
        return KnownCoverage(
            key=self.key,
            first_sequence=self.sequence,
            last_sequence_inclusive=self.sequence,
            first_sample=self.first_sample,
            last_sample_exclusive=self.last_sample_exclusive,
            first_captured_at_monotonic_ns=self.captured_at_monotonic_ns,
            last_captured_at_monotonic_ns=(
                self.captured_at_monotonic_ns + self.duration_ms * 1_000_000
            ),
        )

    def metadata_dict(self) -> Dict[str, object]:
        return {
            "protocolVersion": 1,
            "eventType": "audio.chunk",
            "sessionId": self.key.session_id,
            "streamId": self.key.stream_id,
            "deviceId": self.device_id,
            "captureGeneration": self.key.capture_generation,
            "eventId": self.event_id,
            "capturedAtMonotonicNs": self.captured_at_monotonic_ns,
            "capturedAtWallClock": self.captured_at_wall_clock,
            "source": self.key.source.value,
            "sequence": self.sequence,
            "firstSample": self.first_sample,
            "lastSampleExclusive": self.last_sample_exclusive,
            "sampleRateHertz": self.sample_rate_hertz,
            "channelCount": self.channel_count,
            "encoding": self.encoding,
            "durationMs": self.duration_ms,
            "payloadBytes": len(self.payload),
            "payloadDigestSha256": self.payload_digest_sha256,
        }

    def encoded_metadata(self) -> bytes:
        encoded = json.dumps(
            self.metadata_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > MAX_CONTROL_BYTES:
            raise ProtocolViolation("encoded metadata exceeds the control-message bound")
        return encoded


class EventLedger:
    """Idempotent event-ID registry used to detect retry payload changes."""

    def __init__(self) -> None:
        self._payload_digests: Dict[str, str] = {}

    def observe(self, event_id: str, payload_digest_sha256: str) -> bool:
        _validate_identifier("eventId", event_id)
        _validate_sha256("payloadDigestSha256", payload_digest_sha256)
        existing = self._payload_digests.get(event_id)
        if existing is None:
            self._payload_digests[event_id] = payload_digest_sha256
            return True
        if existing != payload_digest_sha256:
            raise ProtocolViolation("eventId was reused with a different payload")
        return False


@dataclass(frozen=True)
class TerminalOutcome:
    kind: TerminalKind
    coverage: Coverage
    stt_attempt_generation: int
    result_ordinal: int = 0
    reason_code: Optional[str] = None
    device_id: str = "device_phase1a"
    captured_at_monotonic_ns: int = 0
    captured_at_wall_clock: str = "1970-01-01T00:00:00Z"

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TerminalKind):
            raise ProtocolViolation("terminal kind is invalid")
        if not isinstance(self.coverage, (KnownCoverage, UnknownEndCoverage)):
            raise ProtocolViolation("terminal coverage is invalid")
        _validate_non_negative_integer(
            "sttAttemptGeneration", self.stt_attempt_generation
        )
        _validate_non_negative_integer("resultOrdinal", self.result_ordinal)
        _validate_identifier("deviceId", self.device_id)
        _validate_non_negative_integer(
            "capturedAtMonotonicNs", self.captured_at_monotonic_ns
        )
        _validate_wall_clock(self.captured_at_wall_clock)
        if self.kind is TerminalKind.TRANSCRIPT:
            if not isinstance(self.coverage, KnownCoverage):
                raise ProtocolViolation("a transcript requires known coverage")
            if self.reason_code is not None:
                raise ProtocolViolation("a transcript may not carry a gap reason")
        else:
            if not self.reason_code:
                raise ProtocolViolation("a gap requires a reason code")
            _validate_reason_code(self.reason_code)

    @property
    def key(self) -> StreamKey:
        return self.coverage.key

    @property
    def event_id(self) -> str:
        token = (
            self.coverage.coverage_id
            if isinstance(self.coverage, KnownCoverage)
            else self.coverage.identity_token
        )
        digest = _digest_fields(
            "tars-terminal-v1",
            self.kind.value,
            token,
            str(self.result_ordinal),
        )
        return "term_" + digest

    def semantic_identity(self) -> Tuple[object, ...]:
        return (self.kind, self.coverage, self.result_ordinal, self.reason_code)

    def to_dict(self) -> Dict[str, object]:
        result: Dict[str, object] = {
            "protocolVersion": 1,
            "eventType": (
                "transcript.final"
                if self.kind is TerminalKind.TRANSCRIPT
                else "capture.gap"
            ),
            "sessionId": self.key.session_id,
            "streamId": self.key.stream_id,
            "deviceId": self.device_id,
            "captureGeneration": self.key.capture_generation,
            "capturedAtMonotonicNs": self.captured_at_monotonic_ns,
            "capturedAtWallClock": self.captured_at_wall_clock,
            "source": self.key.source.value,
            "eventId": self.event_id,
            "outcome": self.kind.value,
            "coverage": self.coverage.to_dict(),
            "sttAttemptGeneration": self.stt_attempt_generation,
            "resultOrdinal": self.result_ordinal,
        }
        if self.reason_code is not None:
            result["reasonCode"] = self.reason_code
        return result

    def encoded_metadata(self) -> bytes:
        encoded = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) > MAX_CONTROL_BYTES:
            raise ProtocolViolation("encoded metadata exceeds the control-message bound")
        return encoded


class TerminalLedger:
    """One observable terminal outcome per non-overlapping coverage range."""

    def __init__(self) -> None:
        self._by_event_id: Dict[str, TerminalOutcome] = {}
        self._by_key: Dict[StreamKey, List[TerminalOutcome]] = {}
        self._starts_by_key: Dict[StreamKey, List[int]] = {}

    def commit(self, outcome: TerminalOutcome) -> bool:
        if not isinstance(outcome, TerminalOutcome):
            raise ProtocolViolation("terminal ledger accepts TerminalOutcome values only")
        existing = self._by_event_id.get(outcome.event_id)
        if existing is not None:
            if existing.semantic_identity() == outcome.semantic_identity():
                return False
            raise ProtocolViolation("terminal eventId was reused with different semantics")

        outcomes = self._by_key.setdefault(outcome.key, [])
        starts = self._starts_by_key.setdefault(outcome.key, [])
        start = outcome.coverage.first_sequence
        index = bisect_left(starts, start)
        neighbors = outcomes[max(0, index - 1) : min(len(outcomes), index + 1)]
        for committed in neighbors:
            if self._coverage_conflicts(committed.coverage, outcome.coverage):
                raise ProtocolViolation("terminal coverage overlaps an existing outcome")

        self._by_event_id[outcome.event_id] = outcome
        starts.insert(index, start)
        outcomes.insert(index, outcome)
        return True

    def outcomes(self, key: StreamKey) -> Tuple[TerminalOutcome, ...]:
        return tuple(self._by_key.get(key, ()))

    @staticmethod
    def _coverage_conflicts(left: Coverage, right: Coverage) -> bool:
        if isinstance(left, UnknownEndCoverage) and isinstance(
            right, UnknownEndCoverage
        ):
            return True

        if isinstance(left, KnownCoverage) and isinstance(right, KnownCoverage):
            sequence_overlap = not (
                left.last_sequence_inclusive < right.first_sequence
                or right.last_sequence_inclusive < left.first_sequence
            )
            sample_overlap = not (
                left.last_sample_exclusive <= right.first_sample
                or right.last_sample_exclusive <= left.first_sample
            )
            if sequence_overlap or sample_overlap:
                return True

            left_before_by_sequence = (
                left.last_sequence_inclusive < right.first_sequence
            )
            left_before_by_sample = left.last_sample_exclusive <= right.first_sample
            return left_before_by_sequence != left_before_by_sample

        unknown = left if isinstance(left, UnknownEndCoverage) else right
        known = right if isinstance(left, UnknownEndCoverage) else left
        assert isinstance(unknown, UnknownEndCoverage)
        assert isinstance(known, KnownCoverage)
        known_is_strictly_before = (
            known.last_sequence_inclusive < unknown.first_sequence
            and known.last_sample_exclusive <= unknown.first_sample
        )
        return not known_is_strictly_before
