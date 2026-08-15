"""Ordered coverage, terminal claims, and derived gateway state."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import (
    AtomicRange,
    CoverageState,
    FailureCode,
    GatewayError,
    Segment,
    TerminalClaim,
    TerminalOutcome,
)


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


class IntervalSet:
    """Disjoint half-open integer intervals."""

    def __init__(self) -> None:
        self._intervals: list[tuple[int, int]] = []

    @property
    def intervals(self) -> tuple[tuple[int, int], ...]:
        return tuple(self._intervals)

    def add(self, first: int, last_exclusive: int) -> None:
        if first < 0 or last_exclusive <= first:
            raise GatewayError(FailureCode.CONFLICT)
        candidate = (first, last_exclusive)
        if any(_overlaps(candidate, interval) for interval in self._intervals):
            raise GatewayError(FailureCode.OVERLAP)
        self._intervals.append(candidate)
        self._intervals.sort()

    def contains(self, first: int, last_exclusive: int) -> bool:
        return any(start <= first and last_exclusive <= end for start, end in self._intervals)

    def contiguous_prefix(self) -> int:
        cursor = 0
        for start, end in self._intervals:
            if start > cursor:
                break
            cursor = max(cursor, end)
        return cursor - 1


@dataclass(frozen=True)
class CoverageSnapshot:
    state: CoverageState
    admitted: tuple[AtomicRange, ...]
    claims: tuple[TerminalClaim, ...]
    gap_count: int


class CoverageLedger:
    def __init__(self) -> None:
        self.state = CoverageState.NOT_STARTED
        self._admitted: list[AtomicRange] = []
        self._claims: dict[AtomicRange, TerminalClaim] = {}

    @property
    def claims(self) -> tuple[TerminalClaim, ...]:
        return tuple(self._claims.values())

    def admit_range(self, atomic_range: AtomicRange) -> None:
        if any(
            atomic_range.first_sequence <= item.last_sequence_inclusive
            and item.first_sequence <= atomic_range.last_sequence_inclusive
            for item in self._admitted
        ):
            raise GatewayError(FailureCode.OVERLAP)
        self._admitted.append(atomic_range)
        self._admitted.sort()
        self.state = CoverageState.OPEN

    def terminalize(self, claim: TerminalClaim) -> None:
        if claim.atomic_range not in self._admitted:
            raise GatewayError(FailureCode.CONFLICT)
        self._validate_segments(claim.segments)
        existing = self._claims.get(claim.atomic_range)
        if existing is None:
            self._claims[claim.atomic_range] = claim
            return
        if existing.outcome is not claim.outcome:
            raise GatewayError(FailureCode.CONFLICT)
        if claim.outcome is TerminalOutcome.GAP:
            if existing.claim_id != claim.claim_id:
                raise GatewayError(FailureCode.CONFLICT)
            return

        # Provider finals are independent segments. Multiple finals may fall
        # inside or straddle one atomic audio chunk, so merge exact segment
        # identities into the single terminal audio-coverage claim instead of
        # treating a second final as a duplicate/conflict.
        by_id = {segment.segment_id: segment for segment in existing.segments}
        for segment in claim.segments:
            previous = by_id.get(segment.segment_id)
            if previous is not None and previous != segment:
                raise GatewayError(FailureCode.CONFLICT)
            by_id[segment.segment_id] = segment
        merged = tuple(
            sorted(by_id.values(), key=lambda item: (item.start_sample, item.end_sample_exclusive, item.segment_id))
        )
        if merged == existing.segments:
            return
        self._claims[claim.atomic_range] = TerminalClaim(
            claim.atomic_range,
            TerminalOutcome.TRANSCRIPT,
            merged,
        )

    @staticmethod
    def _validate_segments(segments: tuple[Segment, ...]) -> None:
        segment_ids = [segment.segment_id for segment in segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise GatewayError(FailureCode.CONFLICT)

    def begin_finalization(self) -> None:
        if self.state is not CoverageState.OPEN:
            raise GatewayError(FailureCode.CONFLICT)
        self.state = CoverageState.FINALIZING

    def complete(self) -> CoverageSnapshot:
        if self.state not in (CoverageState.OPEN, CoverageState.FINALIZING):
            raise GatewayError(FailureCode.CONFLICT)
        for atomic_range in self._admitted:
            if atomic_range not in self._claims:
                self._claims[atomic_range] = TerminalClaim(
                    atomic_range,
                    TerminalOutcome.GAP,
                    reason="missing_terminal_outcome",
                )
        gap_count = sum(claim.outcome is TerminalOutcome.GAP for claim in self._claims.values())
        self.state = CoverageState.COMPLETED_WITH_GAPS if gap_count else CoverageState.COMPLETED
        return self.snapshot(gap_count)

    def snapshot(self, gap_count: int | None = None) -> CoverageSnapshot:
        gaps = (
            gap_count
            if gap_count is not None
            else sum(claim.outcome is TerminalOutcome.GAP for claim in self._claims.values())
        )
        return CoverageSnapshot(self.state, tuple(self._admitted), self.claims, gaps)
