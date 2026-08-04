"""Application configuration using pydantic-settings."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """T.A.R.S. configuration — loaded from environment / .env file."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # GCP
    google_cloud_project: str = Field(..., description="GCP project ID")

    # Audio
    blackhole_device_name: str = Field(
        default="BlackHole 2ch",
        description="Name substring used to find the BlackHole virtual audio device",
    )
    microphone_device_name: str = Field(
        default="",
        description="Name substring for the microphone device. Empty = system default mic.",
    )
    sample_rate: int = Field(default=16000, description="Audio sample rate in Hz")
    channels: int = Field(default=1, description="Audio channels (mono)")
    audio_chunk_duration_ms: int = Field(
        default=100, description="Duration of each audio chunk in milliseconds"
    )
    audio_buffer_max_seconds: int = Field(
        default=30, description="Max seconds of audio to buffer before dropping"
    )

    # STT
    stt_language_code: str = Field(
        default="pt-BR", description="BCP-47 language code for STT"
    )
    stt_model: str = Field(
        default="chirp_3", description="Google STT v2 model name"
    )
    stt_location: str = Field(
        default="us", description="Google STT v2 region (chirp_3 requires 'us' or 'eu', chirp_2 uses 'global')"
    )
    stt_speaker_label_self: str = Field(
        default="Entrevistador", description="Label for the user's own voice"
    )
    stt_speaker_label_other: str = Field(
        default="Candidato", description="Label for the other participant's voice"
    )
    stt_stream_max_duration_seconds: int = Field(
        default=270,  # 4:30
        description="Max duration per STT stream before rotation",
    )
    stt_stream_overlap_seconds: int = Field(
        default=5, description="Overlap between old and new STT streams"
    )
    stt_min_speaker_count: int = Field(
        default=2, description="Min expected speakers for diarization"
    )
    stt_max_speaker_count: int = Field(
        default=6, description="Max expected speakers for diarization"
    )

    # Server
    fastapi_host: str = Field(default="127.0.0.1")
    fastapi_port: int = Field(default=8000)

    # Session
    session_max_duration_minutes: int = Field(default=180)
    data_retention_days: int = Field(default=90)

    # Audio backup
    audio_backup_dir: str = Field(
        default="recordings", description="Directory for local audio backup files"
    )

    @property
    def chunk_size(self) -> int:
        """Number of samples per audio chunk."""
        return int(self.sample_rate * self.audio_chunk_duration_ms / 1000)

    @property
    def buffer_max_chunks(self) -> int:
        """Max number of chunks in the audio buffer."""
        return int(self.audio_buffer_max_seconds * 1000 / self.audio_chunk_duration_ms)


def get_settings() -> Settings:
    """Create and return a Settings instance."""
    return Settings()
