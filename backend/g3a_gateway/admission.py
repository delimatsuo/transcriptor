"""Disclosure, authority, and bounded admission for G3A."""

from __future__ import annotations

from dataclasses import dataclass

from .authority import AuthorityStore
from .contracts import (
    ActorContext,
    AudioFrame,
    FailureCode,
    GatewayError,
    QuotaLimits,
    Source,
)


@dataclass
class _Bucket:
    second: int
    events: int = 0
    audio_bytes: int = 0
    metadata_bytes: int = 0


class QuotaLedger:
    """Atomic source/session quota rows with no queue allocation on failure."""

    def __init__(self, limits: QuotaLimits | None = None) -> None:
        self.limits = limits or QuotaLimits()
        self._source_buckets: dict[tuple[str, str, Source, int], _Bucket] = {}
        self._session_buckets: dict[tuple[str, int], _Bucket] = {}
        self._attempts: dict[str, int] = {}
        self._pending_handshakes = 0
        self._receive_bytes = 0
        self._resident_bytes = 0
        self._source_resident: dict[tuple[str, str, Source, int], int] = {}

    def begin_handshake(self) -> None:
        if self._pending_handshakes >= self.limits.max_pending_handshakes:
            raise GatewayError(FailureCode.QUOTA_EXCEEDED)
        self._pending_handshakes += 1

    def finish_handshake(self) -> None:
        self._pending_handshakes = max(0, self._pending_handshakes - 1)

    def reserve_receive(self, amount: int) -> None:
        if amount < 0 or self._receive_bytes + amount > self.limits.max_receive_bytes:
            raise GatewayError(FailureCode.QUOTA_EXCEEDED)
        self._receive_bytes += amount

    def release_receive(self, amount: int) -> None:
        self._receive_bytes = max(0, self._receive_bytes - max(0, amount))

    def reserve_resident(
        self,
        amount: int,
        *,
        source_key: tuple[str, str, Source, int] | None = None,
    ) -> None:
        if amount < 0 or self._resident_bytes + amount > self.limits.max_receive_bytes:
            raise GatewayError(FailureCode.QUOTA_EXCEEDED)
        if source_key is not None:
            source_total = self._source_resident.get(source_key, 0) + amount
            if source_total > self.limits.max_resident_bytes:
                raise GatewayError(FailureCode.QUOTA_EXCEEDED)
            self._source_resident[source_key] = source_total
        self._resident_bytes += amount

    def release_resident(
        self,
        amount: int,
        *,
        source_key: tuple[str, str, Source, int] | None = None,
    ) -> None:
        self._resident_bytes = max(0, self._resident_bytes - max(0, amount))
        if source_key is not None:
            remaining = self._source_resident.get(source_key, 0) - max(0, amount)
            if remaining > 0:
                self._source_resident[source_key] = remaining
            else:
                self._source_resident.pop(source_key, None)

    def burn_attempt(self, session_id: str) -> int:
        attempts = self._attempts.get(session_id, 0) + 1
        if attempts > self.limits.max_provider_attempts:
            raise GatewayError(FailureCode.QUOTA_EXCEEDED)
        self._attempts[session_id] = attempts
        return attempts

    @staticmethod
    def _metadata_charge(frame: AudioFrame) -> int:
        # The protocol length prefix is charged to the metadata budget.
        return frame.metadata_bytes + 4

    def _reserve_bucket(self, bucket: _Bucket, frame: AudioFrame, *, multiplier: int = 1) -> None:
        metadata_charge = self._metadata_charge(frame)
        next_events = bucket.events + 1
        next_audio = bucket.audio_bytes + frame.payload_bytes
        next_metadata = bucket.metadata_bytes + metadata_charge
        if (
            next_events > self.limits.audio_event_burst * multiplier
            or next_audio > self.limits.audio_bytes_burst * multiplier
            or next_metadata > self.limits.metadata_bytes_burst * multiplier
            or bucket.events >= self.limits.audio_events_per_second * multiplier
            or bucket.audio_bytes >= self.limits.audio_bytes_per_second * multiplier
            or bucket.metadata_bytes >= self.limits.metadata_bytes_per_second * multiplier
        ):
            raise GatewayError(FailureCode.QUOTA_EXCEEDED)
        bucket.events, bucket.audio_bytes, bucket.metadata_bytes = (
            next_events,
            next_audio,
            next_metadata,
        )

    def _reserve_rate(self, frame: AudioFrame, now_ms: int) -> None:
        source_key = (
            frame.context.session_id,
            frame.context.stream_id,
            frame.source,
            frame.context.capture_generation,
        )
        session_key = (frame.context.session_id, frame.context.capture_generation)
        second = now_ms // 1000
        source_bucket = self._source_buckets.get(source_key)
        if source_bucket is None or source_bucket.second != second:
            source_bucket = _Bucket(second)
            self._source_buckets[source_key] = source_bucket
        session_bucket = self._session_buckets.get(session_key)
        if session_bucket is None or session_bucket.second != second:
            session_bucket = _Bucket(second)
            self._session_buckets[session_key] = session_bucket
        # Validate both rows before mutating either, so a failed broader row
        # cannot consume source quota.
        source_copy = _Bucket(
            source_bucket.second,
            source_bucket.events,
            source_bucket.audio_bytes,
            source_bucket.metadata_bytes,
        )
        session_copy = _Bucket(
            session_bucket.second,
            session_bucket.events,
            session_bucket.audio_bytes,
            session_bucket.metadata_bytes,
        )
        self._reserve_bucket(source_copy, frame)
        self._reserve_bucket(session_copy, frame, multiplier=2)
        source_bucket.events, source_bucket.audio_bytes, source_bucket.metadata_bytes = (
            source_copy.events,
            source_copy.audio_bytes,
            source_copy.metadata_bytes,
        )
        session_bucket.events, session_bucket.audio_bytes, session_bucket.metadata_bytes = (
            session_copy.events,
            session_copy.audio_bytes,
            session_copy.metadata_bytes,
        )

    def reserve_retry(self, frame: AudioFrame, now_ms: int) -> None:
        """Consume ingress rate budget for an exact duplicate without custody."""
        self._validate_frame(frame)
        self._reserve_rate(frame, now_ms)

    @staticmethod
    def _validate_frame(frame: AudioFrame) -> None:
        if frame.payload_bytes < 1 or frame.metadata_bytes < 0:
            raise GatewayError(FailureCode.CONFLICT)
        if frame.payload_bytes > 64_000 or frame.metadata_bytes > 4_096:
            raise GatewayError(FailureCode.CONFLICT)
        if frame.duration_ms < 20 or frame.duration_ms > 250:
            raise GatewayError(FailureCode.CONFLICT)
        if frame.sample_rate not in (8_000, 16_000, 44_100, 48_000):
            raise GatewayError(FailureCode.CONFLICT)
        if frame.channels not in (1, 2):
            raise GatewayError(FailureCode.CONFLICT)
        sample_count = frame.last_sample_exclusive - frame.first_sample
        if frame.first_sample < 0 or sample_count <= 0:
            raise GatewayError(FailureCode.CONFLICT)
        if frame.payload_bytes != sample_count * frame.channels * 2:
            raise GatewayError(FailureCode.CONFLICT)
        if not isinstance(frame.source, Source):
            raise GatewayError(FailureCode.CONFLICT)

    def reserve_frame(self, frame: AudioFrame, now_ms: int) -> None:
        self._validate_frame(frame)
        self._reserve_rate(frame, now_ms)
        source_key = (
            frame.context.session_id,
            frame.context.stream_id,
            frame.source,
            frame.context.capture_generation,
        )
        # Custody reservation is transactional across receive and resident
        # counters. No counter is changed when either ceiling would fail.
        if (
            self._receive_bytes + frame.payload_bytes > self.limits.max_receive_bytes
            or self._resident_bytes + frame.payload_bytes > self.limits.max_receive_bytes
            or self._source_resident.get(source_key, 0) + frame.payload_bytes
            > self.limits.max_resident_bytes
        ):
            # Rate tokens are intentionally consumed for attempted ingress,
            # but custody counters are not reserved on a failed admission.
            raise GatewayError(FailureCode.QUOTA_EXCEEDED)
        self._receive_bytes += frame.payload_bytes
        self.reserve_resident(frame.payload_bytes, source_key=source_key)


class AdmissionController:
    """One authority and one quota decision for each frame."""

    def __init__(
        self,
        authority: AuthorityStore,
        limits: QuotaLimits | None = None,
    ) -> None:
        self.authority = authority
        self.quotas = QuotaLedger(limits)
        self._admission_revoked: set[str] = set()

    def revoke_session(self, session_id: str) -> None:
        self._admission_revoked.add(session_id)
        self.authority.revoke_lease(session_id)

    def admit(self, frame: AudioFrame, *, owner_id: str, runtime_epoch: int, now_ms: int) -> None:
        if frame.context.session_id in self._admission_revoked:
            raise GatewayError(FailureCode.REVOKED)
        self.authority.require_lease(frame.context.session_id, owner_id, runtime_epoch)
        self.quotas.reserve_frame(frame, now_ms)

    def release_frame(self, frame: AudioFrame) -> None:
        self.quotas.release_receive(frame.payload_bytes)
        source_key = (
            frame.context.session_id,
            frame.context.stream_id,
            frame.source,
            frame.context.capture_generation,
        )
        self.quotas.release_resident(frame.payload_bytes, source_key=source_key)
