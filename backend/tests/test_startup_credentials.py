"""The backend must fail loudly instead of hanging on expired ADC."""

import asyncio
import re
import threading
import time
from unittest.mock import Mock

import pytest
from google.auth.exceptions import RefreshError

from backend import startup_credentials


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
