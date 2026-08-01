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
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}

    def create_session(
        self,
        mode: SessionMode = SessionMode.MEETING,
        title: str = "",
    ) -> Session:
        """Create a new session."""
        session = Session(mode=mode, title=title or f"Session {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}")
        self._sessions[session.id] = session
        self._transcripts[session.id] = []

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
        """Add a transcript segment to the session."""
        if session_id not in self._transcripts:
            self._transcripts[session_id] = []
        self._transcripts[session_id].append(segment)

    def get_recent_transcript_text(
        self, session_id: str, max_segments: int = 50
    ) -> str:
        """Get recent transcript as plain text for LLM context."""
        segments = self._transcripts.get(session_id, [])
        recent = segments[-max_segments:]
        lines = []
        for seg in recent:
            speaker = seg.speaker_override or seg.speaker
            lines.append(f"[{speaker}]: {seg.text}")
        return "\n".join(lines)

    def get_transcript_word_count(self, session_id: str, from_seq: int = 0) -> int:
        """Count words in transcript segments after a given sequence number."""
        segments = self._transcripts.get(session_id, [])
        count = 0
        for seg in segments:
            if seg.sequence_number > from_seq and seg.is_final:
                count += len(seg.text.split())
        return count

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

    async def stop_session(self, session_id: str) -> Session | None:
        """Stop a session and mark it as completed."""
        session = self._sessions.get(session_id)
        if session is None:
            return None

        session.status = SessionStatus.COMPLETED
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
