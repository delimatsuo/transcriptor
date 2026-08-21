"""Verify host audio capture is disabled by default to prevent duplicate transcription."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from backend.config import Settings
from backend.sessions.manager import SessionManager
from backend import main as backend_main


def test_host_audio_capture_disabled_by_default():
    settings = Settings(google_cloud_project="test-project")
    assert settings.host_audio_capture_enabled is False


def test_create_session_does_not_spawn_host_pipeline_when_disabled(monkeypatch):
    run_pipeline_called = False

    async def fake_run_pipeline(session_id: str):
        nonlocal run_pipeline_called
        run_pipeline_called = True

    manager = SessionManager(Settings(google_cloud_project="test-project"))
    monkeypatch.setattr(backend_main, "session_mgr", manager)
    monkeypatch.setattr(backend_main, "_run_audio_pipeline", fake_run_pipeline)
    monkeypatch.setattr(backend_main, "gemini_client", MagicMock())
    
    mock_settings = Settings(google_cloud_project="test-project", host_audio_capture_enabled=False)
    monkeypatch.setattr(backend_main, "settings", mock_settings)

    class FakeFirestoreStorage:
        save_session = AsyncMock()

    monkeypatch.setattr(backend_main, "firestore_storage", FakeFirestoreStorage())

    async def run():
        res = await backend_main.create_session(mode="meeting", title="Test")
        assert res["status"] == "active"
        sid = res["session_id"]
        assert run_pipeline_called is False
        assert backend_main.pipeline_tasks.get(sid) == []

    asyncio.run(run())
