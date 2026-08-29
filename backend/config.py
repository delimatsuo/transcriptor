"""Application configuration using pydantic-settings."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

DEFAULT_LOCAL_CORS_ORIGINS: list[str] = [
    "http://localhost:3000",
    "http://localhost:3003",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3003",
    "chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga",
]
IAP_FRONTEND_ORIGIN = "https://tars.ellaexecutivesearch.com"


def parse_cors_allowed_origins(raw: str | None) -> list[str]:
    """Parse, validate, and normalize CORS_ALLOWED_ORIGINS.

    If absent (None), returns DEFAULT_LOCAL_CORS_ORIGINS.
    If provided, splits comma-separated origins, trims surrounding spaces,
    removes a single trailing slash, validates strict ASCII origin syntax (no wildcards,
    no paths, no queries, no fragments, no credentials, no internal whitespace,
    no backslashes, no percent encoding, no control characters, valid host/ID format),
    deduplicates preserving order, and returns the resulting list.
    """
    if raw is None:
        return list(DEFAULT_LOCAL_CORS_ORIGINS)

    stripped = raw.strip()
    if not stripped:
        raise ValueError("CORS_ALLOWED_ORIGINS must not be blank")

    raw_items = [item.strip(" ") for item in raw.split(",")]
    parsed_origins: list[str] = []

    for item in raw_items:
        if not item:
            raise ValueError("Empty entry in CORS_ALLOWED_ORIGINS")

        try:
            item.encode("ascii")
        except UnicodeEncodeError:
            raise ValueError("Non-ASCII character in CORS origin") from None

        if "*" in item:
            raise ValueError("Wildcards are prohibited in CORS_ALLOWED_ORIGINS")
        if "\\" in item:
            raise ValueError("Backslash is prohibited in CORS origin")
        if "%" in item:
            raise ValueError("Percent encoding is prohibited in CORS origin")
        if "?" in item or "#" in item:
            raise ValueError("Query and fragment delimiters are prohibited in CORS origin")
        if any(c.isspace() for c in item):
            raise ValueError("Whitespace is prohibited within CORS origin")
        if any(ord(c) < 32 or ord(c) == 127 for c in item):
            raise ValueError("Control characters are prohibited in CORS origin")

        # Normalize only a single trailing slash
        if item.endswith("/"):
            item = item[:-1]

        if item.endswith(":"):
            raise ValueError("Empty port delimiter in CORS origin")

        try:
            split = urlsplit(item)
        except Exception:
            raise ValueError("Malformed CORS origin URI") from None

        if split.scheme not in ("http", "https", "chrome-extension"):
            raise ValueError("Invalid scheme in CORS origin")
        if split.query or split.fragment:
            raise ValueError("Query or fragment not allowed in CORS origin")
        if split.username is not None or split.password is not None or "@" in split.netloc:
            raise ValueError("Credentials/userinfo not allowed in CORS origin")
        if split.path and split.path != "":
            raise ValueError("Non-root path not allowed in CORS origin")
        if not split.hostname:
            raise ValueError("Missing host/extension ID in CORS origin")

        try:
            port = split.port
        except ValueError:
            raise ValueError("Invalid port in CORS origin") from None

        if port is not None and not (1 <= port <= 65535):
            raise ValueError("Port out of range in CORS origin")

        if split.scheme == "chrome-extension":
            if port is not None or ":" in split.netloc:
                raise ValueError("Port not allowed in chrome-extension origin")
            raw_id = split.netloc
            # Chrome extension ID: exactly 32 lowercase chars in 'a' through 'p'
            if len(raw_id) != 32 or not all("a" <= c <= "p" for c in raw_id):
                raise ValueError("Invalid chrome-extension ID in CORS origin")
            normalized_origin = f"chrome-extension://{raw_id}"
        else:
            # http or https
            if split.netloc.startswith("["):
                # Bracketed IPv6
                if "]" not in split.netloc:
                    raise ValueError("Malformed bracketed host in CORS origin")
                bracket_host = split.netloc[1:split.netloc.index("]")]
                try:
                    ipaddress.IPv6Address(bracket_host)
                except ValueError:
                    raise ValueError("Invalid IPv6 host in CORS origin") from None
                expected_prefix = f"[{bracket_host}]"
                if not (split.netloc == expected_prefix or split.netloc.startswith(f"{expected_prefix}:")):
                    raise ValueError("Malformed bracketed host in CORS origin")
            else:
                if "[" in split.netloc or "]" in split.netloc:
                    raise ValueError("Malformed host brackets in CORS origin")

                hostname = split.hostname
                is_ipv4 = False
                try:
                    ipaddress.IPv4Address(hostname)
                    is_ipv4 = True
                except ValueError:
                    pass

                if not is_ipv4:
                    labels = hostname.split(".")
                    for label in labels:
                        if not label or len(label) > 63:
                            raise ValueError("Invalid DNS label in CORS origin")
                        if not (label[0].isalnum() and label[-1].isalnum()):
                            raise ValueError("DNS label must start and end with alphanumeric")
                        if not all(c.isalnum() or c == "-" for c in label):
                            raise ValueError("DNS label contains invalid character")

            normalized_origin = f"{split.scheme}://{split.netloc}"

        if normalized_origin not in parsed_origins:
            parsed_origins.append(normalized_origin)

    return parsed_origins


def select_cors_allowed_origins(
    auth_mode: str = "firebase", raw: str | None = None
) -> list[str]:
    """Select CORS origins without weakening the hosted IAP boundary.

    Firebase/local behavior remains the existing configurable parser.  IAP
    mode is fixed to the approved App Hosting origin; a wildcard or any extra
    origin is a configuration error rather than a silently widened policy.
    """
    if auth_mode == "iap":
        if raw is not None and parse_cors_allowed_origins(raw) != [IAP_FRONTEND_ORIGIN]:
            raise ValueError("IAP mode CORS must contain only the approved frontend origin")
        return [IAP_FRONTEND_ORIGIN]
    return parse_cors_allowed_origins(raw)


class CorsSettings(BaseSettings):
    """Isolated settings reader for CORS configuration without full Settings requirements."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    cors_allowed_origins: str | None = Field(
        default=None,
        description="Optional comma-separated list of allowed CORS origins",
    )
    auth_mode: str = Field(default="firebase")


class Settings(BaseSettings):
    """T.A.R.S. configuration — loaded from environment / .env file."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # GCP
    google_cloud_project: str = Field(..., description="GCP project ID")
    gcs_bucket_name: str | None = Field(
        default=None,
        description="Optional override for GCS bucket name",
    )

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

    # Task 08 private preproduction authentication.  These values are
    # intentionally optional in Firebase mode so existing local/dev startup
    # remains backward-compatible.
    auth_mode: str = Field(default="firebase", description="Authentication mode: firebase or iap")
    auth_iap_audience: str | None = Field(
        default=None,
        description="Exact Cloud Run IAP audience path",
    )
    auth_iap_frontend_origin: str | None = Field(
        default=None,
        description="Exact App Hosting frontend origin for IAP bootstrap",
    )
    auth_iap_ws_max_lifetime_seconds: int = Field(
        default=3300,
        ge=1,
        le=3300,
        description="Absolute browser WebSocket lease lifetime",
    )
    auth_task08_operator_emails: str = Field(
        default="",
        description="Comma-separated IAP operator subset of auth_allowed_emails",
    )
    auth_kill_switch: bool = Field(
        default=False,
        description="Monotonic process-local emergency admission latch",
    )

    @model_validator(mode="after")
    def validate_task08_iap_configuration(self) -> "Settings":
        if self.auth_mode not in {"firebase", "iap"}:
            raise ValueError("AUTH_MODE must be exactly firebase or iap")
        if self.auth_mode != "iap":
            return self

        if self.auth_bypass:
            raise ValueError("AUTH_BYPASS is incompatible with AUTH_MODE=iap")
        if self.auth_org_id != "ella-internal":
            raise ValueError("AUTH_ORG_ID must be exactly ella-internal in IAP mode")

        audience = (self.auth_iap_audience or "").strip()
        if not re.fullmatch(
            r"/projects/[0-9]+/locations/[a-z0-9-]+/services/[a-z0-9](?:[a-z0-9-]*[a-z0-9])?",
            audience,
        ):
            raise ValueError("AUTH_IAP_AUDIENCE must be a Cloud Run IAP audience path")
        self.auth_iap_audience = audience

        origin = (self.auth_iap_frontend_origin or "").strip()
        if origin != "https://tars.ellaexecutivesearch.com":
            raise ValueError(
                "AUTH_IAP_FRONTEND_ORIGIN must be the approved HTTPS frontend origin"
            )
        self.auth_iap_frontend_origin = origin

        admitted = self._strict_email_values(self.auth_allowed_emails, "AUTH_ALLOWED_EMAILS")
        operators = self._strict_email_values(
            self.auth_task08_operator_emails,
            "AUTH_TASK08_OPERATOR_EMAILS",
        )
        if not operators:
            raise ValueError("AUTH_TASK08_OPERATOR_EMAILS must not be empty in IAP mode")
        if not operators.issubset(admitted):
            raise ValueError(
                "AUTH_TASK08_OPERATOR_EMAILS must be a subset of AUTH_ALLOWED_EMAILS"
            )
        # The hosted packet is bound to exactly five unique corporate
        # identities.  Do not let duplicate normalization silently reduce the
        # independently reviewed admission set.
        raw_admitted = [item.strip().casefold() for item in self.auth_allowed_emails.split(",")]
        if len(raw_admitted) != 5 or len(admitted) != 5:
            raise ValueError("AUTH_ALLOWED_EMAILS must contain exactly five unique addresses")
        if any(address.rsplit("@", 1)[-1] != "ellaexecutivesearch.com" for address in admitted):
            raise ValueError("AUTH_ALLOWED_EMAILS must use the ellaexecutivesearch.com domain")
        return self

    @staticmethod
    def _email_values(raw: str) -> set[str]:
        return {item.strip().casefold() for item in raw.split(",") if item.strip()}

    @staticmethod
    def _strict_email_values(raw: str, label: str) -> set[str]:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{label} must not be blank in IAP mode")
        values: set[str] = set()
        for item in raw.split(","):
            value = item.strip()
            if (
                not value
                or any(character.isspace() for character in value)
                or not value.isascii()
            ):
                raise ValueError(f"{label} contains a malformed email")
            value = value.casefold()
            if not re.fullmatch(
                r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+",
                value,
            ):
                raise ValueError(f"{label} contains a malformed email")
            values.add(value)
        return values

    @property
    def admitted_email_set(self) -> set[str]:
        return self._email_values(self.auth_allowed_emails)

    @property
    def task08_operator_email_set(self) -> set[str]:
        return self._email_values(self.auth_task08_operator_emails)

    @field_validator("gcs_bucket_name")
    @classmethod
    def validate_gcs_bucket_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if any(c.isspace() for c in normalized) or "/" in normalized or normalized.startswith("gs://"):
            raise ValueError("GCS_BUCKET_NAME must be a bare bucket name (no gs://, no slashes, no whitespace)")
        return normalized

    @property
    def effective_gcs_bucket_name(self) -> str:
        if self.gcs_bucket_name:
            return self.gcs_bucket_name.strip()
        return f"{self.google_cloud_project}-tars"
    auth_stop_capability_ttl_seconds: int = Field(default=14_400, gt=0, le=86_400)
    auth_extension_capability_ttl_seconds: int = Field(default=900, gt=0, le=3600)
    extension_enabled: bool = Field(
        default=False,
        description="Explicit opt-in for the Chrome extension bridge",
    )
    auth_bypass: bool = Field(
        default=False,
        description="Local development opt-in to admit local recruiter without Firebase",
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
        description="Deadline for every Gemini request, including streaming",
    )
    llm_max_input_chars: int = Field(
        default=120_000,
        gt=0,
        le=500_000,
        description=(
            "Hard ceiling for any Gemini user message before provider invocation"
        ),
    )
    llm_max_output_tokens: int = Field(
        default=8_192,
        gt=0,
        le=32_768,
        description=(
            "Hard ceiling for any Gemini output budget before provider invocation"
        ),
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
        le=120_000,
        description=(
            "Maximum durable context/transcript characters sent to final report generation"
        ),
    )

    @field_validator("llm_location")
    @classmethod
    def validate_llm_location(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("LLM_LOCATION must not be blank")
        if normalized.lower() == "global":
            raise ValueError("LLM_LOCATION=global is prohibited by the privacy policy")
        return normalized

    # Audio
    blackhole_device_name: str = Field(
        default="BlackHole 2ch",
        description="Name substring used to find the BlackHole virtual audio device",
    )
    microphone_device_name: str = Field(
        default="",
        description="Name substring for the microphone device. Empty = system default mic.",
    )
    microphone_input_channel: int = Field(
        default=0,
        ge=0,
        description=(
            "Zero-based input channel to capture from the microphone device; "
            "Vocaster One Host Microphone is channel 4."
        ),
    )
    sample_rate: int = Field(default=16000, description="Audio sample rate in Hz")
    channels: int = Field(default=1, description="Audio channels (mono)")
    audio_chunk_duration_ms: int = Field(
        default=100, description="Duration of each audio chunk in milliseconds"
    )
    audio_buffer_max_seconds: int = Field(
        default=30, description="Max seconds of audio to buffer before dropping"
    )
    host_audio_capture_enabled: bool = Field(
        default=False,
        description=(
            "Enable server-host PyAudio capture on the backend process (legacy mode). "
            "Disabled by default when using companion or browser Web Audio streaming."
        ),
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
