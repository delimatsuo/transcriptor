"""Regression tests for rotation-boundary audio-gap measurement."""

import pytest

from backend.schemas.models import TranscriptSegment
from backend.scripts.soak_rotation import rotation_boundary_gap


def final_segment(start: float, end: float) -> TranscriptSegment:
    return TranscriptSegment(
        text="fala sintética",
        speaker="soak",
        start_time=start,
        end_time=end,
        confidence=1.0,
        sequence_number=1,
        is_final=True,
    )


def test_boundary_inside_final_segment_has_no_gap():
    assert rotation_boundary_gap([final_segment(268.0, 272.0)], 270.0) == 0.0


def test_boundary_gap_uses_provider_audio_intervals():
    gap = rotation_boundary_gap(
        [final_segment(260.0, 268.5), final_segment(274.25, 280.0)],
        270.0,
    )

    assert gap == pytest.approx(5.75)


def test_adjacent_audio_intervals_report_small_gap():
    gap = rotation_boundary_gap(
        [final_segment(260.0, 269.5), final_segment(270.25, 280.0)],
        270.0,
    )

    assert gap == pytest.approx(0.75)


@pytest.mark.parametrize(
    "segments",
    [
        [final_segment(271.0, 280.0)],
        [final_segment(260.0, 269.0)],
        [],
    ],
)
def test_boundary_without_both_sides_is_inconclusive(segments):
    assert rotation_boundary_gap(segments, 270.0) is None
