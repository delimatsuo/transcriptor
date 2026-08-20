"""Audio capture via sounddevice — supports any input device."""

from __future__ import annotations

import asyncio
from typing import Callable

import numpy as np
import sounddevice as sd
import structlog

from backend.config import Settings

logger = structlog.get_logger()


def find_input_device(device_name: str) -> int:
    """Find an input device index by name substring.

    Raises RuntimeError if not found.
    """
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if device_name.lower() in dev["name"].lower() and dev["max_input_channels"] > 0:
            logger.info(
                "input_device_found",
                device_index=i,
                device_name=dev["name"],
                max_input_channels=dev["max_input_channels"],
                default_samplerate=dev["default_samplerate"],
            )
            return i

    available = [
        f"  [{i}] {d['name']} (in={d['max_input_channels']})"
        for i, d in enumerate(devices)
        if d["max_input_channels"] > 0
    ]
    raise RuntimeError(
        f"Input device '{device_name}' not found.\n"
        f"Available input devices:\n" + "\n".join(available)
    )


def get_default_input_device() -> int:
    """Get the system default input device index."""
    default = sd.default.device[0]
    if default is None or default < 0:
        raise RuntimeError("No default input device found.")
    dev = sd.query_devices(default)
    logger.info(
        "default_input_device",
        device_index=default,
        device_name=dev["name"],
    )
    return int(default)


class AudioCapture:
    """Captures audio from an input device and pushes chunks to an async queue."""

    def __init__(
        self,
        settings: Settings,
        output_queue: asyncio.Queue,
        device_name: str = "",
        label: str = "audio",
        input_channel: int = 0,
    ) -> None:
        if input_channel < 0:
            raise ValueError("input_channel must be non-negative")
        self.settings = settings
        self.output_queue = output_queue
        self.device_name = device_name
        self.label = label
        self.input_channel = input_channel
        self._device_index: int | None = None
        self._stream: sd.InputStream | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def _find_device(self) -> int:
        if self.device_name:
            return find_input_device(self.device_name)
        return get_default_input_device()

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: dict,
        status: sd.CallbackFlags,
    ) -> None:
        """Called from the sounddevice audio thread — must be non-blocking."""
        if status:
            logger.warning("audio_callback_status", status=str(status), label=self.label)
        if self._running and self._loop is not None:
            # Copy data since the buffer is reused by sounddevice
            if indata.ndim > 1:
                if self.input_channel >= indata.shape[1]:
                    logger.error(
                        "audio_input_channel_unavailable",
                        label=self.label,
                        input_channel=self.input_channel,
                        available_channels=indata.shape[1],
                    )
                    return
                chunk = indata[:, self.input_channel].copy()
            else:
                if self.input_channel != 0:
                    logger.error(
                        "audio_input_channel_unavailable",
                        label=self.label,
                        input_channel=self.input_channel,
                        available_channels=1,
                    )
                    return
                chunk = indata.copy().flatten()
            self._loop.call_soon_threadsafe(self._enqueue, chunk)

    def _enqueue(self, chunk: np.ndarray) -> None:
        """Enqueue audio chunk from the main event loop thread."""
        try:
            self.output_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            # Drop oldest chunk on overflow
            try:
                self.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self.output_queue.put_nowait(chunk)
            except asyncio.QueueFull:
                logger.warning("audio_buffer_overflow", label=self.label)

    async def start(self) -> None:
        """Start capturing audio."""
        self._device_index = self._find_device()
        self._loop = asyncio.get_running_loop()
        self._running = True

        self._stream = sd.InputStream(
            device=self._device_index,
            samplerate=self.settings.sample_rate,
            channels=max(self.settings.channels, self.input_channel + 1),
            dtype="float32",
            blocksize=self.settings.chunk_size,
            callback=self._audio_callback,
        )
        self._stream.start()
        logger.info(
            "audio_capture_started",
            label=self.label,
            device=self._device_index,
            sample_rate=self.settings.sample_rate,
            chunk_size=self.settings.chunk_size,
            input_channel=self.input_channel,
        )

        # Verify device is producing non-silent audio (first 500ms)
        await self._verify_audio_signal()

    async def _verify_audio_signal(self) -> None:
        """Check that the device is producing audio (not all zeros) within 500ms."""
        chunks_to_check = max(1, int(500 / self.settings.audio_chunk_duration_ms))
        silent = True
        for _ in range(chunks_to_check):
            try:
                chunk = await asyncio.wait_for(self.output_queue.get(), timeout=1.0)
                if np.abs(chunk).max() > 1e-6:
                    silent = False
                    # Put it back for downstream consumers
                    try:
                        self.output_queue.put_nowait(chunk)
                    except asyncio.QueueFull:
                        pass
                    break
            except asyncio.TimeoutError:
                break

        if silent:
            logger.warning(
                "audio_device_silent",
                label=self.label,
                message=f"Device '{self.label}' is producing silence. "
                "Check your audio configuration.",
            )

    async def stop(self) -> None:
        """Stop capturing audio."""
        self._running = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        logger.info("audio_capture_stopped", label=self.label)
