"""Deterministic, memory-only Phase 1A gateway/provider state simulator."""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .model import (
    AudioChunk,
    EventLedger,
    KnownCoverage,
    ProtocolViolation,
    Source,
    StreamKey,
    TerminalKind,
    TerminalLedger,
    TerminalOutcome,
    UnknownEndCoverage,
)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class AuthorizationRejected(ProtocolViolation):
    """Non-enumerating rejection for fake Phase 1A identity failures."""


class PrincipalState(str, Enum):
    ACTIVE = "active"
    UNAUTHENTICATED = "unauthenticated"
    EXPIRED = "expired"
    REVOKED = "revoked"


class CrashPoint(str, Enum):
    BEFORE_PROVIDER_WRITE = "before_provider_write"
    AFTER_PROVIDER_WRITE_BEFORE_JOURNAL = "after_provider_write_before_journal"
    AFTER_JOURNAL_BEFORE_TRANSCRIPT = "after_journal_before_transcript"
    AFTER_TERMINAL_COMMIT = "after_terminal_commit"


@dataclass(frozen=True)
class FakePrincipal:
    organization_id: str
    user_id: str
    state: PrincipalState = PrincipalState.ACTIVE

    def __post_init__(self) -> None:
        if not isinstance(self.state, PrincipalState):
            raise ProtocolViolation("fake principal state is invalid")
        for name, value in (
            ("organizationId", self.organization_id),
            ("userId", self.user_id),
        ):
            if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
                raise ProtocolViolation(name + " is not a valid fixture identifier")


@dataclass(frozen=True)
class StageWatermark:
    sequence: Optional[int] = None
    last_sample_exclusive: Optional[int] = None
    captured_at_monotonic_ns: Optional[int] = None

    def __post_init__(self) -> None:
        present = (
            self.sequence is not None,
            self.last_sample_exclusive is not None,
            self.captured_at_monotonic_ns is not None,
        )
        if any(present) and not all(present):
            raise ProtocolViolation("watermark sequence, sample, and time must all be set")
        if self.sequence is not None:
            if (
                isinstance(self.sequence, bool)
                or not isinstance(self.sequence, int)
                or self.sequence < 0
            ):
                raise ProtocolViolation("watermark sequence must be non-negative")
            if (
                isinstance(self.last_sample_exclusive, bool)
                or not isinstance(self.last_sample_exclusive, int)
                or self.last_sample_exclusive < 1
            ):
                raise ProtocolViolation("watermark sample must be positive")
            if (
                isinstance(self.captured_at_monotonic_ns, bool)
                or not isinstance(self.captured_at_monotonic_ns, int)
                or self.captured_at_monotonic_ns < 0
            ):
                raise ProtocolViolation("watermark time must be non-negative")


EMPTY_WATERMARK = StageWatermark()


@dataclass(frozen=True)
class WatermarkSet:
    admitted: StageWatermark = EMPTY_WATERMARK
    forwarded: StageWatermark = EMPTY_WATERMARK
    durable_transcript: StageWatermark = EMPTY_WATERMARK


@dataclass(frozen=True)
class ForwardingRecord:
    coverage: KnownCoverage
    stt_attempt_generation: int
    logical_time: int
    first_captured_at_monotonic_ns: int
    last_captured_at_monotonic_ns: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "source": self.coverage.key.source.value,
            "firstSequence": self.coverage.first_sequence,
            "lastSequenceInclusive": self.coverage.last_sequence_inclusive,
            "firstSample": self.coverage.first_sample,
            "lastSampleExclusive": self.coverage.last_sample_exclusive,
            "firstCapturedAtMonotonicNs": self.first_captured_at_monotonic_ns,
            "lastCapturedAtMonotonicNs": self.last_captured_at_monotonic_ns,
            "sttAttemptGeneration": self.stt_attempt_generation,
            "logicalTime": self.logical_time,
        }


@dataclass(frozen=True)
class ReconnectPlan:
    authoritative: WatermarkSet
    active_capture_generation: int
    active_stt_attempt_generation: int
    resend_ranges: Tuple[KnownCoverage, ...]


@dataclass(frozen=True)
class _PendingProviderWrite:
    coverage: KnownCoverage
    sequences: Tuple[int, ...]
    stt_attempt_generation: int


class _StreamState:
    def __init__(
        self,
        key: StreamKey,
        max_client_buffer_bytes: int,
        max_gateway_queue_bytes: int,
    ) -> None:
        self.key = key
        self.max_client_buffer_bytes = max_client_buffer_bytes
        self.max_gateway_queue_bytes = max_gateway_queue_bytes
        self.event_ledger = EventLedger()
        self.terminal_ledger = TerminalLedger()
        self.coverage_by_sequence: Dict[int, KnownCoverage] = {}
        self.capture_time_by_sequence: Dict[int, int] = {}
        self.client_chunks: Dict[int, AudioChunk] = {}
        self.gateway_chunks: Dict[int, AudioChunk] = {}
        self.forwarding_records: List[ForwardingRecord] = []
        self.unresolved_forwarding: Dict[str, ForwardingRecord] = {}
        self.pending_provider_write: Optional[_PendingProviderWrite] = None
        self.active_stt_attempt_generation = 0
        self.last_captured_sequence: Optional[int] = None
        self.last_captured_sample = 0
        self.forwarded = EMPTY_WATERMARK
        self.closed = False

    @property
    def client_payload_bytes(self) -> int:
        return sum(len(chunk.payload) for chunk in self.client_chunks.values())

    @property
    def gateway_payload_bytes(self) -> int:
        return sum(len(chunk.payload) for chunk in self.gateway_chunks.values())

    def admitted_watermark(self) -> StageWatermark:
        sequence = self.forwarded.sequence
        sample = self.forwarded.last_sample_exclusive
        capture_time = self.forwarded.captured_at_monotonic_ns
        expected = 0 if sequence is None else sequence + 1
        while expected in self.gateway_chunks:
            coverage = self.coverage_by_sequence[expected]
            sequence = expected
            sample = coverage.last_sample_exclusive
            capture_time = self.capture_time_by_sequence[expected]
            expected += 1
        if sequence is None:
            return EMPTY_WATERMARK
        return StageWatermark(sequence, sample, capture_time)

    def durable_transcript_watermark(self) -> StageWatermark:
        expected_sequence = 0
        expected_sample = 0
        ordered = sorted(
            (
                outcome
                for outcome in self.terminal_ledger.outcomes(self.key)
                if outcome.kind is TerminalKind.TRANSCRIPT
            ),
            key=lambda outcome: outcome.coverage.first_sequence,
        )
        watermark = EMPTY_WATERMARK
        for outcome in ordered:
            coverage = outcome.coverage
            assert isinstance(coverage, KnownCoverage)
            if (
                coverage.first_sequence != expected_sequence
                or coverage.first_sample != expected_sample
            ):
                break
            watermark = StageWatermark(
                coverage.last_sequence_inclusive,
                coverage.last_sample_exclusive,
                self.capture_time_by_sequence[coverage.last_sequence_inclusive],
            )
            expected_sequence = coverage.last_sequence_inclusive + 1
            expected_sample = coverage.last_sample_exclusive
        return watermark

    def watermarks(self) -> WatermarkSet:
        return WatermarkSet(
            admitted=self.admitted_watermark(),
            forwarded=self.forwarded,
            durable_transcript=self.durable_transcript_watermark(),
        )

    def capture(self, chunk: AudioChunk) -> bool:
        if chunk.key != self.key:
            raise ProtocolViolation("chunk stream key does not match simulator stream")

        if chunk.sequence in self.coverage_by_sequence:
            self.event_ledger.observe(chunk.event_id, chunk.payload_digest_sha256)
            return False
        if self.closed:
            raise ProtocolViolation("stream is closed after a terminal capture failure")

        expected_sequence = (
            0 if self.last_captured_sequence is None else self.last_captured_sequence + 1
        )
        if chunk.sequence != expected_sequence:
            raise ProtocolViolation("captured sequence is not strictly contiguous")
        if chunk.first_sample != self.last_captured_sample:
            raise ProtocolViolation("captured sample range is not contiguous")

        self.event_ledger.observe(chunk.event_id, chunk.payload_digest_sha256)

        self.coverage_by_sequence[chunk.sequence] = chunk.coverage
        self.capture_time_by_sequence[chunk.sequence] = chunk.captured_at_monotonic_ns
        self.last_captured_sequence = chunk.sequence
        self.last_captured_sample = chunk.last_sample_exclusive

        if self.client_payload_bytes + len(chunk.payload) > self.max_client_buffer_bytes:
            gap = TerminalOutcome(
                TerminalKind.GAP,
                chunk.coverage,
                self.active_stt_attempt_generation,
                reason_code="buffer_overflow",
            )
            self.terminal_ledger.commit(gap)
            self.closed = True
            return False

        self.client_chunks[chunk.sequence] = chunk
        return True

    def admit(self, sequence: int) -> bool:
        if sequence in self.gateway_chunks:
            return False
        if sequence not in self.client_chunks:
            raise ProtocolViolation("admission requires a retained client chunk")

        admitted = self.admitted_watermark()
        expected = 0 if admitted.sequence is None else admitted.sequence + 1
        if sequence != expected:
            raise ProtocolViolation("gateway admission is not contiguous")

        chunk = self.client_chunks[sequence]
        if self.gateway_payload_bytes + len(chunk.payload) > self.max_gateway_queue_bytes:
            raise ProtocolViolation("gateway transient queue capacity exceeded")
        self.gateway_chunks[sequence] = chunk
        return True

    def provider_write(self, count: int, attempt_generation: int) -> KnownCoverage:
        if self.pending_provider_write is not None:
            raise ProtocolViolation("provider write awaits forwarding-journal resolution")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ProtocolViolation("provider write count must be positive")
        if attempt_generation != self.active_stt_attempt_generation:
            raise ProtocolViolation("stale STT attempt generation")

        start = 0 if self.forwarded.sequence is None else self.forwarded.sequence + 1
        sequences = tuple(range(start, start + count))
        if any(sequence not in self.gateway_chunks for sequence in sequences):
            raise ProtocolViolation("provider write requires contiguous admitted chunks")

        first = self.coverage_by_sequence[sequences[0]]
        last = self.coverage_by_sequence[sequences[-1]]
        coverage = KnownCoverage(
            self.key,
            first_sequence=first.first_sequence,
            last_sequence_inclusive=last.last_sequence_inclusive,
            first_sample=first.first_sample,
            last_sample_exclusive=last.last_sample_exclusive,
        )
        self.pending_provider_write = _PendingProviderWrite(
            coverage, sequences, attempt_generation
        )
        return coverage

    def journal_provider_write(self, logical_time: int) -> ForwardingRecord:
        pending = self.pending_provider_write
        if pending is None:
            raise ProtocolViolation("no provider write is pending")
        record = ForwardingRecord(
            pending.coverage,
            pending.stt_attempt_generation,
            logical_time,
            self.capture_time_by_sequence[pending.sequences[0]],
            self.capture_time_by_sequence[pending.sequences[-1]],
        )
        self.forwarding_records.append(record)
        self.unresolved_forwarding[pending.coverage.coverage_id] = record
        self.forwarded = StageWatermark(
            pending.coverage.last_sequence_inclusive,
            pending.coverage.last_sample_exclusive,
            self.capture_time_by_sequence[pending.coverage.last_sequence_inclusive],
        )
        for sequence in pending.sequences:
            del self.gateway_chunks[sequence]
            del self.client_chunks[sequence]
        self.pending_provider_write = None
        return record

    def commit_transcript(
        self,
        coverage: KnownCoverage,
        result_ordinal: int,
    ) -> Tuple[TerminalOutcome, bool]:
        record = self.unresolved_forwarding.get(coverage.coverage_id)
        if record is None:
            for existing in self.terminal_ledger.outcomes(self.key):
                if (
                    existing.kind is TerminalKind.TRANSCRIPT
                    and existing.coverage == coverage
                    and existing.result_ordinal == result_ordinal
                ):
                    return existing, False
            raise ProtocolViolation(
                "transcript coverage lacks a journaled provider-forwarding record"
            )
        outcome = TerminalOutcome(
            TerminalKind.TRANSCRIPT,
            coverage,
            record.stt_attempt_generation,
            result_ordinal=result_ordinal,
        )
        committed = self.terminal_ledger.commit(outcome)
        if committed:
            del self.unresolved_forwarding[coverage.coverage_id]
        return outcome, committed

    def reconnect_ranges(self) -> Tuple[KnownCoverage, ...]:
        sequences = sorted(self.client_chunks)
        if not sequences:
            return ()

        ranges: List[KnownCoverage] = []
        first_sequence = sequences[0]
        last_sequence = sequences[0]
        first_sample = self.coverage_by_sequence[first_sequence].first_sample
        last_sample = self.coverage_by_sequence[last_sequence].last_sample_exclusive

        for sequence in sequences[1:]:
            coverage = self.coverage_by_sequence[sequence]
            if sequence == last_sequence + 1 and coverage.first_sample == last_sample:
                last_sequence = sequence
                last_sample = coverage.last_sample_exclusive
                continue
            ranges.append(
                KnownCoverage(
                    self.key,
                    first_sequence,
                    last_sequence,
                    first_sample,
                    last_sample,
                )
            )
            first_sequence = sequence
            last_sequence = sequence
            first_sample = coverage.first_sample
            last_sample = coverage.last_sample_exclusive

        ranges.append(
            KnownCoverage(
                self.key,
                first_sequence,
                last_sequence,
                first_sample,
                last_sample,
            )
        )
        return tuple(ranges)


class OfflineProtocolSimulator:
    """In-memory protocol oracle with fake identity and deterministic time."""

    def __init__(
        self,
        session_id: str,
        owner: FakePrincipal,
        device_id: str = "device_phase1a",
        capture_generation: int = 0,
        max_client_buffer_bytes: int = 64_000,
        max_gateway_queue_bytes: int = 64_000,
    ) -> None:
        StreamKey(session_id, "stream_validation", capture_generation, Source.MICROPHONE)
        if not isinstance(owner, FakePrincipal) or owner.state is not PrincipalState.ACTIVE:
            raise ProtocolViolation("simulator owner must be active")
        if not isinstance(device_id, str) or not _IDENTIFIER_RE.fullmatch(device_id):
            raise ProtocolViolation("deviceId is not a valid fixture identifier")
        for name, value in (
            ("maxClientBufferBytes", max_client_buffer_bytes),
            ("maxGatewayQueueBytes", max_gateway_queue_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ProtocolViolation(name + " must be a positive integer")

        self.session_id = session_id
        self.owner = owner
        self.device_id = device_id
        self.capture_generation = capture_generation
        self.max_client_buffer_bytes = max_client_buffer_bytes
        self.max_gateway_queue_bytes = max_gateway_queue_bytes
        self._streams: Dict[StreamKey, _StreamState] = {}
        self._logical_time = 0

    def _authorize(self, principal: FakePrincipal) -> None:
        if (
            not isinstance(principal, FakePrincipal)
            or principal.state is not PrincipalState.ACTIVE
            or principal.organization_id != self.owner.organization_id
            or principal.user_id != self.owner.user_id
        ):
            raise AuthorizationRejected("message rejected")

    def _validate_fence(self, key: StreamKey, device_id: str) -> None:
        if (
            key.session_id != self.session_id
            or key.capture_generation != self.capture_generation
            or device_id != self.device_id
        ):
            raise ProtocolViolation("stale or foreign capture fence")

    def _state(self, key: StreamKey) -> _StreamState:
        state = self._streams.get(key)
        if state is None:
            state = _StreamState(
                key,
                self.max_client_buffer_bytes,
                self.max_gateway_queue_bytes,
            )
            self._streams[key] = state
        return state

    def capture(self, principal: FakePrincipal, chunk: AudioChunk) -> bool:
        self._authorize(principal)
        self._validate_fence(chunk.key, chunk.device_id)
        return self._state(chunk.key).capture(chunk)

    def admit(
        self,
        principal: FakePrincipal,
        key: StreamKey,
        sequence: int,
        device_id: Optional[str] = None,
    ) -> bool:
        self._authorize(principal)
        self._validate_fence(key, self.device_id if device_id is None else device_id)
        return self._state(key).admit(sequence)

    def submit(self, principal: FakePrincipal, chunk: AudioChunk) -> bool:
        captured = self.capture(principal, chunk)
        if not captured:
            return False
        return self.admit(principal, chunk.key, chunk.sequence, chunk.device_id)

    def provider_write(
        self,
        principal: FakePrincipal,
        key: StreamKey,
        count: int = 1,
        attempt_generation: Optional[int] = None,
        device_id: Optional[str] = None,
    ) -> KnownCoverage:
        self._authorize(principal)
        self._validate_fence(key, self.device_id if device_id is None else device_id)
        state = self._state(key)
        attempt = (
            state.active_stt_attempt_generation
            if attempt_generation is None
            else attempt_generation
        )
        return state.provider_write(count, attempt)

    def journal_provider_write(
        self,
        principal: FakePrincipal,
        key: StreamKey,
        device_id: Optional[str] = None,
    ) -> ForwardingRecord:
        self._authorize(principal)
        self._validate_fence(key, self.device_id if device_id is None else device_id)
        self._logical_time += 1
        return self._state(key).journal_provider_write(self._logical_time)

    def commit_transcript(
        self,
        principal: FakePrincipal,
        key: StreamKey,
        coverage: KnownCoverage,
        result_ordinal: int = 0,
        device_id: Optional[str] = None,
    ) -> Tuple[TerminalOutcome, bool]:
        self._authorize(principal)
        self._validate_fence(key, self.device_id if device_id is None else device_id)
        if coverage.key != key:
            raise ProtocolViolation("transcript coverage belongs to another stream")
        return self._state(key).commit_transcript(coverage, result_ordinal)

    def rotate_stt_attempt(
        self,
        principal: FakePrincipal,
        key: StreamKey,
        device_id: Optional[str] = None,
    ) -> int:
        self._authorize(principal)
        self._validate_fence(key, self.device_id if device_id is None else device_id)
        state = self._state(key)
        if state.pending_provider_write is not None:
            raise ProtocolViolation("cannot rotate an ambiguous provider write")
        state.active_stt_attempt_generation += 1
        return state.active_stt_attempt_generation

    def recover(
        self,
        principal: FakePrincipal,
        key: StreamKey,
        crash_point: CrashPoint,
        device_id: Optional[str] = None,
    ) -> Tuple[TerminalOutcome, ...]:
        self._authorize(principal)
        self._validate_fence(key, self.device_id if device_id is None else device_id)
        if not isinstance(crash_point, CrashPoint):
            raise ProtocolViolation("crash point is invalid")
        state = self._state(key)

        if crash_point is CrashPoint.BEFORE_PROVIDER_WRITE:
            if state.pending_provider_write is not None:
                raise ProtocolViolation("provider write already occurred")
            state.gateway_chunks.clear()
            return ()

        if crash_point is CrashPoint.AFTER_PROVIDER_WRITE_BEFORE_JOURNAL:
            pending = state.pending_provider_write
            if pending is None:
                raise ProtocolViolation("crash point requires an unjournaled write")
            gap = TerminalOutcome(
                TerminalKind.GAP,
                pending.coverage,
                pending.stt_attempt_generation,
                reason_code="unknown_forwarding_state",
            )
            state.terminal_ledger.commit(gap)
            for sequence in pending.sequences:
                state.gateway_chunks.pop(sequence, None)
                state.client_chunks.pop(sequence, None)
            state.pending_provider_write = None
            state.closed = True
            return (gap,)

        if crash_point is CrashPoint.AFTER_JOURNAL_BEFORE_TRANSCRIPT:
            if not state.unresolved_forwarding:
                raise ProtocolViolation("crash point requires unresolved forwarding")
            gaps = []
            for coverage_id in sorted(state.unresolved_forwarding):
                record = state.unresolved_forwarding[coverage_id]
                gap = TerminalOutcome(
                    TerminalKind.GAP,
                    record.coverage,
                    record.stt_attempt_generation,
                    reason_code="stt_stream_failed",
                )
                state.terminal_ledger.commit(gap)
                gaps.append(gap)
            state.unresolved_forwarding.clear()
            return tuple(gaps)

        outcomes = state.terminal_ledger.outcomes(key)
        if not outcomes:
            raise ProtocolViolation("crash point requires a terminal outcome")
        latest = outcomes[-1]
        if state.terminal_ledger.commit(latest):
            raise AssertionError("terminal replay unexpectedly created a duplicate")
        return (latest,)

    def reconnect(
        self,
        principal: FakePrincipal,
        key: StreamKey,
        client_observed: WatermarkSet = WatermarkSet(),
        device_id: Optional[str] = None,
    ) -> ReconnectPlan:
        self._authorize(principal)
        self._validate_fence(key, self.device_id if device_id is None else device_id)
        state = self._state(key)
        authoritative = state.watermarks()
        for name in ("admitted", "forwarded", "durable_transcript"):
            client_stage = getattr(client_observed, name)
            client_value = client_stage.sequence
            server_value = getattr(authoritative, name).sequence
            if client_value is not None and (
                server_value is None or client_value > server_value
            ):
                raise ProtocolViolation(name + " watermark is ahead of authority")
            if client_value is not None:
                expected = state.coverage_by_sequence.get(client_value)
                if (
                    expected is None
                    or client_stage.last_sample_exclusive
                    != expected.last_sample_exclusive
                    or client_stage.captured_at_monotonic_ns
                    != state.capture_time_by_sequence[client_value]
                ):
                    raise ProtocolViolation(name + " watermark range is inconsistent")
        return ReconnectPlan(
            authoritative=authoritative,
            active_capture_generation=self.capture_generation,
            active_stt_attempt_generation=state.active_stt_attempt_generation,
            resend_ranges=state.reconnect_ranges(),
        )

    def renew_capture_lease(
        self, principal: FakePrincipal, new_device_id: str
    ) -> int:
        self._authorize(principal)
        if not isinstance(new_device_id, str) or not _IDENTIFIER_RE.fullmatch(
            new_device_id
        ):
            raise ProtocolViolation("deviceId is not a valid fixture identifier")
        for state in self._streams.values():
            if (
                state.client_chunks
                or state.gateway_chunks
                or state.pending_provider_write is not None
                or state.unresolved_forwarding
            ):
                raise ProtocolViolation("cannot renew lease with unresolved stream state")
        self.capture_generation += 1
        self.device_id = new_device_id
        return self.capture_generation

    def record_unknown_end_termination(
        self,
        principal: FakePrincipal,
        key: StreamKey,
        first_sequence: int,
        first_sample: int,
        device_id: Optional[str] = None,
    ) -> TerminalOutcome:
        self._authorize(principal)
        self._validate_fence(key, self.device_id if device_id is None else device_id)
        state = self._state(key)
        if state.pending_provider_write is not None or state.unresolved_forwarding:
            raise ProtocolViolation(
                "unknown-end termination cannot replace an exact forwarding boundary"
            )
        if state.client_chunks:
            expected_sequence = min(state.client_chunks)
            expected_sample = state.coverage_by_sequence[
                expected_sequence
            ].first_sample
        else:
            expected_sequence = (
                0
                if state.last_captured_sequence is None
                else state.last_captured_sequence + 1
            )
            expected_sample = state.last_captured_sample
        if first_sequence != expected_sequence or first_sample != expected_sample:
            raise ProtocolViolation("unknown-end gap must start at the first unresolved range")
        coverage = UnknownEndCoverage(key, first_sequence, first_sample)
        gap = TerminalOutcome(
            TerminalKind.GAP,
            coverage,
            state.active_stt_attempt_generation,
            reason_code="process_terminated",
        )
        state.terminal_ledger.commit(gap)
        state.client_chunks.clear()
        state.gateway_chunks.clear()
        state.pending_provider_write = None
        state.closed = True
        return gap

    def watermarks(self, key: StreamKey) -> WatermarkSet:
        return self._state(key).watermarks()

    def forwarding_records(self, key: StreamKey) -> Tuple[ForwardingRecord, ...]:
        return tuple(self._state(key).forwarding_records)

    def terminal_outcomes(self, key: StreamKey) -> Tuple[TerminalOutcome, ...]:
        return self._state(key).terminal_ledger.outcomes(key)

    def raw_payload_bytes(self, key: StreamKey) -> int:
        state = self._state(key)
        return state.client_payload_bytes + state.gateway_payload_bytes

    def diagnostics(self) -> Dict[str, object]:
        streams = []
        for key in sorted(
            self._streams,
            key=lambda item: (
                item.capture_generation,
                item.source.value,
                item.stream_id,
            ),
        ):
            state = self._streams[key]
            watermarks = state.watermarks()
            streams.append(
                {
                    "source": key.source.value,
                    "captureGeneration": key.capture_generation,
                    "clientQueueDepth": len(state.client_chunks),
                    "gatewayQueueDepth": len(state.gateway_chunks),
                    "clientPayloadBytes": state.client_payload_bytes,
                    "gatewayPayloadBytes": state.gateway_payload_bytes,
                    "admittedSequence": watermarks.admitted.sequence,
                    "forwardedSequence": watermarks.forwarded.sequence,
                    "durableTranscriptSequence": (
                        watermarks.durable_transcript.sequence
                    ),
                    "terminalOutcomeCount": len(
                        state.terminal_ledger.outcomes(key)
                    ),
                    "forwardingRecordCount": len(state.forwarding_records),
                    "activeSttAttemptGeneration": (
                        state.active_stt_attempt_generation
                    ),
                }
            )
        return {
            "protocolVersion": 1,
            "activeCaptureGeneration": self.capture_generation,
            "streamCount": len(streams),
            "streams": streams,
        }
