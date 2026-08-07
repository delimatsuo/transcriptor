"""Regression tests for rotation delivery and transcript-window evidence."""

import pytest

from backend.schemas.models import TranscriptSegment
from backend.scripts.soak_rotation import transcript_boundary_window


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


def test_transcript_window_uses_provider_result_ends():
    window = transcript_boundary_window(
        [final_segment(260.0, 268.5), final_segment(274.25, 280.0)],
        270.0,
    )

    assert window == pytest.approx((1.5, 10.0))


def test_transcript_window_selects_nearest_results_on_each_side():
    window = transcript_boundary_window(
        [
            final_segment(250.0, 260.0),
            final_segment(260.0, 269.5),
            final_segment(270.25, 280.0),
            final_segment(280.0, 290.0),
        ],
        270.0,
    )

    assert window == pytest.approx((0.5, 10.0))


@pytest.mark.parametrize(
    "segments",
    [
        [final_segment(271.0, 280.0)],
        [final_segment(260.0, 269.0)],
        [],
    ],
)
def test_boundary_without_both_sides_is_inconclusive(segments):
    assert transcript_boundary_window(segments, 270.0) is None
