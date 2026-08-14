"""Deterministic provider-effect and quota oracles for G2-A tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .model import ProtocolV2Violation


class EffectState(str, Enum):
    PREPARED = "prepared"
    INVOKING = "invoking"
    FORWARDED = "forwarded"
    AMBIGUOUS = "ambiguous"
    EFFECT_QUIESCENCE_REQUIRED = "effect_quiescence_required"
    TERMINAL = "terminal"


class DeletionState(str, Enum):
    ACTIVE = "active"
    DELETE_QUIESCING = "delete_quiescing"
    DELETING = "deleting"
    DELETED = "deleted"


@dataclass(frozen=True)
class EffectToken:
    runtime_epoch: int
    egress_fence: int
    owner_id: str
    effect_id: str


class ProviderEffectFence:
    """Single-use provider effect with epoch/fence and positive quiescence."""

    def __init__(self, effect_id: str, runtime_epoch: int = 0, egress_fence: int = 0) -> None:
        if not effect_id:
            raise ProtocolV2Violation("effect id is required")
        self.effect_id = effect_id
        self.runtime_epoch = runtime_epoch
        self.egress_fence = egress_fence
        self.state = EffectState.PREPARED
        self.owner_id: Optional[str] = None
        self.token: Optional[EffectToken] = None
        self.provider_close_ack = False
        self.owner_termination_ack = False
        self.invoke_count = 0

    def prepare(self, owner_id: str) -> EffectToken:
        if self.state is not EffectState.PREPARED:
            raise ProtocolV2Violation("effect is not prepareable")
        if not owner_id:
            raise ProtocolV2Violation("effect owner is required")
        self.owner_id = owner_id
        self.token = EffectToken(self.runtime_epoch, self.egress_fence, owner_id, self.effect_id)
        return self.token

    def invoke(self, token: EffectToken) -> None:
        self._check_token(token)
        if self.state is not EffectState.PREPARED:
            raise ProtocolV2Violation("effect invocation is not single-use")
        self.state = EffectState.INVOKING
        self.invoke_count += 1

    def provider_ack(self, token: EffectToken) -> None:
        self._check_token(token)
        if self.state is not EffectState.INVOKING:
            raise ProtocolV2Violation("provider ack is out of order")
        self.state = EffectState.FORWARDED

    def callback(self, token: EffectToken) -> None:
        self._check_token(token)
        if self.state in (EffectState.EFFECT_QUIESCENCE_REQUIRED, EffectState.TERMINAL):
            raise ProtocolV2Violation("late provider callback rejected")
        if self.state not in (EffectState.INVOKING, EffectState.FORWARDED):
            raise ProtocolV2Violation("provider callback is out of order")

    def mark_ambiguous(self, token: EffectToken) -> None:
        self._check_token(token)
        if self.state not in (EffectState.INVOKING, EffectState.FORWARDED):
            raise ProtocolV2Violation("only active effects can become ambiguous")
        self.state = EffectState.AMBIGUOUS

    def recovery_epoch(self, new_runtime_epoch: int, new_egress_fence: int) -> None:
        if new_runtime_epoch <= self.runtime_epoch or new_egress_fence <= self.egress_fence:
            raise ProtocolV2Violation("recovery epoch/fence must advance")
        self.runtime_epoch = new_runtime_epoch
        self.egress_fence = new_egress_fence
        if self.state in (EffectState.PREPARED, EffectState.INVOKING, EffectState.AMBIGUOUS, EffectState.FORWARDED):
            self.state = EffectState.EFFECT_QUIESCENCE_REQUIRED

    def acknowledge_provider_close(self) -> None:
        self.provider_close_ack = True
        self._finish_quiescence_if_ready()

    def acknowledge_owner_termination(self) -> None:
        self.owner_termination_ack = True
        self._finish_quiescence_if_ready()

    def terminalize(self) -> None:
        if self.state is not EffectState.EFFECT_QUIESCENCE_REQUIRED:
            raise ProtocolV2Violation("effect must be quiescence-required before terminalization")
        if not (self.provider_close_ack and self.owner_termination_ack):
            raise ProtocolV2Violation("positive provider and owner quiescence required")
        self.state = EffectState.TERMINAL

    def _finish_quiescence_if_ready(self) -> None:
        # Acknowledgements never silently restore PREPARED or INVOKING. The
        # owner must explicitly terminalize or create a new fenced effect.
        return None

    def _check_token(self, token: EffectToken) -> None:
        if token != self.token:
            raise ProtocolV2Violation("stale or foreign effect token")
        if token.runtime_epoch != self.runtime_epoch or token.egress_fence != self.egress_fence:
            raise ProtocolV2Violation("effect token is stale after recovery")


class DeletionFence:
    """Deletion sequencing oracle: fence first, then positive acknowledgements."""

    def __init__(self) -> None:
        self.state = DeletionState.ACTIVE
        self.generation = 0
        self.worker_acks: set[str] = set()
        self.callback_acks: set[str] = set()

    def request(self) -> int:
        if self.state is not DeletionState.ACTIVE:
            raise ProtocolV2Violation("deletion is already in progress")
        self.generation += 1
        self.state = DeletionState.DELETE_QUIESCING
        return self.generation

    def assert_admission_allowed(self) -> None:
        if self.state is not DeletionState.ACTIVE:
            raise ProtocolV2Violation("admission is fenced during deletion")

    def acknowledge_worker(self, worker_id: str, generation: int) -> None:
        self._ack(worker_id, generation)
        self.worker_acks.add(worker_id)

    def acknowledge_callback(self, callback_id: str, generation: int) -> None:
        self._ack(callback_id, generation)
        self.callback_acks.add(callback_id)

    def start_deleting(self, expected_workers: set[str], expected_callbacks: set[str]) -> None:
        if self.state is not DeletionState.DELETE_QUIESCING:
            raise ProtocolV2Violation("deletion requires delete_quiescing")
        if not expected_workers.issubset(self.worker_acks) or not expected_callbacks.issubset(self.callback_acks):
            raise ProtocolV2Violation("positive worker and callback quiescence required")
        self.state = DeletionState.DELETING

    def finish(self) -> None:
        if self.state is not DeletionState.DELETING:
            raise ProtocolV2Violation("deletion is not in deleting state")
        self.state = DeletionState.DELETED

    def _ack(self, identifier: str, generation: int) -> None:
        if not identifier or generation != self.generation or self.state is not DeletionState.DELETE_QUIESCING:
            raise ProtocolV2Violation("stale deletion acknowledgement")


@dataclass(frozen=True)
class QuotaLimits:
    event_rate: int
    event_burst: int
    payload_rate: int
    payload_burst: int
    metadata_rate: int
    metadata_burst: int
    custody_bytes: int


class TokenBucketQuota:
    """Integer token buckets; rejected attempts consume tokens."""

    def __init__(self, limits: QuotaLimits) -> None:
        for name in ("event_rate", "event_burst", "payload_rate", "payload_burst", "metadata_rate", "metadata_burst", "custody_bytes"):
            value = getattr(limits, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ProtocolV2Violation(f"{name} must be non-negative")
        self.limits = limits
        self.events = limits.event_burst
        self.payload = limits.payload_burst
        self.metadata = limits.metadata_burst
        self.custody = 0
        self.last_second = 0

    def refill(self, now_second: int) -> None:
        if now_second < self.last_second:
            raise ProtocolV2Violation("quota clock moved backwards")
        elapsed = now_second - self.last_second
        if elapsed:
            self.events = min(self.limits.event_burst, self.events + elapsed * self.limits.event_rate)
            self.payload = min(self.limits.payload_burst, self.payload + elapsed * self.limits.payload_rate)
            self.metadata = min(self.limits.metadata_burst, self.metadata + elapsed * self.limits.metadata_rate)
            self.last_second = now_second

    def reserve(self, now_second: int, *, events: int, payload_bytes: int, metadata_bytes: int, custody_bytes: int = 0) -> bool:
        self.refill(now_second)
        for value in (events, payload_bytes, metadata_bytes, custody_bytes):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ProtocolV2Violation("quota reservation values must be non-negative")
        # Attempts consume the requested event/byte budget even when another
        # dimension rejects; this prevents retry-burst amplification.
        allowed = (
            self.events >= events
            and self.payload >= payload_bytes
            and self.metadata >= metadata_bytes
            and self.custody + custody_bytes <= self.limits.custody_bytes
        )
        self.events = max(0, self.events - events)
        self.payload = max(0, self.payload - payload_bytes)
        self.metadata = max(0, self.metadata - metadata_bytes)
        if allowed:
            self.custody += custody_bytes
        return allowed

    def release_custody(self, bytes_count: int) -> None:
        if bytes_count < 0 or bytes_count > self.custody:
            raise ProtocolV2Violation("custody release exceeds reserved bytes")
        self.custody -= bytes_count
