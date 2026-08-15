from __future__ import annotations

import pytest

from backend.g3a_gateway.contracts import FailureCode, GatewayError
from backend.g3a_gateway.deletion import DeletionCoordinator, DeletionState


def test_delete_requires_positive_worker_and_effect_quiescence() -> None:
    coordinator = DeletionCoordinator({"session", "transcript"})
    coordinator.register_worker("worker")
    coordinator.register_effect("effect")
    generation = coordinator.request()
    assert coordinator.state is DeletionState.DELETE_QUIESCING
    assert coordinator.progress().state is DeletionState.EFFECT_QUIESCENCE_REQUIRED
    coordinator.acknowledge_worker("worker", generation)
    coordinator.acknowledge_effect("effect", generation, quiesced=True)
    assert coordinator.progress().state is DeletionState.DELETING
    coordinator.absence_pass(generation, set())
    final = coordinator.absence_pass(generation, set())
    assert final.state is DeletionState.DELETED


def test_late_generation_and_unverified_inventory_fail_closed() -> None:
    coordinator = DeletionCoordinator()
    generation = coordinator.request()
    with pytest.raises(GatewayError) as stale:
        coordinator.late_callback(generation - 1)
    assert stale.value.code is FailureCode.STALE_FENCE
    assert coordinator.progress().state is DeletionState.DELETING
    with pytest.raises(GatewayError) as remaining:
        coordinator.absence_pass(generation, {"late-record"})
    assert remaining.value.code is FailureCode.DELETION_FAILED


def test_delete_is_idempotent_after_completion() -> None:
    coordinator = DeletionCoordinator()
    generation = coordinator.request()
    coordinator.progress()
    coordinator.absence_pass(generation, set())
    coordinator.absence_pass(generation, set())
    assert coordinator.request() == generation
    assert coordinator.state is DeletionState.DELETED
