"""Configuration tests for Google Speech-to-Text streaming."""

from backend.config import Settings
from backend.stt.google_stt import GoogleSTTStream


def test_chirp_streaming_config_does_not_request_unsupported_word_offsets():
    stream = GoogleSTTStream(
        Settings(google_cloud_project="test-project"),
        stream_id="stream-1",
    )

    config = stream._build_streaming_config()

    assert config.config.features.enable_word_time_offsets is False
