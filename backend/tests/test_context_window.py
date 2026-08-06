"""Rolling-summary context remains bounded before provider invocation."""

import asyncio

from backend import main
from backend.config import Settings
from backend.llm.context_window import _TRUNCATION_MARKER, ContextWindowManager
from backend.schemas.models import TranscriptSegment
from backend.sessions.manager import SessionManager


class RecordingGemini:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def generate(self, *, system_instruction, user_message, **_kwargs):
        self.messages.append(user_message)
        return "summary"


def test_rolling_summary_keeps_recent_tail_within_configured_budget():
    gemini = RecordingGemini()
    manager = ContextWindowManager(
        Settings(
            google_cloud_project="test-project",
            llm_rolling_context_max_chars=100,
        ),
        gemini,
    )

    asyncio.run(manager.update_summary("old\n" + ("x" * 300) + "\nnew", 4))

    new_chunk = gemini.messages[0].split("## New Transcript Content\n", 1)[1]
    assert len(new_chunk) == 100
    assert new_chunk.startswith(_TRUNCATION_MARKER)
    assert new_chunk.endswith("new")


def test_failed_rolling_summary_uses_bounded_exponential_backoff():
    class FailingGemini:
        calls = 0

        async def generate(self, **_kwargs):
            self.calls += 1
            raise RuntimeError("provider unavailable")

    gemini = FailingGemini()
    manager = ContextWindowManager(
        Settings(
            google_cloud_project="test-project",
            llm_rolling_failure_backoff_seconds=10,
            llm_rolling_failure_backoff_max_seconds=15,
        ),
        gemini,
    )
    now = 100.0
    manager._now = lambda: now

    assert asyncio.run(manager.update_summary("chunk", 1)) == ""
    assert gemini.calls == 1
    assert manager.last_summary_seq == 0
    assert manager.should_summarize(300) is False

    now = 111.0
    assert manager.should_summarize(300) is True

    now = 120.0
    assert asyncio.run(manager.update_summary("chunk", 1)) == ""
    assert manager.should_summarize(300) is False
    now = 135.0
    assert manager.should_summarize(300) is True


def test_session_context_uses_appended_indices_not_source_sequence_numbers():
    manager = SessionManager(Settings(google_cloud_project="test-project"))
    session = manager.create_session()
    manager.add_transcript_segment(
        session.id,
        TranscriptSegment(text="old", sequence_number=1, is_final=True),
    )
    manager.add_transcript_segment(
        session.id,
        TranscriptSegment(text="new words", sequence_number=1, is_final=True),
    )

    assert manager.get_transcript_text_since_index(session.id, from_index=1) == (
        "[Speaker 1]: new words"
    )
    assert manager.get_transcript_word_count_since_index(session.id, from_index=1) == 2


def test_session_manager_normalizes_source_sequences_for_durable_order():
    manager = SessionManager(Settings(google_cloud_project="test-project"))
    session = manager.create_session()
    first = TranscriptSegment(
        text="first",
        speaker="Entrevistador",
        sequence_number=1,
        is_final=True,
    )
    second = TranscriptSegment(
        text="second",
        speaker="Candidato",
        sequence_number=1,
        is_final=True,
    )

    manager.add_transcript_segment(session.id, first)
    manager.add_transcript_segment(session.id, second)

    assert [segment.sequence_number for segment in manager.get_transcript(session.id)] == [1, 2]
    assert [
        segment.source_sequence_number
        for segment in manager.get_transcript(session.id)
    ] == [1, 1]
    assert "source_sequence_number" not in first.model_dump()


def test_main_rolling_context_isolated_per_session():
    settings = Settings(google_cloud_project="test-project")

    class FakeGemini:
        async def generate(self, **_kwargs):
            return "session summary"

    first = ContextWindowManager(settings, FakeGemini())
    second = ContextWindowManager(settings, FakeGemini())
    previous = main.context_window
    main.context_window = None
    main.context_windows.clear()
    main.context_windows.update({"session-a": first, "session-b": second})

    try:
        asyncio.run(first.update_summary("private A", 1))
        assert main._context_window_for("session-a") is first
        assert main._context_window_for("session-b") is second
        assert second.current_summary == ""
    finally:
        main.context_windows.clear()
        main.context_window = previous
