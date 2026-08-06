"""Rolling-summary context remains bounded before provider invocation."""

import asyncio
from unittest.mock import AsyncMock

from backend import main
from backend.config import Settings
from backend.llm.context_window import _TRUNCATION_MARKER, ContextWindowManager
from backend.schemas.models import SessionStatus, TranscriptSegment
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


def test_session_manager_word_count_uses_final_prefix_totals():
    manager = SessionManager(Settings(google_cloud_project="test-project"))
    session = manager.create_session()
    manager.add_transcript_segment(
        session.id,
        TranscriptSegment(text="one two", sequence_number=1, is_final=True),
    )
    manager.add_transcript_segment(
        session.id,
        TranscriptSegment(text="interim words are ignored", sequence_number=1),
    )
    manager.add_transcript_segment(
        session.id,
        TranscriptSegment(text="three four five", sequence_number=2, is_final=True),
    )

    assert manager.get_transcript_word_count_since_index(session.id, from_index=1) == 3
    assert manager.get_transcript_word_count(session.id, from_seq=1) == 3
    assert manager.get_transcript_word_count_since_index(session.id, from_index=99) == 0


def test_session_manager_rolling_batches_are_contiguous_and_bounded():
    manager = SessionManager(Settings(google_cloud_project="test-project"))
    session = manager.create_session()
    for index in range(4):
        manager.add_transcript_segment(
            session.id,
            TranscriptSegment(
                text=f"segment-{index}",
                sequence_number=index + 1,
                is_final=True,
            ),
        )

    first_text, first_end = manager.get_transcript_batch_since_index(
        session.id,
        max_segments=2,
        max_chars=200,
    )
    second_text, second_end = manager.get_transcript_batch_since_index(
        session.id,
        from_index=first_end,
        max_segments=2,
        max_chars=200,
    )

    assert first_end == 2
    assert second_end == 4
    assert "segment-0" in first_text
    assert "segment-1" in first_text
    assert "segment-2" in second_text
    assert "segment-3" in second_text
    assert "segment-2" not in first_text
    assert "segment-0" not in second_text


def test_terminal_transcript_memory_can_be_released_without_losing_session_metadata():
    manager = SessionManager(Settings(google_cloud_project="test-project"))
    session = manager.create_session()
    manager.add_transcript_segment(
        session.id,
        TranscriptSegment(text="durable final", sequence_number=1, is_final=True),
    )

    manager.release_transcript_memory(session.id)

    assert manager.get_session(session.id) is session
    assert manager.get_transcript(session.id) == []
    assert manager.get_transcript_word_count_since_index(session.id) == 0


def test_incomplete_cleanup_defers_release_until_terminal_write(monkeypatch):
    manager = SessionManager(Settings(google_cloud_project="test-project"))
    session = manager.create_session()
    manager.add_transcript_segment(
        session.id,
        TranscriptSegment(text="unsaved final", sequence_number=1, is_final=True),
    )
    session.status = SessionStatus.INCOMPLETE
    previous_manager = main.session_mgr
    monkeypatch.setattr(main, "session_mgr", manager)

    try:
        main._cleanup_session_context(
            session.id,
            release_transcript_memory=False,
        )
        assert len(manager.get_transcript(session.id)) == 1

        main._release_terminal_transcript_memory(session.id)
        assert manager.get_transcript(session.id) == []
    finally:
        main.session_mgr = previous_manager


def test_child_persistence_failure_prevents_terminal_memory_release(monkeypatch):
    manager = SessionManager(Settings(google_cloud_project="test-project"))
    session = manager.create_session()
    manager.add_transcript_segment(
        session.id,
        TranscriptSegment(text="only in memory", sequence_number=1, is_final=True),
    )
    session.status = SessionStatus.INCOMPLETE
    previous_manager = main.session_mgr
    monkeypatch.setattr(main, "session_mgr", manager)
    main.transcript_persistence_failures.add(session.id)

    try:
        main._release_terminal_transcript_memory(session.id)
        assert len(manager.get_transcript(session.id)) == 1
    finally:
        main.transcript_persistence_failures.discard(session.id)
        main.session_mgr = previous_manager


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


def test_rolling_summary_catches_up_contiguous_backlog(monkeypatch):
    settings = Settings(
        google_cloud_project="test-project",
        llm_rolling_context_max_chars=80,
    )
    manager = SessionManager(settings)
    session = manager.create_session()
    for index in range(40):
        manager.add_transcript_segment(
            session.id,
            TranscriptSegment(
                text=f"segment-{index} words here",
                sequence_number=index + 1,
                is_final=True,
            ),
        )

    class FakeGemini:
        def __init__(self):
            self.messages: list[str] = []

        async def generate(self, **kwargs):
            self.messages.append(kwargs["user_message"])
            return "rolling summary"

    gemini = FakeGemini()
    context = ContextWindowManager(settings, gemini)
    previous = (
        main.session_mgr,
        main.gemini_client,
        main.firestore_storage,
        main.context_windows,
        main.rolling_summary_tasks,
        main.rolling_summary_followups,
    )
    main.session_mgr = manager
    main.gemini_client = gemini
    main.firestore_storage = type(
        "Storage",
        (),
        {"save_summary": AsyncMock()},
    )()
    main.context_windows = {session.id: context}
    main.rolling_summary_tasks = {}
    main.rolling_summary_followups = set()
    monkeypatch.setattr(main.ws_manager, "broadcast", AsyncMock())

    async def run():
        main._schedule_rolling_summary(session.id)
        for _ in range(2000):
            await asyncio.sleep(0)
            if (
                session.id not in main.rolling_summary_tasks
                and session.id not in main.rolling_summary_followups
            ):
                break

    try:
        asyncio.run(run())
        assert context.last_summary_seq == len(manager.get_transcript(session.id))
        assert len(gemini.messages) > 1
        assert all(len(message) <= settings.llm_max_input_chars for message in gemini.messages)
    finally:
        (
            main.session_mgr,
            main.gemini_client,
            main.firestore_storage,
            main.context_windows,
            main.rolling_summary_tasks,
            main.rolling_summary_followups,
        ) = previous
