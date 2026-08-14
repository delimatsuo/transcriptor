"""Protocol-v2 pure model.

This module deliberately has no network, filesystem, provider, device, or
credential dependency.  It is the reference for canonical bytes and the
bounded in-memory custody projection used by the G2-A offline vectors.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple


MAX_CONTROL_BYTES = 68_100
MAX_AUDIO_PAYLOAD_BYTES = 64_000
MIN_AUDIO_DURATION_MS = 20
MAX_AUDIO_DURATION_MS = 250
MIN_SAMPLE_RATE_HERTZ = 8_000
MAX_SAMPLE_RATE_HERTZ = 48_000
MAX_CHANNEL_COUNT = 2
MAX_CUSTODY_FRAMES = 96_000
MAX_CUSTODY_SECONDS = 2
MAX_METADATA_BYTES_PER_SOURCE = 409_600
MAX_QUEUED_AUDIO_EVENTS = 100
MAX_RESERVATIONS = 100
MAX_QUEUE_OBJECTS = 100
MAX_SAFE_JSON_INTEGER = 2**53 - 1
MAX_U32 = 2**32 - 1
MAX_U64 = 2**64 - 1
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class ProtocolV2Violation(ValueError):
    """Fail-closed validation error for protocol-v2 inputs."""


class Source(str, Enum):
    MICROPHONE = "microphone"
    SYSTEM_AUDIO = "system_audio"


class TerminalKind(str, Enum):
    TRANSCRIPT = "transcript"
    GAP = "gap"


def _identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ProtocolV2Violation(f"{name} is not a valid protocol identifier")


def _non_negative(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolV2Violation(f"{name} must be a non-negative integer")


def _bounded_u32(name: str, value: int) -> None:
    _non_negative(name, value)
    if value > MAX_U32:
        raise ProtocolV2Violation(f"{name} exceeds uint32")


def _bounded_u64(name: str, value: int) -> None:
    _non_negative(name, value)
    if value > MAX_U64:
        raise ProtocolV2Violation(f"{name} exceeds uint64")


def _nfc_string(name: str, value: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or "\0" in value:
        raise ProtocolV2Violation(f"{name} must be a NUL-free string")
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value:
        raise ProtocolV2Violation(f"{name} must already be NFC")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise ProtocolV2Violation(f"{name} is not valid Unicode") from exc
    if len(encoded) > MAX_U32:
        raise ProtocolV2Violation(f"{name} exceeds uint32 byte length")
    if identifier:
        _identifier(name, value)
    return value


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _length_prefixed(value: str, name: str) -> bytes:
    encoded = _nfc_string(name, value).encode("utf-8")
    return struct.pack(">I", len(encoded)) + encoded


def _identity_prefix(prefix: str, key: "StreamKey", extra: Sequence[str]) -> bytes:
    fields = [
        _nfc_string("identity prefix", prefix),
        _nfc_string("sessionId", key.session_id, identifier=True),
        _nfc_string("streamId", key.stream_id, identifier=True),
        str(key.capture_generation),
        key.source.value,
        *extra,
    ]
    for field_value in fields:
        _nfc_string("identity field", field_value)
    return b"\0".join(field.encode("utf-8") for field in fields)


def canonical_json_bytes(value: Any) -> bytes:
    """Encode the restricted RFC-8785-compatible fixture subset.

    Protocol vectors use NFC strings, booleans, null, unsigned JSON-safe
    integers, arrays, and maps. Floats are rejected rather than allowing
    platform-dependent spellings. Object keys use RFC 8785 UTF-16 ordering.
    """

    def validate_and_order(item: Any) -> Any:
        if item is None or isinstance(item, bool):
            return item
        if isinstance(item, int):
            if item < 0 or item > MAX_SAFE_JSON_INTEGER:
                raise ProtocolV2Violation("JSON integer is outside the unsigned safe domain")
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ProtocolV2Violation("non-finite JSON number")
            raise ProtocolV2Violation("floating-point JSON is not in the fixture subset")
        if isinstance(item, str):
            return _nfc_string("JSON string", item)
        if isinstance(item, Mapping):
            ordered: dict[str, Any] = {}
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise ProtocolV2Violation("JSON object keys must be strings")
                _nfc_string("JSON object key", key)
            for key in sorted(item, key=lambda value: value.encode("utf-16-be")):
                ordered[key] = validate_and_order(item[key])
            return ordered
        if isinstance(item, (list, tuple)):
            return [validate_and_order(nested) for nested in item]
        raise ProtocolV2Violation("value is not in the canonical JSON fixture subset")

    try:
        canonical = validate_and_order(value)
        encoded = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        if isinstance(exc, ProtocolV2Violation):
            raise
        raise ProtocolV2Violation("value is not canonical JSON") from exc
    if len(encoded) > MAX_CONTROL_BYTES:
        raise ProtocolV2Violation("canonical JSON exceeds the control envelope")
    return encoded


def parse_canonical_json_bytes(payload: bytes) -> Any:
    """Parse only exact canonical bytes from the bounded unsigned profile."""

    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_CONTROL_BYTES:
        raise ProtocolV2Violation("canonical JSON payload size is invalid")

    def reject_constant(_: str) -> Any:
        raise ProtocolV2Violation("non-finite JSON number")

    def reject_float(_: str) -> Any:
        raise ProtocolV2Violation("floating-point JSON is not in the fixture subset")

    def parse_integer(value: str) -> int:
        parsed = int(value)
        if parsed < 0 or parsed > MAX_SAFE_JSON_INTEGER:
            raise ProtocolV2Violation("JSON integer is outside the unsigned safe domain")
        return parsed

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, nested in pairs:
            if key in result:
                raise ProtocolV2Violation("duplicate JSON object key")
            result[key] = nested
        return result

    try:
        decoded = payload.decode("utf-8", "strict")
        value = json.loads(
            decoded,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=reject_float,
            parse_int=parse_integer,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, ProtocolV2Violation):
            raise
        raise ProtocolV2Violation("payload is not valid canonical JSON") from exc
    if canonical_json_bytes(value) != payload:
        raise ProtocolV2Violation("payload is not encoded in canonical JSON form")
    return value


@dataclass(frozen=True)
class StreamKey:
    session_id: str
    stream_id: str
    capture_generation: int
    source: Source

    def __post_init__(self) -> None:
        _identifier("sessionId", self.session_id)
        _identifier("streamId", self.stream_id)
        _bounded_u64("captureGeneration", self.capture_generation)
        if not isinstance(self.source, Source):
            raise ProtocolV2Violation("source is invalid")


@dataclass(frozen=True, order=True)
class AtomicCoverage:
    key: StreamKey = field(compare=False)
    sequence: int
    first_sample: int
    last_sample_exclusive: int

    def __post_init__(self) -> None:
        if not isinstance(self.key, StreamKey):
            raise ProtocolV2Violation("atomic coverage key is invalid")
        _bounded_u64("sequence", self.sequence)
        _bounded_u64("firstSample", self.first_sample)
        _bounded_u64("lastSampleExclusive", self.last_sample_exclusive)
        if self.last_sample_exclusive <= self.first_sample:
            raise ProtocolV2Violation("atomic sample range must be non-empty")

    @property
    def coverage_id(self) -> str:
        payload = _identity_prefix(
            "tars-atomic-coverage-v2",
            self.key,
            [
                str(self.sequence),
                str(self.first_sample),
                str(self.last_sample_exclusive),
            ],
        )
        return "acov_" + _sha256(payload)

    @property
    def sort_key(self) -> tuple[int, int, int, str]:
        return (
            self.sequence,
            self.first_sample,
            self.last_sample_exclusive,
            self.coverage_id,
        )


@dataclass(frozen=True, order=True)
class Interval:
    first_sequence: int
    last_sequence_inclusive: int
    first_sample: int
    last_sample_exclusive: int

    def __post_init__(self) -> None:
        _bounded_u64("firstSequence", self.first_sequence)
        _bounded_u64("lastSequenceInclusive", self.last_sequence_inclusive)
        _bounded_u64("firstSample", self.first_sample)
        _bounded_u64("lastSampleExclusive", self.last_sample_exclusive)
        if self.last_sequence_inclusive < self.first_sequence:
            raise ProtocolV2Violation("interval sequence range is reversed")
        if self.last_sample_exclusive <= self.first_sample:
            raise ProtocolV2Violation("interval sample range is empty")

    def overlaps(self, other: "Interval") -> bool:
        sequence_overlap = not (
            self.last_sequence_inclusive < other.first_sequence
            or other.last_sequence_inclusive < self.first_sequence
        )
        sample_overlap = not (
            self.last_sample_exclusive <= other.first_sample
            or other.last_sample_exclusive <= self.first_sample
        )
        return sequence_overlap or sample_overlap

    def contains(self, coverage: AtomicCoverage) -> bool:
        return (
            self.first_sequence <= coverage.sequence <= self.last_sequence_inclusive
            and self.first_sample <= coverage.first_sample
            and coverage.last_sample_exclusive <= self.last_sample_exclusive
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "firstSequence": str(self.first_sequence),
            "lastSequenceInclusive": str(self.last_sequence_inclusive),
            "firstSample": str(self.first_sample),
            "lastSampleExclusive": str(self.last_sample_exclusive),
        }


class IntervalSet:
    """Authoritative ordered disjoint interval set; no scalar watermark."""

    def __init__(self, intervals: Iterable[Interval] = ()) -> None:
        ordered = sorted(intervals, key=lambda item: (item.first_sequence, item.first_sample, item.last_sample_exclusive))
        for previous, current in zip(ordered, ordered[1:]):
            if previous.overlaps(current):
                raise ProtocolV2Violation("interval set contains overlap")
        self._intervals: tuple[Interval, ...] = tuple(ordered)

    @property
    def intervals(self) -> tuple[Interval, ...]:
        return self._intervals

    def add(self, interval: Interval) -> "IntervalSet":
        return IntervalSet((*self._intervals, interval))

    def contains(self, coverage: AtomicCoverage) -> bool:
        return any(interval.contains(coverage) for interval in self._intervals)

    def has_preceding_gap(self, coverage: AtomicCoverage) -> bool:
        for interval in self._intervals:
            if interval.first_sequence <= coverage.sequence:
                if interval.contains(coverage):
                    return False
                continue
            return True
        return coverage.sequence != 0

    def derived_contiguous_prefix(self, expected_sequence: int, expected_sample: int) -> Optional[Interval]:
        for interval in self._intervals:
            if interval.first_sequence == expected_sequence and interval.first_sample == expected_sample:
                return interval
            if interval.first_sequence > expected_sequence:
                break
        return None

    def to_list(self) -> list[dict[str, str]]:
        return [interval.to_dict() for interval in self._intervals]


def _validate_atomic_list(key: StreamKey, atomic: Sequence[AtomicCoverage]) -> tuple[AtomicCoverage, ...]:
    if not atomic:
        raise ProtocolV2Violation("terminal coverage must contain atomic coverage")
    ordered = tuple(sorted(atomic, key=lambda item: item.sort_key))
    if any(item.key != key for item in ordered):
        raise ProtocolV2Violation("atomic coverage key mismatch")
    ids = [item.coverage_id for item in ordered]
    if len(set(ids)) != len(ids):
        raise ProtocolV2Violation("duplicate atomic coverage identity")
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if left.sequence == right.sequence or not (
                left.last_sample_exclusive <= right.first_sample
                or right.last_sample_exclusive <= left.first_sample
            ):
                raise ProtocolV2Violation("overlapping atomic coverage")
    return ordered


def terminal_coverage_bytes(key: StreamKey, atomic: Sequence[AtomicCoverage]) -> bytes:
    ordered = _validate_atomic_list(key, atomic)
    payload = _identity_prefix("tars-terminal-coverage-v2", key, [])
    payload += struct.pack(">I", len(ordered))
    for item in ordered:
        payload += _length_prefixed(item.coverage_id, "coverageId")
    return payload


def terminal_coverage_id(key: StreamKey, atomic: Sequence[AtomicCoverage]) -> str:
    return "covr_" + _sha256(terminal_coverage_bytes(key, atomic))


def transcript_segment_bytes(
    key: StreamKey,
    atomic: Sequence[AtomicCoverage],
    text_first_sample: int,
    text_last_sample_exclusive: int,
    provider_result_ordinal: int,
    provider_name: str,
    provider_result_id: str,
    stt_attempt_generation: Optional[int] = None,
) -> bytes:
    ordered = _validate_atomic_list(key, atomic)
    _bounded_u64("textFirstSample", text_first_sample)
    _bounded_u64("textLastSampleExclusive", text_last_sample_exclusive)
    _bounded_u64("providerResultOrdinal", provider_result_ordinal)
    if text_last_sample_exclusive <= text_first_sample:
        raise ProtocolV2Violation("segment text sample range is empty")
    _nfc_string("providerName", provider_name, identifier=True)
    _nfc_string("providerResultId", provider_result_id, identifier=True)
    if stt_attempt_generation is not None:
        _bounded_u64("sttAttemptGeneration", stt_attempt_generation)
    payload = _identity_prefix("tars-transcript-segment-v2", key, [])
    payload += struct.pack(">I", len(ordered))
    for item in ordered:
        payload += _length_prefixed(item.coverage_id, "coverageId")
    payload += struct.pack(">QQQ", text_first_sample, text_last_sample_exclusive, provider_result_ordinal)
    payload += _length_prefixed(provider_name, "providerName")
    payload += _length_prefixed(provider_result_id, "providerResultId")
    if stt_attempt_generation is None:
        payload += b"\0"
    else:
        payload += b"\1" + struct.pack(">Q", stt_attempt_generation)
    return payload


def transcript_segment_id(*args: Any, **kwargs: Any) -> str:
    return "seg_" + _sha256(transcript_segment_bytes(*args, **kwargs))


@dataclass(frozen=True)
class AudioChunkV2:
    key: StreamKey
    sequence: int
    first_sample: int
    last_sample_exclusive: int
    sample_rate_hertz: int
    channel_count: int
    duration_ms: int
    payload: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.key, StreamKey):
            raise ProtocolV2Violation("chunk key is invalid")
        _bounded_u64("sequence", self.sequence)
        _bounded_u64("firstSample", self.first_sample)
        _bounded_u64("lastSampleExclusive", self.last_sample_exclusive)
        if self.last_sample_exclusive <= self.first_sample:
            raise ProtocolV2Violation("chunk sample range is empty")
        _non_negative("sampleRateHertz", self.sample_rate_hertz)
        if not MIN_SAMPLE_RATE_HERTZ <= self.sample_rate_hertz <= MAX_SAMPLE_RATE_HERTZ:
            raise ProtocolV2Violation("sample rate is outside v2 bounds")
        if self.channel_count not in (1, 2):
            raise ProtocolV2Violation("channel count is outside v2 bounds")
        if not MIN_AUDIO_DURATION_MS <= self.duration_ms <= MAX_AUDIO_DURATION_MS:
            raise ProtocolV2Violation("chunk duration is outside v2 bounds")
        if not isinstance(self.payload, bytes):
            raise ProtocolV2Violation("payload must be bytes")
        frames = self.last_sample_exclusive - self.first_sample
        expected_bytes = frames * self.channel_count * 2
        if len(self.payload) != expected_bytes or not 0 < len(self.payload) <= MAX_AUDIO_PAYLOAD_BYTES:
            raise ProtocolV2Violation("payload size does not match sample range")
        if frames * 1000 != self.duration_ms * self.sample_rate_hertz:
            raise ProtocolV2Violation("duration does not match sample range")
        if frames > min(MAX_CUSTODY_FRAMES, 2 * self.sample_rate_hertz):
            raise ProtocolV2Violation("chunk exceeds custody frame bound")

    @property
    def coverage(self) -> AtomicCoverage:
        return AtomicCoverage(self.key, self.sequence, self.first_sample, self.last_sample_exclusive)

    @property
    def payload_digest_sha256(self) -> str:
        return _sha256(self.payload)


@dataclass(frozen=True)
class TerminalClaim:
    kind: TerminalKind
    key: StreamKey
    atomic: tuple[AtomicCoverage, ...]
    reason: Optional[str] = None
    attempt_generation: Optional[int] = None

    def __post_init__(self) -> None:
        _validate_atomic_list(self.key, self.atomic)
        if self.kind is TerminalKind.GAP:
            if not self.reason:
                raise ProtocolV2Violation("gap requires a reason")
            _nfc_string("gap reason", self.reason, identifier=True)
        elif self.reason is not None:
            raise ProtocolV2Violation("transcript claim cannot carry a gap reason")
        if self.attempt_generation is not None:
            _bounded_u64("attemptGeneration", self.attempt_generation)

    @property
    def coverage_id(self) -> str:
        return terminal_coverage_id(self.key, self.atomic)


@dataclass(frozen=True)
class TranscriptSegment:
    key: StreamKey
    atomic: tuple[AtomicCoverage, ...]
    text_first_sample: int
    text_last_sample_exclusive: int
    provider_result_ordinal: int
    provider_name: str
    provider_result_id: str
    text: str
    stt_attempt_generation: Optional[int] = None

    def __post_init__(self) -> None:
        _validate_atomic_list(self.key, self.atomic)
        _nfc_string("text", self.text)
        if not self.text:
            raise ProtocolV2Violation("transcript text must be non-empty")
        transcript_segment_bytes(
            self.key,
            self.atomic,
            self.text_first_sample,
            self.text_last_sample_exclusive,
            self.provider_result_ordinal,
            self.provider_name,
            self.provider_result_id,
            self.stt_attempt_generation,
        )

    @property
    def segment_id(self) -> str:
        return transcript_segment_id(
            self.key,
            self.atomic,
            self.text_first_sample,
            self.text_last_sample_exclusive,
            self.provider_result_ordinal,
            self.provider_name,
            self.provider_result_id,
            self.stt_attempt_generation,
        )


class CustodyLedger:
    """Bounded interval/terminal projection for one source stream."""

    def __init__(self, key: StreamKey) -> None:
        self.key = key
        self.admitted = IntervalSet()
        self.forwarded = IntervalSet()
        self.terminal: dict[str, TerminalClaim] = {}
        self.segments: dict[str, TranscriptSegment] = {}
        self._atomic: dict[str, AtomicCoverage] = {}

    def admit(self, coverage: AtomicCoverage) -> None:
        if coverage.key != self.key:
            raise ProtocolV2Violation("admission key mismatch")
        self._atomic[coverage.coverage_id] = coverage
        self.admitted = self.admitted.add(
            Interval(coverage.sequence, coverage.sequence, coverage.first_sample, coverage.last_sample_exclusive)
        )

    def forward(self, coverage: AtomicCoverage) -> None:
        if coverage.coverage_id not in self._atomic:
            raise ProtocolV2Violation("forwarding requires admitted atomic coverage")
        if any(
            coverage.coverage_id in {item.coverage_id for item in claim.atomic}
            for claim in self.terminal.values()
        ):
            raise ProtocolV2Violation("terminalized coverage cannot be forwarded")
        if self.forwarded.contains(coverage):
            return
        self.forwarded = self.forwarded.add(
            Interval(coverage.sequence, coverage.sequence, coverage.first_sample, coverage.last_sample_exclusive)
        )

    def commit_segment(self, segment: TranscriptSegment) -> bool:
        if segment.key != self.key:
            raise ProtocolV2Violation("segment key mismatch")
        existing = self.segments.get(segment.segment_id)
        if existing is not None:
            if existing != segment:
                raise ProtocolV2Violation("segment identity reused with different content")
            return False
        if any(item.coverage_id not in self._atomic for item in segment.atomic):
            raise ProtocolV2Violation("segment references unknown atomic coverage")
        if any(not self.forwarded.contains(item) for item in segment.atomic):
            raise ProtocolV2Violation("segment requires forwarded atomic coverage")
        self.segments[segment.segment_id] = segment
        return True

    def commit_terminal(self, claim: TerminalClaim) -> bool:
        if claim.key != self.key:
            raise ProtocolV2Violation("terminal claim key mismatch")
        existing = self.terminal.get(claim.coverage_id)
        if existing is not None:
            if existing != claim:
                raise ProtocolV2Violation("terminal identity reused with different claim")
            return False
        if any(item.coverage_id not in self._atomic for item in claim.atomic):
            raise ProtocolV2Violation("terminal claim references unknown atomic coverage")
        if claim.kind is TerminalKind.GAP and any(
            self.forwarded.contains(item) for item in claim.atomic
        ):
            raise ProtocolV2Violation("gap cannot overwrite forwarded atomic coverage")
        for previous in self.terminal.values():
            previous_ids = {item.coverage_id for item in previous.atomic}
            if previous_ids.intersection(item.coverage_id for item in claim.atomic):
                raise ProtocolV2Violation("atomic coverage already terminalized")
        if claim.kind is TerminalKind.TRANSCRIPT and any(
            not self.forwarded.contains(item) for item in claim.atomic
        ):
            raise ProtocolV2Violation("transcript claim requires forwarded atomic coverage")
        self.terminal[claim.coverage_id] = claim
        return True

    def release_authorized(self, coverage: AtomicCoverage) -> bool:
        """Release only an exact forwarded/terminal interval with no prior gap."""
        if coverage.coverage_id not in self._atomic:
            return False
        current_terminal = any(
            coverage.coverage_id in {item.coverage_id for item in claim.atomic}
            for claim in self.terminal.values()
        )
        if not self.forwarded.contains(coverage) and not current_terminal:
            return False
        # Forwarded is a release authority only when all earlier atomic ranges
        # are terminal; never let a scalar prefix skip an unresolved gap.
        for item in self._atomic.values():
            prior_terminal = any(
                item.coverage_id in {part.coverage_id for part in claim.atomic}
                for claim in self.terminal.values()
            )
            if item.sequence < coverage.sequence and not (
                self.forwarded.contains(item) or prior_terminal
            ):
                return False
        return True
