"""Rolling summaries must not queue stale duplicate provider work."""

import asyncio

import pytest

from backend import main


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
