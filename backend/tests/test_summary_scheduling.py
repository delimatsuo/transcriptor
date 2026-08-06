"""Rolling summaries must not queue stale duplicate provider work."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from backend import main
from backend.schemas.models import TranscriptSegment


def test_scheduler_deduplicates_inflight_summary(monkeypatch):
    release = asyncio.Event()
    started = asyncio.Event()

    async def fake_generate(session_id):
        assert session_id == "session-1"
        started.set()
        await release.wait()

    monkeypatch.setattr(main, "_generate_rolling_summary", fake_generate)
    main.rolling_summary_tasks.clear()

    async def run():
        main._schedule_rolling_summary("session-1")
        first = main.rolling_summary_tasks["session-1"]
        await started.wait()

        main._schedule_rolling_summary("session-1")
        assert main.rolling_summary_tasks["session-1"] is first

        release.set()
        await first
        await asyncio.sleep(0)
        assert "session-1" not in main.rolling_summary_tasks

    try:
        asyncio.run(run())
    finally:
        main.rolling_summary_tasks.clear()


def test_cleanup_cancels_inflight_summary(monkeypatch):
    started = asyncio.Event()

    async def fake_generate(_session_id):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "_generate_rolling_summary", fake_generate)
    main.rolling_summary_tasks.clear()

    async def run():
        main._schedule_rolling_summary("session-2")
        task = main.rolling_summary_tasks["session-2"]
        await started.wait()

        main._cleanup_session_context("session-2")
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "session-2" not in main.rolling_summary_tasks

    try:
        asyncio.run(run())
    finally:
        main.rolling_summary_tasks.clear()


def test_failed_summary_does_not_broadcast_or_persist_stale_text(monkeypatch):
    class SessionManager:
        def get_transcript(self, _session_id):
            return [TranscriptSegment(text="new words", is_final=True)]

        def get_transcript_text_since_index(self, _session_id, *, from_index, max_segments):
            assert from_index == 0
            assert max_segments == 50
            return "[Candidato]: new words"

    class FailedContext:
        last_summary_seq = 0

        async def update_summary(self, _text, _current_seq):
            return ""

    class Storage:
        save_summary = AsyncMock()

    storage = Storage()
    monkeypatch.setattr(main, "session_mgr", SessionManager())
    monkeypatch.setattr(main, "gemini_client", object())
    monkeypatch.setattr(main, "firestore_storage", storage)
    monkeypatch.setattr(main.ws_manager, "broadcast", AsyncMock())
    previous_context = main.context_window
    main.context_window = None
    main.context_windows.clear()
    main.context_windows["session-1"] = FailedContext()

    try:
        asyncio.run(main._generate_rolling_summary("session-1"))
        main.ws_manager.broadcast.assert_not_awaited()
        storage.save_summary.assert_not_awaited()
    finally:
        main.context_windows.clear()
        main.context_window = previous_context
