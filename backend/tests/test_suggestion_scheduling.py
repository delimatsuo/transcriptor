"""Suggestion generation must not queue stale duplicate provider work."""

import asyncio

import pytest

from backend import main


def test_scheduler_deduplicates_inflight_suggestion(monkeypatch):
    release = asyncio.Event()
    started = asyncio.Event()

    async def fake_generate(session_id):
        assert session_id == "session-1"
        started.set()
        await release.wait()

    monkeypatch.setattr(main, "_generate_interview_suggestions", fake_generate)
    main.interview_suggestion_tasks.clear()

    async def run():
        main._schedule_interview_suggestions("session-1")
        first = main.interview_suggestion_tasks["session-1"]
        await started.wait()

        main._schedule_interview_suggestions("session-1")
        assert main.interview_suggestion_tasks["session-1"] is first

        release.set()
        await first
        await asyncio.sleep(0)
        assert "session-1" not in main.interview_suggestion_tasks

    try:
        asyncio.run(run())
    finally:
        main.interview_suggestion_tasks.clear()

def test_cleanup_cancels_inflight_suggestion(monkeypatch):
    started = asyncio.Event()

    async def fake_generate(_session_id):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "_generate_interview_suggestions", fake_generate)
    main.interview_suggestion_tasks.clear()

    async def run():
        main._schedule_interview_suggestions("session-2")
        task = main.interview_suggestion_tasks["session-2"]
        await started.wait()

        main._cleanup_session_context("session-2")
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "session-2" not in main.interview_suggestion_tasks

    try:
        asyncio.run(run())
    finally:
        main.interview_suggestion_tasks.clear()
