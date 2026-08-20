"""The backend must fail loudly instead of hanging on expired ADC."""

import asyncio
import re
import threading
import time
from unittest.mock import Mock

import pytest
from google.auth.exceptions import RefreshError

from backend import startup_credentials
from backend.config import Settings


def test_probe_refreshes_default_credentials(monkeypatch, capsys):
    credentials = Mock()
    request = object()
    default = Mock(return_value=(credentials, "test-project"))

    monkeypatch.setattr(startup_credentials.google.auth, "default", default)
    monkeypatch.setattr(startup_credentials, "Request", lambda: request)

    asyncio.run(startup_credentials.probe_application_default_credentials())

    default.assert_called_once_with()
    credentials.refresh.assert_called_once_with(request)
    assert capsys.readouterr().err == ""


def test_probe_exits_loudly_when_credential_refresh_fails(monkeypatch, capsys):
    credentials = Mock()
    credentials.refresh.side_effect = RefreshError("reauthentication required")
    request = object()

    monkeypatch.setattr(
        startup_credentials.google.auth,
        "default",
        lambda: (credentials, "test-project"),
    )
    monkeypatch.setattr(startup_credentials, "Request", lambda: request)

    with pytest.raises(
        startup_credentials.ADCStartupError,
        match=f"^{re.escape(startup_credentials.ADC_ERROR_MESSAGE)}$",
    ):
        asyncio.run(
            startup_credentials.probe_application_default_credentials(
                timeout_seconds=0.5
            )
        )

    credentials.refresh.assert_called_once_with(request)
    assert capsys.readouterr().err == f"{startup_credentials.ADC_ERROR_MESSAGE}\n"


def test_probe_times_out_without_waiting_for_stuck_refresh(monkeypatch, capsys):
    release_refresh = threading.Event()
    refresh_finished = threading.Event()
    credentials = Mock()

    def blocking_refresh(_request):
        release_refresh.wait(timeout=1.0)
        refresh_finished.set()

    credentials.refresh.side_effect = blocking_refresh
    monkeypatch.setattr(
        startup_credentials.google.auth,
        "default",
        lambda: (credentials, "test-project"),
    )

    async def run_probe_and_release_worker():
        started_at = time.monotonic()
        with pytest.raises(
            startup_credentials.ADCStartupError,
            match=f"^{re.escape(startup_credentials.ADC_ERROR_MESSAGE)}$",
        ):
            await startup_credentials.probe_application_default_credentials(
                timeout_seconds=0.02
            )
        elapsed = time.monotonic() - started_at

        release_refresh.set()
        for _ in range(100):
            if refresh_finished.is_set():
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0)
        return elapsed

    elapsed = asyncio.run(run_probe_and_release_worker())

    assert elapsed < 0.5
    assert refresh_finished.is_set()
    assert capsys.readouterr().err == f"{startup_credentials.ADC_ERROR_MESSAGE}\n"


def test_lifespan_probes_adc_before_readiness(monkeypatch):
    """The app must not yield a ready lifespan before the ADC probe succeeds."""
    from backend import main

    events: list[str] = []
    settings = Settings(google_cloud_project="test-project")

    async def fake_probe():
        events.append("adc_probe")

    def fake_initialize(_settings):
        events.append("firebase")

    class FakeSessionManager:
        def __init__(self, _settings):
            events.append("session_manager")

        def detect_orphaned_sessions(self):
            return []

    class FakeStorage:
        def __init__(self, _settings):
            events.append("storage")

    class FakeGemini:
        def __init__(self, _settings):
            events.append("gemini")

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "probe_application_default_credentials", fake_probe)
    monkeypatch.setattr(main, "initialize_firebase_admin", fake_initialize)
    monkeypatch.setattr(main, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(main, "FirestoreStorage", FakeStorage)
    monkeypatch.setattr(main, "GCSStorage", FakeStorage)
    monkeypatch.setattr(main, "GeminiClient", FakeGemini)
    async def run_lifespan():
        async with main.lifespan(main.app):
            events.append("ready")

    asyncio.run(run_lifespan())

    assert events.index("adc_probe") < events.index("ready")
    assert events.index("adc_probe") < events.index("firebase")
