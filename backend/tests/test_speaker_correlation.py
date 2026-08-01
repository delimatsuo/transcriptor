"""Tests for SpeakerCorrelator's time-window active-speaker matching."""

from __future__ import annotations

import time

from backend.schemas.models import ActiveSpeakerEvent, TranscriptSegment
from backend.speaker_correlation import SpeakerCorrelator


def _segment(start: float, end: float) -> TranscriptSegment:
    return TranscriptSegment(text="hello", start_time=start, end_time=end, is_final=True)


def _event(name: str, timestamp: float) -> ActiveSpeakerEvent:
    return ActiveSpeakerEvent(participant_name=name, timestamp=timestamp)


def test_no_events_returns_none():
    correlator = SpeakerCorrelator(session_id="s1")
    assert correlator.correlate(_segment(10.0, 12.0)) == (None, 0.0)


def test_zero_duration_segment_returns_none():
    correlator = SpeakerCorrelator(session_id="s1")
    correlator.add_events([_event("Alice", 9.0)])
    assert correlator.correlate(_segment(10.0, 10.0)) == (None, 0.0)


def test_no_events_in_window_infers_self_when_self_name_set():
    correlator = SpeakerCorrelator(session_id="s1")
    correlator.add_events([_event("Alice", 0.0)])  # far before any window
    correlator.set_participants([{"name": "Me", "isSelf": True}], self_name="Me")
    assert correlator.correlate(_segment(100.0, 102.0)) == ("Me", 0.7)


def test_no_events_in_window_returns_none_without_self_name():
    correlator = SpeakerCorrelator(session_id="s1")
    correlator.add_events([_event("Alice", 0.0)])
    assert correlator.correlate(_segment(100.0, 102.0)) == (None, 0.0)


def test_single_speaker_full_overlap_returns_high_confidence():
    correlator = SpeakerCorrelator(session_id="s1")
    correlator.add_events([_event("Alice", 9.0)])
    speaker, confidence = correlator.correlate(_segment(10.0, 12.0))
    assert speaker == "Alice"
    assert confidence == 1.0


def test_majority_speaker_wins_with_partial_overlap():
    correlator = SpeakerCorrelator(session_id="s1")
    # Alice speaks 9.0-11.0 (1s inside segment), Bob speaks 11.0 onward (3s inside segment).
    correlator.add_events([_event("Alice", 9.0), _event("Bob", 11.0)])
    speaker, confidence = correlator.correlate(_segment(10.0, 14.0))
    assert speaker == "Bob"
    assert confidence == 0.75


def test_low_confidence_below_point_three_infers_self():
    correlator = SpeakerCorrelator(session_id="s1")
    correlator.add_events([_event("Alice", 18.0)])  # only overlaps last 2s of a 10s segment
    correlator.set_participants([{"name": "Me", "isSelf": True}], self_name="Me")
    assert correlator.correlate(_segment(10.0, 20.0)) == ("Me", 0.6)


def test_low_confidence_below_point_three_without_self_name_returns_raw_confidence():
    correlator = SpeakerCorrelator(session_id="s1")
    correlator.add_events([_event("Alice", 18.0)])
    speaker, confidence = correlator.correlate(_segment(10.0, 20.0))
    assert speaker is None
    assert abs(confidence - 0.2) < 1e-9


def test_confidence_between_thresholds_does_not_infer_self():
    # 0.4 confidence: below min_confidence (0.5) but NOT below 0.3, so self-inference
    # must not fire even though a self name is set — this exercises the exact `< 0.3`
    # boundary, not just "below min_confidence".
    correlator = SpeakerCorrelator(session_id="s1")
    correlator.add_events([_event("Alice", 16.0)])  # overlaps last 4s of a 10s segment
    correlator.set_participants([{"name": "Me", "isSelf": True}], self_name="Me")
    speaker, confidence = correlator.correlate(_segment(10.0, 20.0))
    assert speaker is None
    assert abs(confidence - 0.4) < 1e-9


def test_clock_uncertainty_widens_window():
    correlator = SpeakerCorrelator(session_id="s1", window_back=1.0, window_forward=1.0)
    correlator.update_clock_uncertainty(0.0)
    correlator.add_events([_event("Alice", 9.0)])

    # With a 1s/1s window and zero uncertainty, an event at t=9.0 is outside the
    # window for a segment ending at t=12.0 (window is [11.0, 13.0]).
    assert correlator.correlate(_segment(10.0, 12.0)) == (None, 0.0)

    # Widening clock uncertainty pulls the same event back into range.
    correlator.update_clock_uncertainty(3.0)
    speaker, confidence = correlator.correlate(_segment(10.0, 12.0))
    assert speaker == "Alice"
    assert confidence == 1.0


def test_add_events_maintains_sorted_order():
    correlator = SpeakerCorrelator(session_id="s1")
    correlator.add_events([_event("A", 5.0)])
    correlator.add_events([_event("B", 2.0), _event("C", 8.0)])
    correlator.add_events([_event("D", 1.0)])
    assert [ev.timestamp for ev in correlator.events] == [1.0, 2.0, 5.0, 8.0]


def test_add_events_rolling_cap_evicts_oldest():
    correlator = SpeakerCorrelator(session_id="s1")
    events = [_event(f"P{i}", float(i)) for i in range(SpeakerCorrelator.MAX_EVENTS + 10)]
    correlator.add_events(events)
    assert len(correlator.events) == SpeakerCorrelator.MAX_EVENTS
    # The retained events are the most recent (highest-timestamp) ones.
    assert correlator.events[0].timestamp == 10.0
    assert correlator.events[-1].timestamp == float(SpeakerCorrelator.MAX_EVENTS + 9)


def test_heartbeat_never_received_is_not_stale():
    correlator = SpeakerCorrelator(session_id="s1")
    assert correlator.is_heartbeat_stale() is False


def test_heartbeat_recent_is_not_stale():
    correlator = SpeakerCorrelator(session_id="s1")
    correlator.update_heartbeat(True)
    assert correlator.is_heartbeat_stale(timeout=30.0) is False
    assert correlator.healthy is True


def test_heartbeat_old_is_stale():
    correlator = SpeakerCorrelator(session_id="s1")
    correlator.update_heartbeat(False)
    correlator._last_heartbeat = time.time() - 60.0
    assert correlator.is_heartbeat_stale(timeout=30.0) is True
    assert correlator.healthy is False


def test_set_participants_records_self_name():
    correlator = SpeakerCorrelator(session_id="s1")
    correlator.set_participants(
        [{"name": "Alice", "isSelf": False}, {"name": "Me", "isSelf": True}],
        self_name="Me",
    )
    assert correlator._self_name == "Me"
    assert correlator.participants == {"Alice": "Alice", "Me": "Me"}
