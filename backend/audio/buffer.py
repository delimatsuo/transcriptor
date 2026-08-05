"""Async audio buffer with local FLAC backup."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import numpy as np
import soundfile as sf
import structlog

from backend.config import Settings

logger = structlog.get_logger()


class AudioBuffer:
    """Async buffer between audio capture and STT sender, with optional FLAC backup."""

    def __init__(self, settings: Settings, input_queue: asyncio.Queue) -> None:
        self.settings = settings
        self.input_queue = input_queue
        self._backup_file: sf.SoundFile | None = None
        self._backup_path: Path | None = None
        self._running = False

    def _open_backup_file(self) -> None:
        """Open a local FLAC file for crash-insurance recording."""
        backup_dir = Path(self.settings.audio_backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self._backup_path = backup_dir / f"session_{timestamp}.flac"
        self._backup_file = sf.SoundFile(
            str(self._backup_path),
            mode="w",
            samplerate=self.settings.sample_rate,
            channels=self.settings.channels,
            format="FLAC",
        )
        logger.info("audio_backup_started", path=str(self._backup_path))

    def _close_backup_file(self) -> None:
        if self._backup_file is not None:
            self._backup_file.close()
            self._backup_file = None
            logger.info("audio_backup_closed", path=str(self._backup_path))

    async def start(self) -> None:
        """Open the backup file only when explicitly enabled (dev opt-in)."""
        if self.settings.audio_backup_enabled:
            self._open_backup_file()
        self._running = True

    async def stop(self) -> None:
        """Close the backup file."""
        self._running = False
        self._close_backup_file()

    @property
    def backup_path(self) -> Path | None:
        return self._backup_path

    def _record_chunk(self, chunk: np.ndarray) -> None:
        """Write a chunk to the opt-in backup when enabled."""
        if self._backup_file is not None:
            try:
                self._backup_file.write(chunk)
            except Exception:
                logger.exception("audio_backup_write_error")

    def drain_pending_chunks(self) -> list[np.ndarray]:
        """Remove and return every queued chunk without losing backup coverage."""
        drained: list[np.ndarray] = []
        while True:
            try:
                chunk = self.input_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._record_chunk(chunk)
            drained.append(chunk)
        return drained

    async def chunks(self) -> AsyncIterator[np.ndarray]:
        """Yield audio chunks from the input queue.

        Each chunk is also written to the local backup file when enabled.
        """
        while self._running:
            try:
                chunk = await asyncio.wait_for(self.input_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            # Write to backup (always, even during silence)
            self._record_chunk(chunk)

            yield chunk

    def float32_to_int16(self, audio: np.ndarray) -> bytes:
        """Convert float32 audio to int16 LINEAR16 bytes for STT."""
        int16_audio = (audio * 32767).astype(np.int16)
        return int16_audio.tobytes()
