"""Hardware-free tests for configured microphone channel selection."""

import asyncio

import numpy as np

from backend.audio.capture import AudioCapture
from backend.config import Settings


def test_audio_callback_selects_configured_input_channel(monkeypatch):
    queue: asyncio.Queue = asyncio.Queue()
    capture = AudioCapture(
        Settings(google_cloud_project="test-project"),
        queue,
        input_channel=4,
    )
    capture._running = True
    capture._loop = asyncio.new_event_loop()
    selected: list[np.ndarray] = []
    monkeypatch.setattr(capture, "_enqueue", selected.append)
    monkeypatch.setattr(capture._loop, "call_soon_threadsafe", lambda callback, chunk: callback(chunk))

    frames = np.arange(15, dtype=np.float32).reshape(3, 5)
    capture._audio_callback(frames, 3, {}, None)

    np.testing.assert_array_equal(selected, [np.array([4.0, 9.0, 14.0], dtype=np.float32)])


def test_audio_callback_rejects_unavailable_input_channel(monkeypatch):
    capture = AudioCapture(
        Settings(google_cloud_project="test-project"),
        asyncio.Queue(),
        input_channel=4,
    )
    capture._running = True
    capture._loop = asyncio.new_event_loop()
    enqueue = []
    monkeypatch.setattr(capture, "_enqueue", enqueue.append)
    monkeypatch.setattr(capture._loop, "call_soon_threadsafe", lambda callback, chunk: callback(chunk))

    capture._audio_callback(np.zeros((3, 2), dtype=np.float32), 3, {}, None)

    assert enqueue == []
