"""Deterministic provider-effect and quota oracles for G2-A tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from .model import ProtocolV2Violation

_UINT64_MAX = (1 << 64) - 1


def _require_uint64(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _UINT64_MAX:
        raise ProtocolV2Violation(f"{name} must be an unsigned 64-bit integer")
    return value


def _require_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ProtocolV2Violation(f"{name} must be boolean")
    return value


class EffectState(str, Enum):
    PREPARED = "prepared"
    INVOKING = "invoking"
    PROVIDER_RETURNED = "provider_returned"
    JOURNALED = "journaled"
    AMBIGUOUS = "ambiguous"
    EFFECT_QUIESCENCE_REQUIRED = "effect_quiescence_required"
    TERMINAL = "terminal"


class DeletionState(str, Enum):
    ACTIVE = "active"
    DELETE_QUIESCING = "delete_quiescing"
    DELETING = "deleting"
    DELETED = "deleted"
    DELETION_FAILED = "deletion_failed"


@dataclass(frozen=True, eq=False)
class EffectToken:
    runtime_epoch: int
    egress_fence: int
    owner_id: str
    effect_id: str


@dataclass(frozen=True, eq=False)
class EffectQuiescenceToken:
    effect_id: str
    runtime_epoch: int
    egress_fence: int
    role: str
    actor_id: str


class ProviderEffectFence:
    """Single-use provider effect with epoch/fence and positive quiescence."""

    def __init__(self, effect_id: str, runtime_epoch: int = 0, egress_fence: int = 0) -> None:
        if not isinstance(effect_id, str) or not effect_id:
            raise ProtocolV2Violation("effect id is required")
        self.effect_id = effect_id
        self.runtime_epoch = _require_uint64(runtime_epoch, "runtime epoch")
        self.egress_fence = _require_uint64(egress_fence, "egress fence")
        self.state = EffectState.PREPARED
        self.owner_id: Optional[str] = None
        self.token: Optional[EffectToken] = None
        self.provider_close_ack = False
        self.owner_termination_ack = False
        self.invoke_count = 0
        self.journal_committed = False
        self.cancelled_without_invoke = False
        self.provider_quiescence_token: Optional[EffectQuiescenceToken] = None
        self.owner_quiescence_token: Optional[EffectQuiescenceToken] = None

    def prepare(self, owner_id: str) -> EffectToken:
        if self.state is not EffectState.PREPARED:
            raise ProtocolV2Violation("effect is not prepareable")
        if not isinstance(owner_id, str) or not owner_id:
            raise ProtocolV2Violation("effect owner is required")
        if self.owner_id is not None:
            if owner_id == self.owner_id and self.token is not None:
                return self.token
            raise ProtocolV2Violation("effect already has a durable owner")
        self.owner_id = owner_id
        self.token = EffectToken(self.runtime_epoch, self.egress_fence, owner_id, self.effect_id)
        return self.token

    def invoke(self, token: EffectToken) -> None:
        self._check_token(token)
        if self.state is not EffectState.PREPARED:
            raise ProtocolV2Violation("effect invocation is not single-use")
        self.state = EffectState.INVOKING
        self.invoke_count += 1

    def cancel_prepared(self, token: EffectToken) -> None:
        self._check_token(token)
        if self.state is not EffectState.PREPARED or self.invoke_count != 0 or self.journal_committed:
            raise ProtocolV2Violation("only an uninvoked prepared effect can be cancelled")
        self.cancelled_without_invoke = True
        self.state = EffectState.TERMINAL

    def provider_ack(self, token: EffectToken) -> None:
        self._check_token(token)
        if self.state is not EffectState.INVOKING:
            raise ProtocolV2Violation("provider ack is out of order")
        self.state = EffectState.PROVIDER_RETURNED

    def commit_journal(self, token: EffectToken) -> None:
        self._check_token(token)
        if self.state is not EffectState.PROVIDER_RETURNED:
            raise ProtocolV2Violation("forwarding journal is out of order")
        self.journal_committed = True
        self.state = EffectState.JOURNALED

    def callback(self, token: EffectToken) -> None:
        self._check_token(token)
        if self.state in (EffectState.EFFECT_QUIESCENCE_REQUIRED, EffectState.TERMINAL):
            raise ProtocolV2Violation("late provider callback rejected")
        if self.state not in (EffectState.INVOKING, EffectState.PROVIDER_RETURNED, EffectState.JOURNALED):
            raise ProtocolV2Violation("provider callback is out of order")

    def mark_ambiguous(self, token: EffectToken) -> None:
        self._check_token(token)
        if self.state not in (EffectState.INVOKING, EffectState.PROVIDER_RETURNED):
            raise ProtocolV2Violation("only active effects can become ambiguous")
        self.state = EffectState.AMBIGUOUS

    def recovery_epoch(
        self,
        new_runtime_epoch: int,
        new_egress_fence: int,
        *,
        provider_actor_id: str,
        owner_actor_id: str,
    ) -> tuple[EffectQuiescenceToken, EffectQuiescenceToken]:
        new_runtime_epoch = _require_uint64(new_runtime_epoch, "runtime epoch")
        new_egress_fence = _require_uint64(new_egress_fence, "egress fence")
        if (
            self.state is EffectState.TERMINAL
            or new_runtime_epoch <= self.runtime_epoch
            or new_egress_fence <= self.egress_fence
            or not isinstance(provider_actor_id, str)
            or not provider_actor_id
            or not isinstance(owner_actor_id, str)
            or owner_actor_id != self.owner_id
        ):
            raise ProtocolV2Violation("recovery epoch/fence must advance")
        self.runtime_epoch = new_runtime_epoch
        self.egress_fence = new_egress_fence
        self.provider_close_ack = False
        self.owner_termination_ack = False
        self.provider_quiescence_token = EffectQuiescenceToken(
            self.effect_id, new_runtime_epoch, new_egress_fence, "provider", provider_actor_id
        )
        self.owner_quiescence_token = EffectQuiescenceToken(
            self.effect_id, new_runtime_epoch, new_egress_fence, "owner", owner_actor_id
        )
        if self.state in (
            EffectState.PREPARED,
            EffectState.INVOKING,
            EffectState.PROVIDER_RETURNED,
            EffectState.JOURNALED,
            EffectState.AMBIGUOUS,
        ):
            self.state = EffectState.EFFECT_QUIESCENCE_REQUIRED
        return self.provider_quiescence_token, self.owner_quiescence_token

    def acknowledge_provider_close(self, token: EffectQuiescenceToken, *, actor_id: str) -> None:
        if (
            self.state is not EffectState.EFFECT_QUIESCENCE_REQUIRED
            or token is not self.provider_quiescence_token
            or actor_id != token.actor_id
        ):
            raise ProtocolV2Violation("provider close must acknowledge the current recovery fence")
        self.provider_close_ack = True
        self._finish_quiescence_if_ready()

    def acknowledge_owner_termination(self, token: EffectQuiescenceToken, *, actor_id: str) -> None:
        if (
            self.state is not EffectState.EFFECT_QUIESCENCE_REQUIRED
            or token is not self.owner_quiescence_token
            or actor_id != token.actor_id
        ):
            raise ProtocolV2Violation("owner termination must acknowledge the current recovery fence")
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
        if token is not self.token:
            raise ProtocolV2Violation("stale or foreign effect token")
        if token.runtime_epoch != self.runtime_epoch or token.egress_fence != self.egress_fence:
            raise ProtocolV2Violation("effect token is stale after recovery")

    @property
    def forwarded(self) -> bool:
        return self.journal_committed

    def snapshot(self) -> dict[str, Any]:
        return {
            "effect_id": self.effect_id,
            "runtime_epoch": self.runtime_epoch,
            "egress_fence": self.egress_fence,
            "state": self.state.value,
            "owner_id": self.owner_id,
            "token": None if self.token is None else {
                "runtime_epoch": self.token.runtime_epoch,
                "egress_fence": self.token.egress_fence,
                "owner_id": self.token.owner_id,
                "effect_id": self.token.effect_id,
            },
            "provider_close_ack": self.provider_close_ack,
            "owner_termination_ack": self.owner_termination_ack,
            "invoke_count": self.invoke_count,
            "journal_committed": self.journal_committed,
            "cancelled_without_invoke": self.cancelled_without_invoke,
        }

    @classmethod
    def restore(cls, snapshot: Mapping[str, Any]) -> "ProviderEffectFence":
        if not isinstance(snapshot, Mapping):
            raise ProtocolV2Violation("effect snapshot must be a mapping")
        restored = cls(
            snapshot["effect_id"],
            _require_uint64(snapshot["runtime_epoch"], "snapshot runtime epoch"),
            _require_uint64(snapshot["egress_fence"], "snapshot egress fence"),
        )
        if not isinstance(snapshot["state"], str):
            raise ProtocolV2Violation("effect snapshot state must be a string")
        restored.state = EffectState(snapshot["state"])
        owner_id = snapshot.get("owner_id")
        if owner_id is not None and (not isinstance(owner_id, str) or not owner_id):
            raise ProtocolV2Violation("effect snapshot owner is invalid")
        restored.owner_id = owner_id
        raw_token = snapshot.get("token")
        if raw_token is not None:
            if not isinstance(raw_token, Mapping):
                raise ProtocolV2Violation("effect snapshot token is invalid")
            token_owner = raw_token["owner_id"]
            token_effect = raw_token["effect_id"]
            if token_owner != owner_id or token_effect != restored.effect_id:
                raise ProtocolV2Violation("effect snapshot token binding is invalid")
            restored.token = EffectToken(
                _require_uint64(raw_token["runtime_epoch"], "token runtime epoch"),
                _require_uint64(raw_token["egress_fence"], "token egress fence"),
                token_owner,
                token_effect,
            )
        restored.provider_close_ack = _require_bool(snapshot["provider_close_ack"], "provider close ack")
        restored.owner_termination_ack = _require_bool(snapshot["owner_termination_ack"], "owner termination ack")
        restored.invoke_count = _require_uint64(snapshot["invoke_count"], "invoke count")
        restored.journal_committed = _require_bool(snapshot["journal_committed"], "journal committed")
        restored.cancelled_without_invoke = _require_bool(
            snapshot.get("cancelled_without_invoke", False), "cancelled without invoke"
        )
        if restored.state is EffectState.EFFECT_QUIESCENCE_REQUIRED:
            restored.provider_close_ack = False
            restored.owner_termination_ack = False
        elif restored.state is not EffectState.TERMINAL and (
            restored.provider_close_ack or restored.owner_termination_ack
        ):
            raise ProtocolV2Violation("active effect snapshot contains unauthenticated quiescence")
        return restored


class DeletionFence:
    """Deletion sequencing oracle with resumable two-pass absence proof."""

    def __init__(
        self,
        *,
        workers: set[str] = frozenset(),
        callbacks: set[str] = frozenset(),
        connections: set[str] = frozenset(),
        effects: set[str] = frozenset(),
        stores: set[str] = frozenset(),
    ) -> None:
        self.state = DeletionState.ACTIVE
        self.generation = 0
        self.workers = self._identifiers(workers)
        self.callbacks = self._identifiers(callbacks)
        self.connections = self._identifiers(connections)
        self.effects = self._identifiers(effects)
        self.stores = self._identifiers(stores)
        self.worker_acks: set[str] = set()
        self.callback_acks: set[str] = set()
        self.connection_acks: set[str] = set()
        self.effect_acks: set[str] = set()
        self.absence_passes: dict[int, dict[str, bool]] = {}
        self.failed_pass: Optional[int] = None
        self.late_callback_rejections = 0

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
        if worker_id not in self.workers:
            raise ProtocolV2Violation("worker is not registered for deletion")
        self._ack(worker_id, generation)
        self.worker_acks.add(worker_id)

    def acknowledge_callback(self, callback_id: str, generation: int) -> None:
        if callback_id not in self.callbacks:
            raise ProtocolV2Violation("callback lane is not registered for deletion")
        self._ack(callback_id, generation)
        self.callback_acks.add(callback_id)

    def acknowledge_connection(self, connection_id: str, generation: int) -> None:
        if connection_id not in self.connections:
            raise ProtocolV2Violation("connection is not registered for deletion")
        self._ack(connection_id, generation)
        self.connection_acks.add(connection_id)

    def acknowledge_effect(self, effect_id: str, generation: int) -> None:
        if effect_id not in self.effects:
            raise ProtocolV2Violation("effect is not registered for deletion")
        self._ack(effect_id, generation)
        self.effect_acks.add(effect_id)

    def start_deleting(
        self,
        expected_workers: Optional[set[str]] = None,
        expected_callbacks: Optional[set[str]] = None,
        expected_connections: Optional[set[str]] = None,
        expected_effects: Optional[set[str]] = None,
    ) -> None:
        if self.state is not DeletionState.DELETE_QUIESCING:
            raise ProtocolV2Violation("deletion requires delete_quiescing")
        requested = (
            (expected_workers, self.workers),
            (expected_callbacks, self.callbacks),
            (expected_connections, self.connections),
            (expected_effects, self.effects),
        )
        if any(expected is not None and self._identifiers(expected) != registered for expected, registered in requested):
            raise ProtocolV2Violation("deletion participant set cannot change after request")
        if (
            not self.workers.issubset(self.worker_acks)
            or not self.callbacks.issubset(self.callback_acks)
            or not self.connections.issubset(self.connection_acks)
            or not self.effects.issubset(self.effect_acks)
        ):
            raise ProtocolV2Violation("positive worker, callback, connection, and effect quiescence required")
        self.state = DeletionState.DELETING

    def record_absence_pass(self, pass_number: int, results: Mapping[str, bool]) -> bool:
        if self.state is not DeletionState.DELETING:
            raise ProtocolV2Violation("absence inventory requires deleting state")
        if pass_number not in (1, 2) or (pass_number == 2 and 1 not in self.absence_passes):
            raise ProtocolV2Violation("absence inventories must run in order")
        if set(results) != self.stores or any(not isinstance(value, bool) for value in results.values()):
            raise ProtocolV2Violation("absence inventory store set is not exact")
        normalized = dict(sorted(results.items()))
        existing = self.absence_passes.get(pass_number)
        if existing is not None:
            if existing != normalized:
                raise ProtocolV2Violation("absence inventory replay conflicts")
            return True
        if not all(normalized.values()):
            self.failed_pass = pass_number
            self.state = DeletionState.DELETION_FAILED
            return False
        self.absence_passes[pass_number] = normalized
        self.failed_pass = None
        return True

    def resume(self, generation: int) -> None:
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation != self.generation
            or self.state is not DeletionState.DELETION_FAILED
        ):
            raise ProtocolV2Violation("deletion resume is stale or out of order")
        self.state = DeletionState.DELETING

    def reject_late_callback(self, generation: int) -> None:
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 0
            or generation > self.generation
            or self.state is DeletionState.ACTIVE
        ):
            raise ProtocolV2Violation("callback generation is not fenced")
        self.late_callback_rejections += 1
        raise ProtocolV2Violation("late callback rejected before content persistence")

    def finish(self) -> None:
        if self.state is not DeletionState.DELETING:
            raise ProtocolV2Violation("deletion is not in deleting state")
        if set(self.absence_passes) != {1, 2}:
            raise ProtocolV2Violation("two independent absence passes are required")
        self.state = DeletionState.DELETED

    def _ack(self, identifier: str, generation: int) -> None:
        if (
            not identifier
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation != self.generation
            or self.state is not DeletionState.DELETE_QUIESCING
        ):
            raise ProtocolV2Violation("stale deletion acknowledgement")

    @staticmethod
    def _identifiers(values: set[str]) -> set[str]:
        if any(not isinstance(value, str) or not value for value in values):
            raise ProtocolV2Violation("deletion participant identifier is invalid")
        return set(values)

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "generation": self.generation,
            "workers": sorted(self.workers),
            "callbacks": sorted(self.callbacks),
            "connections": sorted(self.connections),
            "effects": sorted(self.effects),
            "stores": sorted(self.stores),
            "worker_acks": sorted(self.worker_acks),
            "callback_acks": sorted(self.callback_acks),
            "connection_acks": sorted(self.connection_acks),
            "effect_acks": sorted(self.effect_acks),
            "absence_passes": {
                str(number): dict(results) for number, results in self.absence_passes.items()
            },
            "failed_pass": self.failed_pass,
            "late_callback_rejections": self.late_callback_rejections,
        }

    @classmethod
    def restore(cls, snapshot: Mapping[str, Any]) -> "DeletionFence":
        if not isinstance(snapshot, Mapping):
            raise ProtocolV2Violation("deletion snapshot must be a mapping")
        restored = cls(
            workers=set(snapshot["workers"]),
            callbacks=set(snapshot["callbacks"]),
            connections=set(snapshot["connections"]),
            effects=set(snapshot["effects"]),
            stores=set(snapshot["stores"]),
        )
        if not isinstance(snapshot["state"], str):
            raise ProtocolV2Violation("deletion snapshot state must be a string")
        restored.state = DeletionState(snapshot["state"])
        restored.generation = _require_uint64(snapshot["generation"], "deletion generation")
        restored.worker_acks = cls._identifiers(set(snapshot["worker_acks"]))
        restored.callback_acks = cls._identifiers(set(snapshot["callback_acks"]))
        restored.connection_acks = cls._identifiers(set(snapshot["connection_acks"]))
        restored.effect_acks = cls._identifiers(set(snapshot["effect_acks"]))
        if (
            not restored.worker_acks.issubset(restored.workers)
            or not restored.callback_acks.issubset(restored.callbacks)
            or not restored.connection_acks.issubset(restored.connections)
            or not restored.effect_acks.issubset(restored.effects)
        ):
            raise ProtocolV2Violation("deletion snapshot contains foreign acknowledgement")
        raw_passes = snapshot["absence_passes"]
        if not isinstance(raw_passes, Mapping):
            raise ProtocolV2Violation("deletion absence passes are invalid")
        restored.absence_passes = {}
        for number, results in raw_passes.items():
            if number not in ("1", "2") or not isinstance(results, Mapping):
                raise ProtocolV2Violation("deletion absence pass identity is invalid")
            if set(results) != restored.stores or any(not isinstance(value, bool) for value in results.values()):
                raise ProtocolV2Violation("deletion absence pass result is invalid")
            restored.absence_passes[int(number)] = dict(results)
        failed_pass = snapshot.get("failed_pass")
        if failed_pass is not None and (isinstance(failed_pass, bool) or failed_pass not in (1, 2)):
            raise ProtocolV2Violation("deletion failed pass is invalid")
        restored.failed_pass = failed_pass
        restored.late_callback_rejections = _require_uint64(
            snapshot["late_callback_rejections"], "late callback rejection count"
        )
        return restored


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
        if isinstance(now_second, bool) or not isinstance(now_second, int):
            raise ProtocolV2Violation("quota clock must be an integer")
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
        if isinstance(bytes_count, bool) or not isinstance(bytes_count, int) or bytes_count < 0 or bytes_count > self.custody:
            raise ProtocolV2Violation("custody release exceeds reserved bytes")
        self.custody -= bytes_count


class AdmissionRejected(ProtocolV2Violation):
    """Stable non-enumerating rejection for every admission mismatch."""

    public_code = "capture_not_authorized"


@dataclass(frozen=True)
class AdmissionAuthority:
    audience: str
    tenant_id: str
    actor_id: str
    enrollment_id: str
    session_id: str
    stream_id: str
    capture_generation: int
    fence: int
    protocol_version: int
    notice_version: str
    legal_basis: str
    expires_at_ms: int
    authenticated: bool = True
    revoked: bool = False

    def authorize(self, request: Mapping[str, Any], now_ms: int) -> None:
        expected = {
            "audience": self.audience,
            "tenantId": self.tenant_id,
            "actorId": self.actor_id,
            "enrollmentId": self.enrollment_id,
            "sessionId": self.session_id,
            "streamId": self.stream_id,
            "captureGeneration": self.capture_generation,
            "fence": self.fence,
            "protocolVersion": self.protocol_version,
            "noticeVersion": self.notice_version,
            "legalBasis": self.legal_basis,
        }
        if (
            not self.authenticated
            or self.revoked
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in (
                    self.capture_generation,
                    self.fence,
                    self.protocol_version,
                    self.expires_at_ms,
                    now_ms,
                )
            )
            or any(
                isinstance(request.get(name), bool) or not isinstance(request.get(name), int)
                for name in ("captureGeneration", "fence", "protocolVersion")
            )
            or now_ms < 0
            or now_ms >= self.expires_at_ms
            or set(request) != set(expected)
            or any(request[name] != value for name, value in expected.items())
        ):
            raise AdmissionRejected(AdmissionRejected.public_code)


@dataclass
class _RetainedAudio:
    payload: bytearray
    frames: int
    metadata_bytes: int
    resident_bytes: int
    captured_at_ms: int


class RawCustodyBuffer:
    """Two-second generated-byte custody oracle with absolute privacy expiry."""

    def __init__(self, sample_rate_hertz: int, channel_count: int) -> None:
        if (
            isinstance(sample_rate_hertz, bool)
            or not isinstance(sample_rate_hertz, int)
            or isinstance(channel_count, bool)
            or not isinstance(channel_count, int)
            or not 8_000 <= sample_rate_hertz <= 48_000
            or channel_count not in (1, 2)
        ):
            raise ProtocolV2Violation("custody format is outside v2 bounds")
        self.sample_rate_hertz = sample_rate_hertz
        self.channel_count = channel_count
        self.items: dict[str, _RetainedAudio] = {}
        self.released: dict[str, str] = {}
        self.forwarded: set[str] = set()
        self.gap_obligations: dict[str, str] = {}
        self.effects: dict[str, ProviderEffectFence] = {}
        self.effect_pending_releases: set[str] = set()
        self.acquisition_stopped = False
        self.last_clock_ms = 0

    @property
    def retained_frames(self) -> int:
        return sum(item.frames for item in self.items.values())

    @property
    def retained_payload_bytes(self) -> int:
        return sum(len(item.payload) for item in self.items.values())

    @property
    def retained_metadata_bytes(self) -> int:
        return sum(item.metadata_bytes for item in self.items.values())

    @property
    def retained_resident_bytes(self) -> int:
        return sum(item.resident_bytes for item in self.items.values())

    def reserve(
        self,
        event_id: str,
        payload: bytes,
        *,
        frames: int,
        metadata_bytes: int,
        resident_overhead_bytes: int,
        captured_at_ms: int,
    ) -> bool:
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (frames, metadata_bytes, resident_overhead_bytes, captured_at_ms)
        ):
            raise ProtocolV2Violation("custody reservation numeric input is invalid")
        if event_id in self.items:
            existing = self.items[event_id]
            if bytes(existing.payload) != payload or existing.frames != frames or existing.metadata_bytes != metadata_bytes:
                raise ProtocolV2Violation("custody retry changed content")
            return False
        if event_id in self.released:
            raise ProtocolV2Violation("released event cannot regain custody")
        if self.acquisition_stopped:
            raise ProtocolV2Violation("custody acquisition is stopped")
        if (
            not event_id
            or not isinstance(payload, bytes)
            or frames <= 0
            or len(payload) != frames * self.channel_count * 2
            or len(payload) > 64_000
            or not 20 <= (frames * 1_000 // self.sample_rate_hertz) <= 250
            or frames * 1_000 % self.sample_rate_hertz != 0
            or not 0 < metadata_bytes <= 4_096
            or resident_overhead_bytes < 0
            or captured_at_ms < self.last_clock_ms
        ):
            raise ProtocolV2Violation("custody reservation is invalid")
        resident = len(payload) + metadata_bytes + resident_overhead_bytes
        if (
            len(self.items) + 1 > 100
            or self.retained_frames + frames > min(96_000, 2 * self.sample_rate_hertz)
            or self.retained_payload_bytes + len(payload) > min(
                384_000,
                min(96_000, 2 * self.sample_rate_hertz) * self.channel_count * 2,
            )
            or self.retained_metadata_bytes + metadata_bytes > 409_600
            or self.retained_resident_bytes + resident > 1_048_576
        ):
            self.acquisition_stopped = True
            raise ProtocolV2Violation("custody reservation exceeds a frozen bound")
        self.items[event_id] = _RetainedAudio(
            bytearray(payload), frames, metadata_bytes, resident, captured_at_ms
        )
        self.last_clock_ms = captured_at_ms
        return True

    def acknowledge_forwarded(self, event_id: str, *, journal_committed: bool) -> None:
        if event_id in self.effects:
            raise ProtocolV2Violation("registered provider effect requires effect-bound forwarding")
        if not journal_committed:
            raise ProtocolV2Violation("forwarding release requires immutable journal")
        self._release(event_id, "forwarded")
        self.forwarded.add(event_id)

    def acknowledge_effect_forwarded(self, event_id: str, effect: ProviderEffectFence) -> None:
        if self.effects.get(event_id) is not effect or not effect.forwarded:
            raise ProtocolV2Violation("effect-bound forwarding requires the original immutable journal")
        if event_id in self.effect_pending_releases:
            raise ProtocolV2Violation("locally released custody requires pending-effect resolution")
        self._release(event_id, "forwarded")
        self.forwarded.add(event_id)

    def acknowledge_durable_discard(self, event_id: str, gap_id: str) -> None:
        if event_id in self.forwarded or event_id in self.effects or event_id in self.effect_pending_releases or not gap_id:
            raise ProtocolV2Violation("durable discard conflicts with forwarding or gap identity")
        existing_gap = self.gap_obligations.get(event_id)
        if existing_gap is not None and existing_gap != gap_id:
            raise ProtocolV2Violation("durable discard identity replay conflicts")
        self._release(event_id, "durable_discard")
        self.gap_obligations[event_id] = gap_id

    def cancel_prepared_effect_and_discard(
        self,
        event_id: str,
        effect: ProviderEffectFence,
        gap_id: str,
    ) -> None:
        if (
            self.released.get(event_id) == "durable_discard"
            and self.gap_obligations.get(event_id) == gap_id
            and self.effects.get(event_id) is effect
            and effect.cancelled_without_invoke
        ):
            return
        if (
            not gap_id
            or event_id not in self.items
            or self.effects.get(event_id) is not effect
            or event_id in self.effect_pending_releases
            or effect.token is None
        ):
            raise ProtocolV2Violation("prepared-effect discard is stale, active, or foreign")
        effect.cancel_prepared(effect.token)
        self._release(event_id, "durable_discard")
        self.gap_obligations[event_id] = gap_id

    def local_privacy_release(self, event_id: str, reason: str) -> None:
        if reason not in ("privacy_timeout_local", "deletion_local", "emergency_local"):
            raise ProtocolV2Violation("local privacy release reason is invalid")
        if event_id in self.forwarded:
            raise ProtocolV2Violation("forwarded audio cannot become a local privacy discard")
        self._release(event_id, reason)
        if event_id in self.effects:
            self.effect_pending_releases.add(event_id)
        else:
            self.gap_obligations[event_id] = reason

    def register_effect(self, event_id: str, effect: ProviderEffectFence) -> None:
        if event_id not in self.items or event_id in self.released or event_id in self.gap_obligations:
            raise ProtocolV2Violation("provider effect requires live unreleased custody")
        if effect.state is not EffectState.PREPARED or effect.token is None or effect.owner_id is None:
            raise ProtocolV2Violation("provider effect must have a durable owner before range registration")
        existing = self.effects.get(event_id)
        if existing is not None:
            if existing is effect:
                return
            raise ProtocolV2Violation("range already has a different provider effect")
        self.effects[event_id] = effect

    def invoke_effect(self, event_id: str, effect: ProviderEffectFence, token: EffectToken) -> None:
        if event_id not in self.items or self.effects.get(event_id) is not effect:
            raise ProtocolV2Violation("provider invocation requires registered live custody")
        effect.invoke(token)

    def resolve_pending_effect(self, event_id: str, effect: ProviderEffectFence, outcome: str) -> None:
        if event_id not in self.effect_pending_releases or self.effects.get(event_id) is not effect:
            raise ProtocolV2Violation("pending effect resolution is stale or foreign")
        if outcome == "forwarded":
            if not effect.forwarded:
                raise ProtocolV2Violation("forwarded resolution requires immutable journal")
            self.forwarded.add(event_id)
            self.released[event_id] = "forwarded_after_local_release"
        elif outcome == "ambiguous_effect":
            if effect.state is not EffectState.TERMINAL or effect.forwarded:
                raise ProtocolV2Violation("ambiguous resolution requires unforwarded positive quiescence")
            self.gap_obligations[event_id] = "ambiguous_effect"
        else:
            raise ProtocolV2Violation("pending effect cannot resolve as discard")
        self.effect_pending_releases.remove(event_id)

    def advance_clock(self, now_ms: int, *, clock_certain: bool = True) -> None:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or not isinstance(clock_certain, bool):
            raise ProtocolV2Violation("custody clock input is invalid")
        if now_ms < self.last_clock_ms:
            raise ProtocolV2Violation("custody clock moved backwards")
        self.last_clock_ms = now_ms
        if not clock_certain:
            self.acquisition_stopped = True
            for event_id in tuple(self.items):
                self.local_privacy_release(event_id, "privacy_timeout_local")
            return
        if any(now_ms - item.captured_at_ms >= 10_000 for item in self.items.values()):
            self.acquisition_stopped = True
        for event_id, item in tuple(self.items.items()):
            if now_ms - item.captured_at_ms >= 30_000:
                self.local_privacy_release(event_id, "privacy_timeout_local")

    def read(self, event_id: str, now_ms: int, *, clock_certain: bool = True) -> bytes:
        self.advance_clock(now_ms, clock_certain=clock_certain)
        item = self.items.get(event_id)
        if item is None:
            raise ProtocolV2Violation("audio is not in custody")
        return bytes(item.payload)

    def _release(self, event_id: str, outcome: str) -> None:
        item = self.items.pop(event_id, None)
        if item is None:
            if self.released.get(event_id) == outcome:
                return
            raise ProtocolV2Violation("release references absent or conflicting custody")
        item.payload[:] = b"\0" * len(item.payload)
        self.released[event_id] = outcome


@dataclass(frozen=True)
class ScopeQuotaLimits:
    event_rate: int
    event_burst: int
    payload_rate: int
    payload_burst: int
    metadata_rate: int
    metadata_burst: int
    custody_bytes: int
    resident_bytes: int
    active_sessions: int
    writable_attempts: int
    draining_attempts: int

    def __post_init__(self) -> None:
        for value in (
            self.event_rate,
            self.event_burst,
            self.payload_rate,
            self.payload_burst,
            self.metadata_rate,
            self.metadata_burst,
            self.custody_bytes,
            self.resident_bytes,
            self.active_sessions,
            self.writable_attempts,
            self.draining_attempts,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ProtocolV2Violation("scope quota limit is invalid")


@dataclass
class _ScopeQuotaState:
    limits: ScopeQuotaLimits
    events: int
    payload: int
    metadata: int
    custody: int = 0
    resident: int = 0
    sessions: int = 0
    writable: int = 0
    draining: int = 0
    last_second: int = 0


class HierarchicalIngressQuota:
    """Atomically enforces source/session/tenant/process rows before mutation."""

    def __init__(self, limits: Mapping[str, ScopeQuotaLimits]) -> None:
        if set(limits) != {"source", "session", "tenant", "process"}:
            raise ProtocolV2Violation("all quota scopes are required")
        self.scopes = {
            name: _ScopeQuotaState(value, value.event_burst, value.payload_burst, value.metadata_burst)
            for name, value in limits.items()
        }

    def reserve(
        self,
        now_second: int,
        *,
        events: int,
        payload_bytes: int,
        metadata_bytes: int,
        custody_bytes: int,
        resident_bytes: int,
        active_sessions: int = 0,
        writable_attempts: int = 0,
        draining_attempts: int = 0,
    ) -> bool:
        requested = (
            events,
            payload_bytes,
            metadata_bytes,
            custody_bytes,
            resident_bytes,
            active_sessions,
            writable_attempts,
            draining_attempts,
        )
        if (
            isinstance(now_second, bool)
            or not isinstance(now_second, int)
            or now_second < 0
            or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in requested)
        ):
            raise ProtocolV2Violation("hierarchical quota request is invalid")
        for scope in self.scopes.values():
            if now_second < scope.last_second:
                raise ProtocolV2Violation("hierarchical quota clock moved backwards")
            elapsed = now_second - scope.last_second
            if elapsed:
                scope.events = min(scope.limits.event_burst, scope.events + elapsed * scope.limits.event_rate)
                scope.payload = min(scope.limits.payload_burst, scope.payload + elapsed * scope.limits.payload_rate)
                scope.metadata = min(scope.limits.metadata_burst, scope.metadata + elapsed * scope.limits.metadata_rate)
                scope.last_second = now_second
        allowed = all(
            scope.events >= events
            and scope.payload >= payload_bytes
            and scope.metadata >= metadata_bytes
            and scope.custody + custody_bytes <= scope.limits.custody_bytes
            and scope.resident + resident_bytes <= scope.limits.resident_bytes
            and scope.sessions + active_sessions <= scope.limits.active_sessions
            and scope.writable + writable_attempts <= scope.limits.writable_attempts
            and scope.draining + draining_attempts <= scope.limits.draining_attempts
            for scope in self.scopes.values()
        )
        for scope in self.scopes.values():
            scope.events = max(0, scope.events - events)
            scope.payload = max(0, scope.payload - payload_bytes)
            scope.metadata = max(0, scope.metadata - metadata_bytes)
            if allowed:
                scope.custody += custody_bytes
                scope.resident += resident_bytes
                scope.sessions += active_sessions
                scope.writable += writable_attempts
                scope.draining += draining_attempts
        return allowed

    def release(
        self,
        *,
        custody_bytes: int = 0,
        resident_bytes: int = 0,
        active_sessions: int = 0,
        writable_attempts: int = 0,
        draining_attempts: int = 0,
    ) -> None:
        values = (custody_bytes, resident_bytes, active_sessions, writable_attempts, draining_attempts)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ProtocolV2Violation("hierarchical quota release is invalid")
        for scope in self.scopes.values():
            if (
                custody_bytes > scope.custody
                or resident_bytes > scope.resident
                or active_sessions > scope.sessions
                or writable_attempts > scope.writable
                or draining_attempts > scope.draining
            ):
                raise ProtocolV2Violation("hierarchical quota release exceeds reservation")
        for scope in self.scopes.values():
            scope.custody -= custody_bytes
            scope.resident -= resident_bytes
            scope.sessions -= active_sessions
            scope.writable -= writable_attempts
            scope.draining -= draining_attempts


class TransportEdgeBudget:
    """Bounded pre-auth and authenticated streaming-parser allocation oracle."""

    def __init__(self) -> None:
        self.pending: dict[str, tuple[str, int, int]] = {}
        self.authenticated: set[str] = set()

    @property
    def pending_bytes(self) -> int:
        return sum(item[2] for item in self.pending.values())

    def open_pending(
        self,
        connection_id: str,
        source_ip: str,
        now_ms: int,
        *,
        header_bytes: int,
        first_auth_bytes: int,
        receive_buffer_bytes: int,
    ) -> None:
        if connection_id in self.pending or connection_id in self.authenticated:
            raise ProtocolV2Violation("connection identity already exists")
        if (
            not connection_id
            or not source_ip
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in (now_ms, header_bytes, first_auth_bytes, receive_buffer_bytes)
            )
            or now_ms < 0
            or not 0 <= header_bytes <= 16_384
            or not 0 <= first_auth_bytes <= 8_192
            or not 0 <= receive_buffer_bytes <= 32_768
            or len(self.pending) >= 64
            or sum(1 for value in self.pending.values() if value[0] == source_ip) >= 16
            or self.pending_bytes + receive_buffer_bytes > 2_097_152
        ):
            raise ProtocolV2Violation("pending transport bound exceeded")
        self.pending[connection_id] = (source_ip, now_ms, receive_buffer_bytes)

    def reject_pre_auth_audio(self, declared_frame_bytes: int) -> None:
        if isinstance(declared_frame_bytes, bool) or not isinstance(declared_frame_bytes, int) or declared_frame_bytes < 0:
            raise ProtocolV2Violation("declared frame length is invalid")
        raise ProtocolV2Violation("binary audio is rejected before authentication")

    def authenticate(self, connection_id: str, now_ms: int) -> None:
        pending = self.pending.get(connection_id)
        if (
            pending is None
            or isinstance(now_ms, bool)
            or not isinstance(now_ms, int)
            or now_ms < pending[1]
            or now_ms - pending[1] > 8_000
            or len(self.authenticated) >= 16
        ):
            raise ProtocolV2Violation("authentication deadline or connection bound exceeded")
        del self.pending[connection_id]
        self.authenticated.add(connection_id)

    @property
    def authenticated_parser_bytes(self) -> int:
        return len(self.authenticated) * 68_100


class PhysicalCaptureState(str, Enum):
    SETUP_REQUIRED = "setup_required"
    CHECKING_PERMISSIONS_AND_DEVICES = "checking_permissions_and_devices"
    READY_BOTH_SOURCES = "ready_both_sources"
    STARTING = "starting"
    RECORDING = "recording"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"


class TransportState(str, Enum):
    DISCONNECTED = "disconnected"
    ADMITTING = "admitting"
    FORWARDING = "forwarding"
    DRAINING = "draining"
    FENCED = "fenced"
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


class DerivedDisplayState(str, Enum):
    RECORDING = "recording"
    DEGRADED = "degraded"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    COMPLETED_WITH_GAPS = "completed_with_gaps"
    DELETING = "deleting"
    DELETED = "deleted"
    DELETION_FAILED = "deletion_failed"


class LifecycleProjection:
    """Origin-separated monotonic axes with a conservative display reducer."""

    def __init__(self) -> None:
        self.physical: Optional[tuple[int, PhysicalCaptureState]] = None
        self.transport: Optional[tuple[int, TransportState]] = None
        self.coverage: Optional[tuple[int, CoverageState]] = None

    def companion(self, version: int, state: PhysicalCaptureState) -> None:
        self.physical = self._advance(self.physical, version, state)

    def gateway_transport(self, version: int, state: TransportState) -> None:
        self.transport = self._advance(self.transport, version, state)

    def gateway_coverage(self, version: int, state: CoverageState) -> None:
        self.coverage = self._advance(self.coverage, version, state)

    @staticmethod
    def _advance(current: Optional[tuple[int, Any]], version: int, state: Any) -> tuple[int, Any]:
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version < 0
            or (current is not None and version <= current[0])
        ):
            raise ProtocolV2Violation("origin state version is stale")
        return version, state

    def derived(self) -> DerivedDisplayState:
        physical = None if self.physical is None else self.physical[1]
        transport = None if self.transport is None else self.transport[1]
        coverage = None if self.coverage is None else self.coverage[1]
        if coverage is CoverageState.DELETED:
            return DerivedDisplayState.DELETED
        if coverage is CoverageState.DELETION_FAILED:
            return DerivedDisplayState.DELETION_FAILED
        if coverage in (CoverageState.DELETE_QUIESCING, CoverageState.DELETING):
            return DerivedDisplayState.DELETING
        if coverage in (CoverageState.COMPLETED, CoverageState.COMPLETED_WITH_GAPS):
            if physical is PhysicalCaptureState.STOPPED and transport is TransportState.CLOSED:
                return (
                    DerivedDisplayState.COMPLETED
                    if coverage is CoverageState.COMPLETED
                    else DerivedDisplayState.COMPLETED_WITH_GAPS
                )
            return DerivedDisplayState.FINALIZING
        if coverage is CoverageState.FINALIZING or transport is TransportState.DRAINING:
            return DerivedDisplayState.FINALIZING
        if (
            physical is PhysicalCaptureState.RECORDING
            and transport is TransportState.FORWARDING
            and coverage is CoverageState.OPEN
        ):
            return DerivedDisplayState.RECORDING
        return DerivedDisplayState.DEGRADED

    def assert_upgrade_allowed(self) -> None:
        if (
            self.physical is None
            or self.transport is None
            or self.coverage is None
            or self.physical[1] is not PhysicalCaptureState.STOPPED
            or self.transport[1] is not TransportState.CLOSED
            or self.coverage[1] not in (CoverageState.COMPLETED, CoverageState.COMPLETED_WITH_GAPS)
        ):
            raise ProtocolV2Violation("upgrade requires stopped, closed, terminal coverage")
