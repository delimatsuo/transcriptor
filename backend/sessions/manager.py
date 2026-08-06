"""Session lifecycle management."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import structlog

from backend.config import Settings
from backend.schemas.models import (
    ActionItem,
    Session,
    SessionMode,
    SessionStatus,
    TranscriptSegment,
)

logger = structlog.get_logger()


class SessionManager:
    """Manages session lifecycle: create, heartbeat, stop, crash recovery."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._sessions: dict[str, Session] = {}
        self._transcripts: dict[str, list[TranscriptSegment]] = {}
        self._transcript_sequence_counters: dict[str, int] = {}
        # Prefix totals make rolling-summary cadence checks O(1) instead of
        # rescanning the entire un-summarized suffix for every final segment.
        # Entry zero is the total before the first transcript segment.
        self._transcript_final_word_prefix: dict[str, list[int]] = {}
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}

    def create_session(
        self,
        mode: SessionMode = SessionMode.MEETING,
        title: str = "",
        *,
        owner_id: str | None = None,
        org_id: str | None = None,
    ) -> Session:
        """Create a new session."""
        session = Session(
            mode=mode,
            title=title or f"Session {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
            owner_id=owner_id,
            org_id=org_id,
        )
        self._sessions[session.id] = session
        self._transcripts[session.id] = []
        self._transcript_sequence_counters[session.id] = 0
        self._transcript_final_word_prefix[session.id] = [0]

        logger.info(
            "session_created",
            session_id=session.id,
            mode=mode.value,
            title=session.title,
        )
        return session

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def get_transcript(self, session_id: str) -> list[TranscriptSegment]:
        return self._transcripts.get(session_id, [])

    def add_transcript_segment(
        self, session_id: str, segment: TranscriptSegment
    ) -> None:
        """Add a segment and assign a session-global durable ordinal.

        Each STT source owns a counter that can restart at one.  Normalize the
        public/persisted sequence at the session boundary so dual capture has a
        deterministic unique order, while retaining the original source-local
        value for provenance and legacy reconstruction.
        """
        if session_id not in self._transcripts:
            self._transcripts[session_id] = []
            self._transcript_sequence_counters[session_id] = 0
            self._transcript_final_word_prefix[session_id] = [0]
        if segment.source_sequence_number is None:
            segment.source_sequence_number = segment.sequence_number
        next_sequence = self._transcript_sequence_counters[session_id] + 1
        self._transcript_sequence_counters[session_id] = next_sequence
        segment.sequence_number = next_sequence
        self._transcripts[session_id].append(segment)
        prefix = self._transcript_final_word_prefix[session_id]
        prefix.append(
            prefix[-1] + (len(segment.text.split()) if segment.is_final else 0)
        )

    def get_recent_transcript_text(
        self, session_id: str, max_segments: int = 50
    ) -> str:
        """Get recent transcript as plain text for LLM context."""
        segments = self._transcripts.get(session_id, [])
        recent = segments[-max_segments:]
        return self._format_transcript_text(recent)

    @staticmethod
    def _format_transcript_text(segments: list[TranscriptSegment]) -> str:
        """Format transcript segments without exposing storage objects."""
        lines = []
        for seg in segments:
            speaker = seg.speaker_override or seg.speaker
            lines.append(f"[{speaker}]: {seg.text}")
        return "\n".join(lines)

    def get_transcript_text_since_index(
        self,
        session_id: str,
        from_index: int = 0,
        max_segments: int = 50,
    ) -> str:
        """Get only transcript segments appended after a prior list index.

        Stream sequence numbers are source-local and may reset for a second
        audio channel, so rolling context uses the session list index instead.
        """
        segments = self._transcripts.get(session_id, [])
        start = max(0, from_index)
        appended = segments[start:]
        if max_segments > 0:
            appended = appended[-max_segments:]
        return self._format_transcript_text(appended)

    def get_transcript_word_count(self, session_id: str, from_seq: int = 0) -> int:
        """Count words in transcript segments after a given sequence number."""
        prefix = self._transcript_final_word_prefix.get(session_id)
        if not prefix:
            return 0
        start = min(max(0, from_seq), len(prefix) - 1)
        return prefix[-1] - prefix[start]

    def get_transcript_word_count_since_index(
        self, session_id: str, from_index: int = 0
    ) -> int:
        """Count final words appended after a session transcript list index."""
        prefix = self._transcript_final_word_prefix.get(session_id)
        if not prefix:
            return 0
        start = min(max(0, from_index), len(prefix) - 1)
        return prefix[-1] - prefix[start]

    async def start_heartbeat(self, session_id: str) -> None:
        """Start a heartbeat task that updates last_active every 30s."""
        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(30)
                session = self._sessions.get(session_id)
                if session and session.status == SessionStatus.ACTIVE:
                    session.last_active = datetime.utcnow()
                    logger.debug("session_heartbeat", session_id=session_id)
                else:
                    break

        task = asyncio.create_task(_heartbeat())
        self._heartbeat_tasks[session_id] = task

    async def stop_session(
        self,
        session_id: str,
        *,
        transcription_complete: bool = True,
    ) -> Session | None:
        """Stop a session and preserve whether transcription finalized."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.status != SessionStatus.ACTIVE:
            return session

        session.status = (
            SessionStatus.COMPLETED
            if transcription_complete
            else SessionStatus.INCOMPLETE
        )
        session.ended_at = datetime.utcnow()

        # Cancel heartbeat
        task = self._heartbeat_tasks.pop(session_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        logger.info(
            "session_stopped",
            session_id=session_id,
            transcription_complete=transcription_complete,
            duration_seconds=(session.ended_at - session.started_at).total_seconds(),
            transcript_segments=len(self._transcripts.get(session_id, [])),
        )
        return session

    def detect_orphaned_sessions(self, timeout_minutes: int = 10) -> list[Session]:
        """Detect sessions that were left active without recent heartbeat.

        Called on startup to recover from crashes.
        """
        orphaned = []
        cutoff = datetime.utcnow() - timedelta(minutes=timeout_minutes)

        for session in self._sessions.values():
            if session.status == SessionStatus.ACTIVE and session.last_active < cutoff:
                session.status = SessionStatus.INCOMPLETE
                orphaned.append(session)
                logger.warning(
                    "orphaned_session_detected",
                    session_id=session.id,
                    last_active=session.last_active.isoformat(),
                )

        return orphaned

    def update_speaker_map(
        self, session_id: str, speaker_map: dict[str, str]
    ) -> None:
        """Update the speaker label mapping for a session."""
        session = self._sessions.get(session_id)
        if session:
            session.speaker_map = speaker_map

    def set_summary(self, session_id: str, summary: str) -> None:
        session = self._sessions.get(session_id)
        if session:
            session.summary = summary

    def set_action_items(self, session_id: str, items: list[ActionItem]) -> None:
        session = self._sessions.get(session_id)
        if session:
            session.action_items = items
