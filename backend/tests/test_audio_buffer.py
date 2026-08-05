"""FLAC backup must be opt-in (spec §6: no persistent raw audio)."""

import asyncio

import numpy as np
import soundfile as sf

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


def test_drain_pending_chunks_preserves_order(tmp_path):
    settings = make_settings(tmp_path)

    async def run():
        queue: asyncio.Queue = asyncio.Queue()
        first = np.full(4, 0.1, dtype=np.float32)
        second = np.full(4, 0.2, dtype=np.float32)
        await queue.put(first)
        await queue.put(second)
        buf = AudioBuffer(settings, queue)
        await buf.start()
        drained = buf.drain_pending_chunks()
        await buf.stop()
        return drained, queue

    drained, queue = asyncio.run(run())

    assert len(drained) == 2
    np.testing.assert_array_equal(drained[0], np.full(4, 0.1, dtype=np.float32))
    np.testing.assert_array_equal(drained[1], np.full(4, 0.2, dtype=np.float32))
    assert queue.empty()


def test_drain_pending_chunks_keeps_opt_in_backup_complete(tmp_path):
    settings = make_settings(tmp_path, audio_backup_enabled=True)
    pending = np.full(160, 0.25, dtype=np.float32)

    async def run():
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(pending)
        buf = AudioBuffer(settings, queue)
        await buf.start()
        drained = buf.drain_pending_chunks()
        await buf.stop()
        return drained, buf.backup_path

    drained, path = asyncio.run(run())

    assert len(drained) == 1
    assert path is not None
    recorded, sample_rate = sf.read(path, dtype="float32")
    assert sample_rate == settings.sample_rate
    np.testing.assert_allclose(recorded, pending, atol=1e-4)
