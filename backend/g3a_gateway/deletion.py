"""Generation-fenced deletion and positive quiescence coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .contracts import CoverageState, FailureCode, GatewayError


class DeletionState(str, Enum):
    ACTIVE = "active"
    DELETE_QUIESCING = "delete_quiescing"
    DELETING = "deleting"
    DELETED = "deleted"
    DELETION_FAILED = "deletion_failed"
    EFFECT_QUIESCENCE_REQUIRED = "effect_quiescence_required"


@dataclass(frozen=True)
class DeletionSnapshot:
    generation: int
    state: DeletionState
    admission_fenced: bool
    missing_workers: int
    missing_effects: int
    absence_passes: int


class DeletionCoordinator:
    def __init__(self, inventory: set[str] | None = None) -> None:
        self.generation = 0
        self.state = DeletionState.ACTIVE
        self.admission_fenced = False
        self._workers: dict[str, bool] = {}
        self._effects: dict[str, bool] = {}
        self._effect_quiesced: dict[str, bool] = {}
        self._inventory = set(inventory or ())
        self._absence_passes = 0

    def register_worker(self, worker_id: str) -> None:
        if self.state is not DeletionState.ACTIVE:
            raise GatewayError(FailureCode.QUIESCENCE_REQUIRED)
        self._workers[worker_id] = False

    def register_effect(self, effect_id: str) -> None:
        if self.state is not DeletionState.ACTIVE:
            raise GatewayError(FailureCode.QUIESCENCE_REQUIRED)
        self._effects[effect_id] = False
        self._effect_quiesced[effect_id] = False

    def request(self) -> int:
        if self.state is DeletionState.DELETED:
            return self.generation
        if self.state in (
            DeletionState.DELETE_QUIESCING,
            DeletionState.EFFECT_QUIESCENCE_REQUIRED,
            DeletionState.DELETING,
        ):
            return self.generation
        self.generation += 1
        self.state = DeletionState.DELETE_QUIESCING
        self.admission_fenced = True
        return self.generation

    def acknowledge_worker(self, worker_id: str, generation: int) -> None:
        self._require_generation(generation)
        if worker_id not in self._workers:
            raise GatewayError(FailureCode.CONFLICT)
        self._workers[worker_id] = True

    def acknowledge_effect(self, effect_id: str, generation: int, *, quiesced: bool = False) -> None:
        self._require_generation(generation)
        if effect_id not in self._effects:
            raise GatewayError(FailureCode.CONFLICT)
        self._effects[effect_id] = True
        self._effect_quiesced[effect_id] = quiesced

    def _require_generation(self, generation: int) -> None:
        if generation != self.generation:
            raise GatewayError(FailureCode.STALE_FENCE)

    def late_callback(self, generation: int) -> None:
        self._require_generation(generation)
        if self.admission_fenced:
            raise GatewayError(FailureCode.STALE_FENCE)

    def progress(self) -> DeletionSnapshot:
        if self.state not in (
            DeletionState.DELETE_QUIESCING,
            DeletionState.EFFECT_QUIESCENCE_REQUIRED,
        ):
            return self.snapshot()
        missing_workers = sum(not value for value in self._workers.values())
        missing_effects = sum(
            not self._effects[effect_id] or not self._effect_quiesced[effect_id]
            for effect_id in self._effects
        )
        if missing_effects:
            self.state = DeletionState.EFFECT_QUIESCENCE_REQUIRED
            return self.snapshot()
        if missing_workers:
            return self.snapshot()
        self.state = DeletionState.DELETING
        return self.snapshot()

    def absence_pass(self, generation: int, observed_inventory: set[str]) -> DeletionSnapshot:
        self._require_generation(generation)
        if self.state not in (DeletionState.DELETING, DeletionState.DELETION_FAILED):
            raise GatewayError(FailureCode.QUIESCENCE_REQUIRED)
        if observed_inventory:
            self.state = DeletionState.DELETION_FAILED
            raise GatewayError(FailureCode.DELETION_FAILED)
        self._absence_passes += 1
        if self._absence_passes >= 2:
            self.state = DeletionState.DELETED
        return self.snapshot()

    def snapshot(self) -> DeletionSnapshot:
        return DeletionSnapshot(
            self.generation,
            self.state,
            self.admission_fenced,
            sum(not value for value in self._workers.values()),
            sum(not value for value in self._effects.values()),
            self._absence_passes,
        )
