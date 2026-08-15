"""Canonical v2 frame admission, retry, and contiguous forwarding ledger."""

from __future__ import annotations

from dataclasses import dataclass

from .admission import AdmissionController
from .contracts import AudioFrame, FailureCode, GatewayError, TransportState


@dataclass(frozen=True)
class ForwardedRange:
    first_sequence: int
    last_sequence_inclusive: int


class TransportLedger:
    """Memory-only transport state; forwarding releases resident payload bytes."""

    def __init__(self, admission: AdmissionController) -> None:
        self.admission = admission
        self.state = TransportState.ADMITTING
        self._frames: dict[int, AudioFrame] = {}
        self._digests: dict[int, str] = {}
        self._next_sequence = 0
        self._forwarded_prefix = -1
        self._journal: list[ForwardedRange] = []

    @property
    def resident_bytes(self) -> int:
        return sum(frame.payload_bytes for frame in self._frames.values())

    @property
    def forwarded_prefix(self) -> int:
        return self._forwarded_prefix

    @property
    def journal(self) -> tuple[ForwardedRange, ...]:
        return tuple(self._journal)

    def capture(self, frame: AudioFrame, *, owner_id: str, runtime_epoch: int, now_ms: int) -> None:
        if self.state in (TransportState.FENCED, TransportState.CLOSED):
            raise GatewayError(FailureCode.STALE_FENCE)
        if frame.sequence != self._next_sequence:
            if frame.sequence in self._digests and self._digests[frame.sequence] == frame.payload_digest:
                return
            raise GatewayError(FailureCode.OUT_OF_ORDER)
        self.admission.admit(frame, owner_id=owner_id, runtime_epoch=runtime_epoch, now_ms=now_ms)
        self._frames[frame.sequence] = frame
        self._digests[frame.sequence] = frame.payload_digest
        self._next_sequence += 1
        self.state = TransportState.FORWARDING

    def retry(self, frame: AudioFrame, *, owner_id: str, runtime_epoch: int, now_ms: int) -> None:
        if frame.sequence not in self._digests:
            raise GatewayError(FailureCode.CONFLICT)
        if self._digests[frame.sequence] != frame.payload_digest:
            raise GatewayError(FailureCode.CONFLICT)
        self.admission.authority.require_lease(frame.context.session_id, owner_id, runtime_epoch)
        self.capture(frame, owner_id=owner_id, runtime_epoch=runtime_epoch, now_ms=now_ms)

    def journal_forward(self, first_sequence: int, last_sequence_inclusive: int) -> ForwardedRange:
        if first_sequence != self._forwarded_prefix + 1:
            raise GatewayError(FailureCode.OUT_OF_ORDER)
        if last_sequence_inclusive < first_sequence or last_sequence_inclusive >= self._next_sequence:
            raise GatewayError(FailureCode.CONFLICT)
        if any(sequence not in self._frames for sequence in range(first_sequence, last_sequence_inclusive + 1)):
            raise GatewayError(FailureCode.CONFLICT)
        record = ForwardedRange(first_sequence, last_sequence_inclusive)
        self._journal.append(record)
        self._forwarded_prefix = last_sequence_inclusive
        for sequence in range(first_sequence, last_sequence_inclusive + 1):
            frame = self._frames.pop(sequence)
            self.admission.release_frame(frame)
        return record

    def fence(self) -> None:
        self.state = TransportState.FENCED

    def close(self) -> None:
        self.state = TransportState.CLOSED
