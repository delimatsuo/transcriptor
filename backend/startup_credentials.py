"""Fail-fast validation for Google Application Default Credentials."""

from __future__ import annotations

import asyncio
import sys
import threading

import google.auth
from google.auth.transport.requests import Request


ADC_ERROR_MESSAGE = (
    "ADC expirado — rode: gcloud auth application-default login"
)
ADC_PROBE_TIMEOUT_SECONDS = 10.0


class ADCStartupError(RuntimeError):
    """Raised when Application Default Credentials cannot be refreshed."""


def _refresh_application_default_credentials() -> None:
    credentials, _ = google.auth.default()
    credentials.refresh(Request())


def _finish_probe(
    future: asyncio.Future[None], error: Exception | None
) -> None:
    if future.done():
        return
    if error is None:
        future.set_result(None)
    else:
        future.set_exception(error)


async def probe_application_default_credentials(
    timeout_seconds: float = ADC_PROBE_TIMEOUT_SECONDS,
) -> None:
    """Refresh ADC before startup, with a deadline that a stuck refresh cannot defeat."""
    loop = asyncio.get_running_loop()
    completion: asyncio.Future[None] = loop.create_future()

    def refresh_in_background() -> None:
        error: Exception | None = None
        try:
            _refresh_application_default_credentials()
        except Exception as exc:  # surfaced below as one actionable startup error
            error = exc

        try:
            loop.call_soon_threadsafe(_finish_probe, completion, error)
        except RuntimeError:
            # The process may already be exiting after a timed-out startup probe.
            pass

    threading.Thread(
        target=refresh_in_background,
        name="adc-startup-probe",
        daemon=True,
    ).start()

    try:
        await asyncio.wait_for(completion, timeout=timeout_seconds)
    except Exception:
        print(ADC_ERROR_MESSAGE, file=sys.stderr, flush=True)
        raise ADCStartupError(ADC_ERROR_MESSAGE) from None
