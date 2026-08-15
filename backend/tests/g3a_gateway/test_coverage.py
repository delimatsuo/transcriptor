from __future__ import annotations

import pytest

from backend.g3a_gateway.contracts import AtomicRange, CoverageState, FailureCode, GatewayError, Segment, TerminalClaim, TerminalOutcome
from backend.g3a_gateway.coverage import CoverageLedger, IntervalSet


def test_interval_set_preserves_sparse_ranges_and_prefix() -> None:
    intervals = IntervalSet()
    intervals.add(2, 4)
    intervals.add(0, 1)
    assert intervals.intervals == ((0, 1), (2, 4))
    assert intervals.contiguous_prefix() == 0
    with pytest.raises(GatewayError) as overlap:
        intervals.add(3, 5)
    assert overlap.value.code is FailureCode.OVERLAP


def test_terminal_claim_rejects_duplicate_and_segment_overlap() -> None:
    ledger = CoverageLedger()
    atomic = AtomicRange(0, 0)
    ledger.admit_range(atomic)
    first = Segment("a", 0, 500, "digest-a")
    second = Segment("b", 400, 800, "digest-b")
    with pytest.raises(GatewayError) as overlap:
        ledger.terminalize(TerminalClaim(atomic, TerminalOutcome.TRANSCRIPT, (first, second)))
    assert overlap.value.code is FailureCode.OVERLAP
    claim = TerminalClaim(atomic, TerminalOutcome.TRANSCRIPT, (first,))
    ledger.terminalize(claim)
    ledger.terminalize(claim)
    with pytest.raises(GatewayError) as conflict:
        ledger.terminalize(TerminalClaim(atomic, TerminalOutcome.GAP, reason="different"))
    assert conflict.value.code is FailureCode.CONFLICT


def test_two_finals_inside_one_atomic_range_keep_distinct_segments() -> None:
    ledger = CoverageLedger()
    atomic = AtomicRange(10, 10)
    ledger.admit_range(atomic)
    segments = (
        Segment("final-a", 8_000, 8_400, "text-a"),
        Segment("final-b", 8_400, 8_800, "text-b"),
    )
    ledger.terminalize(TerminalClaim(atomic, TerminalOutcome.TRANSCRIPT, segments))
    snapshot = ledger.complete()
    assert snapshot.state is CoverageState.COMPLETED
    assert len(snapshot.claims[0].segments) == 2
