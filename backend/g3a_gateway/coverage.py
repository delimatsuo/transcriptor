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
        if claim.atomic_range in self._claims:
            existing = self._claims[claim.atomic_range]
            if existing.claim_id == claim.claim_id:
                return
            raise GatewayError(FailureCode.CONFLICT)
        self._validate_segments(claim.segments)
        self._claims[claim.atomic_range] = claim

    @staticmethod
    def _validate_segments(segments: tuple[Segment, ...]) -> None:
        ordered = sorted(segments, key=lambda item: (item.start_sample, item.end_sample_exclusive))
        for left, right in zip(ordered, ordered[1:]):
            if _overlaps(
                (left.start_sample, left.end_sample_exclusive),
                (right.start_sample, right.end_sample_exclusive),
            ):
                raise GatewayError(FailureCode.OVERLAP)

    def begin_finalization(self) -> None:
        if self.state is not CoverageState.OPEN:
            raise GatewayError(FailureCode.CONFLICT)
        self.state = CoverageState.FINALIZING

    def complete(self) -> CoverageSnapshot:
        if self.state not in (CoverageState.OPEN, CoverageState.FINALIZING):
            raise GatewayError(FailureCode.CONFLICT)
        missing = len(self._admitted) - len(self._claims)
        self.state = CoverageState.COMPLETED_WITH_GAPS if missing else CoverageState.COMPLETED
        return self.snapshot(missing)

    def snapshot(self, gap_count: int | None = None) -> CoverageSnapshot:
        gaps = gap_count if gap_count is not None else len(self._admitted) - len(self._claims)
        return CoverageSnapshot(self.state, tuple(self._admitted), self.claims, gaps)
