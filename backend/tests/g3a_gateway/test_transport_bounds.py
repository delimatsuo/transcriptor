from __future__ import annotations

import pytest

from backend.g3a_gateway.admission import AdmissionController
from backend.g3a_gateway.authority import AuthorityStore
from backend.g3a_gateway.contracts import AudioFrame, Disclosure, FailureCode, GatewayError, QuotaLimits
from backend.g3a_gateway.transport import TransportLedger


def _transport() -> tuple[TransportLedger, object, object]:
    authority = AuthorityStore()
    token = authority.issue_enrollment("actor", "org", 100)
    context = authority.open_context(
        token,
        session_id="s1",
        stream_id="stream",
        capture_generation=1,
        disclosure=Disclosure("notice-v2", "actor", "org", "s1", 100, "consent"),
        now_ms=101,
    )
    lease = authority.acquire_lease("s1", "owner", 1)
    return TransportLedger(AdmissionController(authority, QuotaLimits())), context, lease


def _frame(context, sequence: int, payload: bytes = b"x" * 1600) -> AudioFrame:
    return AudioFrame(context, f"event-{sequence}", sequence, sequence * 800, (sequence + 1) * 800, 8_000, 1, 1000 + sequence, payload, 32)


def test_out_of_order_and_changed_retry_fail_closed() -> None:
    transport, context, lease = _transport()
    first = _frame(context, 0)
    transport.capture(first, owner_id=lease.owner_id, runtime_epoch=lease.runtime_epoch, now_ms=1000)
    with pytest.raises(GatewayError) as order:
        transport.capture(_frame(context, 2), owner_id=lease.owner_id, runtime_epoch=lease.runtime_epoch, now_ms=1000)
    assert order.value.code is FailureCode.OUT_OF_ORDER
    with pytest.raises(GatewayError) as changed:
        transport.retry(_frame(context, 0, b"changed"), owner_id=lease.owner_id, runtime_epoch=lease.runtime_epoch, now_ms=1000)
    assert changed.value.code is FailureCode.CONFLICT


def test_journal_advances_only_contiguous_and_releases_resident_bytes() -> None:
    transport, context, lease = _transport()
    for sequence in (0, 1):
        transport.capture(_frame(context, sequence), owner_id=lease.owner_id, runtime_epoch=lease.runtime_epoch, now_ms=1000)
    with pytest.raises(GatewayError) as skip:
        transport.journal_forward(1, 1)
    assert skip.value.code is FailureCode.OUT_OF_ORDER
    transport.journal_forward(0, 1)
    assert transport.forwarded_prefix == 1
    assert transport.resident_bytes == 0


def test_fence_rejects_new_capture() -> None:
    transport, context, lease = _transport()
    transport.fence()
    with pytest.raises(GatewayError) as exc:
        transport.capture(_frame(context, 0), owner_id=lease.owner_id, runtime_epoch=lease.runtime_epoch, now_ms=1000)
    assert exc.value.code is FailureCode.STALE_FENCE
