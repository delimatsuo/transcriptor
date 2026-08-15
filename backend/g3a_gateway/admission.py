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
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._attempts: dict[str, int] = {}
        self._pending_handshakes = 0
        self._receive_bytes = 0
        self._resident_bytes = 0

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

    def reserve_resident(self, amount: int) -> None:
        if amount < 0 or self._resident_bytes + amount > self.limits.max_resident_bytes:
            raise GatewayError(FailureCode.QUOTA_EXCEEDED)
        self._resident_bytes += amount

    def release_resident(self, amount: int) -> None:
        self._resident_bytes = max(0, self._resident_bytes - max(0, amount))

    def burn_attempt(self, session_id: str) -> int:
        attempts = self._attempts.get(session_id, 0) + 1
        if attempts > self.limits.max_provider_attempts:
            raise GatewayError(FailureCode.QUOTA_EXCEEDED)
        self._attempts[session_id] = attempts
        return attempts

    def reserve_frame(self, frame: AudioFrame, now_ms: int) -> None:
        if frame.payload_bytes < 1 or frame.metadata_bytes < 0:
            raise GatewayError(FailureCode.CONFLICT)
        if frame.duration_ms <= 0 or frame.duration_ms > 2_000:
            raise GatewayError(FailureCode.CONFLICT)
        if frame.sample_rate not in (8_000, 16_000, 44_100, 48_000):
            raise GatewayError(FailureCode.CONFLICT)
        if frame.channels not in (1, 2):
            raise GatewayError(FailureCode.CONFLICT)
        bucket_key = (frame.context.session_id, frame.context.stream_id)
        second = now_ms // 1000
        bucket = self._buckets.get(bucket_key)
        if bucket is None or bucket.second != second:
            bucket = _Bucket(second)
            self._buckets[bucket_key] = bucket
        next_events = bucket.events + 1
        next_audio = bucket.audio_bytes + frame.payload_bytes
        next_metadata = bucket.metadata_bytes + frame.metadata_bytes
        if (
            next_events > self.limits.audio_event_burst
            or next_audio > self.limits.audio_bytes_burst
            or next_metadata > self.limits.metadata_bytes_burst
            or bucket.events >= self.limits.audio_events_per_second
            or bucket.audio_bytes >= self.limits.audio_bytes_per_second
            or bucket.metadata_bytes >= self.limits.metadata_bytes_per_second
        ):
            raise GatewayError(FailureCode.QUOTA_EXCEEDED)
        self.reserve_receive(frame.payload_bytes)
        self.reserve_resident(frame.payload_bytes)
        bucket.events, bucket.audio_bytes, bucket.metadata_bytes = (
            next_events,
            next_audio,
            next_metadata,
        )


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
        self.quotas.release_resident(frame.payload_bytes)
