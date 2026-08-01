"""Speaker correlation — matches active-speaker events from Chrome extension to transcript segments."""

from __future__ import annotations

import bisect
import time

import structlog

from backend.schemas.models import ActiveSpeakerEvent, TranscriptSegment

logger = structlog.get_logger()


class SpeakerCorrelator:
    """Correlates active-speaker events with transcript segments using time-window matching."""

    MAX_EVENTS = 2000  # rolling cap

    def __init__(
        self,
        session_id: str,
        window_back: float = 5.0,
        window_forward: float = 1.0,
        min_confidence: float = 0.5,
    ):
        self.session_id = session_id
        self.events: list[ActiveSpeakerEvent] = []
        self.participants: dict[str, str] = {}  # name -> name
        self._self_name: str = ""
        self._clock_uncertainty: float = 0.5
        self.window_back = window_back
        self.window_forward = window_forward
        self.min_confidence = min_confidence
        self._last_heartbeat: float = 0.0
        self.healthy: bool = True

    def add_events(self, events: list[ActiveSpeakerEvent]) -> None:
        """Add active-speaker events, maintaining sorted order by timestamp."""
        for e in events:
            # Use bisect to insert in sorted position
            timestamps = [ev.timestamp for ev in self.events]
            idx = bisect.bisect_right(timestamps, e.timestamp)
            self.events.insert(idx, e)

        # Rolling cap: evict oldest events
        if len(self.events) > self.MAX_EVENTS:
            self.events = self.events[-self.MAX_EVENTS:]

    def set_participants(self, participants: list[dict], self_name: str) -> None:
        """Set known participants. participants is a list of {name, isSelf} dicts."""
        self.participants = {p["name"]: p["name"] for p in participants}
        self._self_name = self_name

    def update_clock_uncertainty(self, uncertainty: float) -> None:
        """Widen/narrow correlation window based on measured sync quality."""
        self._clock_uncertainty = uncertainty

    def update_heartbeat(self, can_detect: bool) -> None:
        """Update health status from extension heartbeat."""
        self._last_heartbeat = time.time()
        self.healthy = can_detect

    def is_heartbeat_stale(self, timeout: float = 30.0) -> bool:
        """Check if heartbeat is overdue."""
        if self._last_heartbeat == 0:
            return False  # Never received one yet
        return (time.time() - self._last_heartbeat) > timeout

    def correlate(self, segment: TranscriptSegment) -> tuple[str | None, float]:
        """Match a transcript segment to an active speaker.

        Returns (speaker_name, confidence) or (None, 0.0) if no match.

        Algorithm:
        1. Find active-speaker events within a time window around the segment.
        2. Build spans of who was speaking when.
        3. Calculate overlap with the segment's time range.
        4. Return the speaker with the most overlap, if above min_confidence.
        5. If no remote speaker events in window but segment exists → infer self.
        """
        if not self.events:
            return None, 0.0

        # Dynamic window based on clock uncertainty
        back = self.window_back + self._clock_uncertainty
        forward = self.window_forward + self._clock_uncertainty

        seg_start = segment.start_time
        seg_end = segment.end_time
        seg_duration = seg_end - seg_start

        if seg_duration <= 0:
            return None, 0.0

        window_start = seg_end - back
        window_end = seg_end + forward

        # Binary search for events in the time window
        timestamps = [ev.timestamp for ev in self.events]
        left = bisect.bisect_left(timestamps, window_start)
        right = bisect.bisect_right(timestamps, window_end)

        window_events = self.events[left:right]

        if not window_events:
            # No remote speaker events — if we have a self_name, infer self is speaking
            if self._self_name:
                return self._self_name, 0.7  # moderate confidence for inference
            return None, 0.0

        # Build speaker spans from events
        # Each event marks when a speaker became active; they stay active until the next event
        spans: list[tuple[str, float, float]] = []  # (speaker, start, end)

        # Include events just before the window for context
        context_left = max(0, left - 1)
        all_relevant = self.events[context_left:right]

        for i, ev in enumerate(all_relevant):
            span_start = ev.timestamp
            if i + 1 < len(all_relevant):
                span_end = all_relevant[i + 1].timestamp
            else:
                # Last event — extend to window end
                span_end = window_end

            # Clip span to segment time range
            overlap_start = max(span_start, seg_start)
            overlap_end = min(span_end, seg_end)

            if overlap_end > overlap_start:
                spans.append((ev.participant_name, overlap_start, overlap_end))

        if not spans:
            # Events exist in window but no overlap with segment — infer self
            if self._self_name:
                return self._self_name, 0.6
            return None, 0.0

        # Aggregate overlap by speaker
        speaker_overlap: dict[str, float] = {}
        for speaker, start, end in spans:
            speaker_overlap[speaker] = speaker_overlap.get(speaker, 0.0) + (end - start)

        # Find best match
        best_speaker = max(speaker_overlap, key=speaker_overlap.get)  # type: ignore[arg-type]
        best_overlap = speaker_overlap[best_speaker]
        confidence = best_overlap / seg_duration

        if confidence >= self.min_confidence:
            return best_speaker, min(confidence, 1.0)

        # Low confidence on remote speaker — might be self speaking
        # If remote speaker has low overlap, likely self is speaking
        if self._self_name and confidence < 0.3:
            return self._self_name, 0.6

        return None, confidence
