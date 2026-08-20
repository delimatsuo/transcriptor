from __future__ import annotations

import pytest

from backend.g3a_gateway.contracts import FailureCode, GatewayError
from backend.g3a_gateway.simulator import G3ASimulator


def test_generated_byte_path_forwards_and_terminalizes() -> None:
    simulator = G3ASimulator()
    handle = simulator.open_session()
    simulator.capture(handle, 0, b"a" * 1600, 1000)
    simulator.capture(handle, 1, b"b" * 1600, 1100)
    simulator.forward(1)
    simulator.terminalize(0)
    simulator.terminalize(1)
    snapshot = simulator.coverage.complete()
    assert snapshot.gap_count == 0
    assert simulator.transport.resident_bytes == 0


def test_kill_switch_is_content_free_and_fails_closed() -> None:
    simulator = G3ASimulator()
    simulator.log.activate_kill_switch("operator_stop")
    with pytest.raises(GatewayError) as exc:
        simulator.log.require_live()
    assert exc.value.code is FailureCode.REVOKED
    assert simulator.log.events == ()


def test_delete_fences_admission_and_completes_without_effect() -> None:
    simulator = G3ASimulator()
    handle = simulator.open_session()
    simulator.capture(handle, 0, b"x" * 1600, 1000)
    simulator.delete()
    assert simulator.deletion.state.value == "deleted"
    with pytest.raises(GatewayError) as exc:
        simulator.capture(handle, 1, b"y" * 1600, 1100)
    assert exc.value.code in (FailureCode.REVOKED, FailureCode.STALE_FENCE)


def test_rollback_prevents_live_operation() -> None:
    simulator = G3ASimulator()
    simulator.rollback.request()
    with pytest.raises(GatewayError) as exc:
        simulator.rollback.require_not_requested()
    assert exc.value.code is FailureCode.REVOKED
