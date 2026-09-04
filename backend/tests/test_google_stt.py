import asyncio
import pytest
from backend.config import Settings
from backend.stt.google_stt import GoogleSTTStream


def test_chirp_streaming_config_does_not_request_unsupported_word_offsets():
    stream = GoogleSTTStream(
        Settings(google_cloud_project="test-project"),
        stream_id="stream-1",
    )

    config = stream._build_streaming_config()

    assert config.config.features.enable_word_time_offsets is False


def test_request_generator_yields_keepalive_silence_on_inactivity():
    settings = Settings(
        google_cloud_project="test-project",
        sample_rate=16000,
        channels=1,
        stt_keepalive_interval_seconds=0.01,
    )
    stream = GoogleSTTStream(settings, stream_id="stream-test")

    async def run():
        gen = stream._request_generator()

        # First item: config
        first = await anext(gen)
        assert first.streaming_config is not None

        # Next item should be keepalive silence (after 0.5s timeout > 0.01s interval)

        # Next item should be keepalive silence
        keepalive = await anext(gen)
        assert keepalive.audio is not None
        assert len(keepalive.audio) == 1600
        assert keepalive.audio == b"\x00" * 1600

        # Clean close
        await stream.stop()
        # Drain sentinel
        try:
            await anext(gen)
        except StopAsyncIteration:
            pass

    asyncio.run(run())

