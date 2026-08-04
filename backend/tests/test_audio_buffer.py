"""FLAC backup must be opt-in (spec §6: no persistent raw audio)."""

import asyncio

import numpy as np

from backend.audio.buffer import AudioBuffer
from backend.config import Settings


def make_settings(tmp_path, **overrides) -> Settings:
    return Settings(
        google_cloud_project="test-project",
        audio_backup_dir=str(tmp_path / "recordings"),
        **overrides,
    )


def test_backup_disabled_by_default_writes_no_file(tmp_path):
    settings = make_settings(tmp_path)
    assert settings.audio_backup_enabled is False

    async def run():
        queue: asyncio.Queue = asyncio.Queue()
        buf = AudioBuffer(settings, queue)
        await buf.start()
        await queue.put(np.zeros(1600, dtype=np.float32))
        chunk = await anext(buf.chunks())
        await buf.stop()
        return chunk

    chunk = asyncio.run(run())
    assert chunk.shape == (1600,)  # audio still flows
    assert not (tmp_path / "recordings").exists()  # nothing touched disk


def test_backup_opt_in_still_works(tmp_path):
    settings = make_settings(tmp_path, audio_backup_enabled=True)

    async def run():
        queue: asyncio.Queue = asyncio.Queue()
        buf = AudioBuffer(settings, queue)
        await buf.start()
        await queue.put(np.zeros(1600, dtype=np.float32))
        await anext(buf.chunks())
        await buf.stop()
        return buf.backup_path

    path = asyncio.run(run())
    assert path is not None and path.exists()
