"""Application configuration using pydantic-settings."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """T.A.R.S. configuration — loaded from environment / .env file."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # GCP
    google_cloud_project: str = Field(..., description="GCP project ID")

    # Authentication / internal tenancy
    firebase_project_id: str | None = Field(
        default=None,
        description="Firebase project ID; defaults to google_cloud_project",
    )
    auth_org_id: str = Field(
        default="ella-internal",
        description="Server-derived internal organization identifier",
    )
    auth_allowed_emails: str = Field(
        default="",
        description="Comma-separated exact email allowlist for internal access",
    )
    auth_ws_ticket_ttl_seconds: int = Field(default=60, gt=0, le=300)
    auth_stop_capability_ttl_seconds: int = Field(default=14_400, gt=0, le=86_400)
    auth_extension_capability_ttl_seconds: int = Field(default=900, gt=0, le=3600)
    extension_enabled: bool = Field(
        default=False,
        description="Explicit opt-in for the Chrome extension bridge",
    )

    # Model/provider guardrails.  Keep one bounded queue across all Gemini
    # features so concurrent sessions cannot fan out unbounded provider work.
    llm_max_concurrent_requests: int = Field(
        default=2,
        gt=0,
        le=8,
        description="Maximum concurrent Gemini requests in this backend process",
    )
    llm_location: str = Field(
        default="us-central1",
        min_length=1,
        max_length=64,
        description="Explicit Vertex AI region for Gemini requests",
    )
    llm_request_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=300,
        description="Deadline for each non-stream Gemini request",
    )
    llm_rolling_context_max_chars: int = Field(
        default=16_000,
        gt=0,
        le=50_000,
        description="Maximum transcript characters sent in each rolling summary update",
    )
    llm_rolling_failure_backoff_seconds: float = Field(
        default=30.0,
        gt=0,
        le=300,
        description="Initial cooldown after a rolling summary provider failure",
    )
    llm_rolling_failure_backoff_max_seconds: float = Field(
        default=300.0,
        gt=0,
        le=900,
        description="Maximum exponential cooldown after rolling summary failures",
    )
    llm_final_report_max_input_chars: int = Field(
        default=120_000,
        gt=0,
        le=500_000,
        description=(
            "Maximum durable context/transcript characters sent to final report generation"
        ),
    )

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
    stt_graceful_drain_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description=(
            "Maximum time to await final STT responses after closing audio input"
        ),
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
    audio_backup_enabled: bool = Field(
        default=False,
        description=(
            "Opt-in local FLAC crash-insurance recording. MUST stay False by "
            "default: spec 2026-08-03 §6 — no persistent raw audio."
        ),
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
