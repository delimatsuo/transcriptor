"""Application configuration using pydantic-settings."""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit
from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings


class AuthConfigurationError(ValueError):
    """Raised when authentication or environment configuration fails validation."""


DEFAULT_LOCAL_CORS_ORIGINS: list[str] = [
    "http://localhost:3000",
    "http://localhost:3003",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3003",
    "chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga",
]


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


class CorsSettings(BaseSettings):
    """Isolated settings reader for CORS configuration without full Settings requirements."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    cors_allowed_origins: str | None = Field(
        default=None,
        description="Optional comma-separated list of allowed CORS origins",
    )


class Settings(BaseSettings):
    """T.A.R.S. configuration — loaded from environment / .env file."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # Runtime mode
    tars_runtime_mode: Literal["local", "hosted-pilot"] = Field(
        default="local",
        description="Runtime execution mode: local or hosted-pilot",
    )

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

    @field_validator("google_cloud_project")
    @classmethod
    def validate_google_cloud_project(cls, value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", value):
            raise ValueError("GOOGLE_CLOUD_PROJECT must match [a-z][a-z0-9-]{4,28}[a-z0-9] (6-30 chars)")
        return value

    @field_validator("firebase_project_id")
    @classmethod
    def validate_firebase_project_id(cls, value: str | None) -> str | None:
        if value is not None:
            if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", value):
                raise ValueError("FIREBASE_PROJECT_ID must match [a-z][a-z0-9-]{4,28}[a-z0-9] (6-30 chars)")
        return value

    @field_validator("auth_org_id")
    @classmethod
    def validate_auth_org_id(cls, value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,61}[a-z0-9]", value):
            raise ValueError("AUTH_ORG_ID must match [a-z][a-z0-9-]{1,61}[a-z0-9] (3-63 chars)")
        return value

    @field_validator("auth_bypass", mode="before")
    @classmethod
    def validate_auth_bypass(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            if value == "true":
                return True
            if value == "false":
                return False
        raise ValueError("AUTH_BYPASS must be exact canonical 'true', 'false', or a boolean literal")

    @field_validator("tars_runtime_mode", mode="before")
    @classmethod
    def validate_runtime_mode_canonical(cls, value: Any) -> str:
        if isinstance(value, str):
            if value in ("local", "hosted-pilot"):
                return value
        raise ValueError("TARS_RUNTIME_MODE must be exact 'local' or 'hosted-pilot'")

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
    llm_model_name: str = Field(
        default="gemini-2.5-flash",
        min_length=1,
        max_length=64,
        description="Vertex AI model name for generative tasks (e.g. gemini-2.5-flash, gemini-3.8-flash)",
    )
    llm_allow_global: bool = Field(
        default=False,
        description="Explicit opt-in to permit LLM_LOCATION=global for models only served globally (e.g. Gemini 3.8 Flash)",
    )
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

    @field_validator("llm_model_name")
    @classmethod
    def validate_llm_model_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("LLM_MODEL_NAME must not be blank")
        return normalized

    @field_validator("llm_location")
    @classmethod
    def validate_llm_location(cls, value: str, info: ValidationInfo) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("LLM_LOCATION must not be blank")
        allow_global = False
        if info and info.data:
            allow_global = bool(info.data.get("llm_allow_global", False))
        if normalized.lower() == "global" and not allow_global:
            raise ValueError(
                "LLM_LOCATION=global is prohibited by the privacy policy unless LLM_ALLOW_GLOBAL=true"
            )
        return normalized

    # Workable ATS Integration
    workable_subdomain: str | None = Field(
        default=None,
        description="Workable company subdomain (e.g. 'acme' in acme.workable.com)",
    )
    workable_api_key: str | None = Field(
        default=None,
        description="Workable API partner access token",
    )
    calendar_ical_url: str | None = Field(
        default=None,
        description="Optional Google Calendar or Outlook secret iCal feed URL for automated interview detection",
    )

    @field_validator("workable_subdomain")
    @classmethod
    def validate_workable_subdomain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            return None
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", normalized):
            raise ValueError("WORKABLE_SUBDOMAIN must be valid subdomain alphanumeric with hyphens")
        return normalized

    @field_validator("workable_api_key")
    @classmethod
    def validate_workable_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

    @property
    def has_workable_integration(self) -> bool:
        return bool(self.workable_subdomain and self.workable_api_key)

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
    stt_keepalive_interval_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Interval between silence keepalive frames to keep STT gRPC stream open",
    )

    # Server
    fastapi_host: str = Field(default="127.0.0.1")
    fastapi_port: int = Field(default=8008)

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


def resolve_settings_safely() -> Settings:
    """Resolve Settings behind a content-free error boundary."""
    success = False
    resolved: Settings | None = None
    try:
        resolved = get_settings()
        success = True
    except Exception:
        success = False
        resolved = None

    if not success or resolved is None:
        raise AuthConfigurationError("Configuration validation failed during settings resolution") from None
    return resolved


def resolve_cors_settings_safely(secondary_source: dict[str, Any] | None = None) -> CorsSettings:
    """Resolve CorsSettings behind a content-free error boundary."""
    success = False
    resolved: CorsSettings | None = None
    try:
        if secondary_source is not None:
            resolved = CorsSettings(_env_file=None, **secondary_source)
        else:
            resolved = CorsSettings()
        success = True
    except Exception:
        success = False
        resolved = None

    if not success or resolved is None:
        raise AuthConfigurationError("Configuration validation failed during CORS settings resolution") from None
    return resolved


PROTECTED_LOGICAL_KEYS = (
    "TARS_RUNTIME_MODE",
    "AUTH_BYPASS",
    "GOOGLE_CLOUD_PROJECT",
    "FIREBASE_PROJECT_ID",
    "AUTH_ORG_ID",
    "AUTH_ALLOWED_EMAILS",
)


def validate_raw_process_env(
    raw_env: Mapping[str, str],
    resolved_settings: Settings | None = None,
) -> Literal["local", "hosted-pilot"]:
    """Pure validator for raw process environment and raw-vs-resolved configuration matrix."""
    key_occurrences: dict[str, list[tuple[str, str]]] = {}
    for k, v in raw_env.items():
        k_upper = k.upper()
        if k_upper in PROTECTED_LOGICAL_KEYS or k_upper == "K_SERVICE":
            key_occurrences.setdefault(k_upper, []).append((k, v))

    # Check for case collisions across protected keys
    for protected_key in PROTECTED_LOGICAL_KEYS:
        occurrences = key_occurrences.get(protected_key, [])
        if len(occurrences) > 1:
            raise AuthConfigurationError(f"Duplicate or case-colliding environment key detected for logical key: {protected_key}")
        if len(occurrences) == 1:
            raw_key, _ = occurrences[0]
            if raw_key != protected_key:
                raise AuthConfigurationError(f"Protected environment key must have exact uppercase spelling: {protected_key}")

    k_service_occs = key_occurrences.get("K_SERVICE", [])
    if len(k_service_occs) > 1:
        raise AuthConfigurationError("Duplicate or case-colliding environment key detected for logical key: K_SERVICE")
    has_k_service = False
    if len(k_service_occs) == 1:
        raw_k, raw_v = k_service_occs[0]
        if raw_k != "K_SERVICE":
            raise AuthConfigurationError("Protected environment key must have exact uppercase spelling: K_SERVICE")
        has_k_service = len(raw_v) > 0

    mode_occ = key_occurrences.get("TARS_RUNTIME_MODE", [])
    raw_mode = mode_occ[0][1] if mode_occ else None

    # Hosted determination: K_SERVICE non-empty by length or raw_mode == "hosted-pilot"
    is_hosted = has_k_service or (raw_mode == "hosted-pilot")

    if is_hosted:
        if not mode_occ:
            raise AuthConfigurationError("TARS_RUNTIME_MODE=hosted-pilot required in hosted environment")
        if mode_occ[0][0] != "TARS_RUNTIME_MODE" or mode_occ[0][1] != "hosted-pilot":
            raise AuthConfigurationError("TARS_RUNTIME_MODE must be exact 'hosted-pilot' in hosted environment")

        # In hosted mode, require all 6 protected keys with exact uppercase spelling
        for req_key in PROTECTED_LOGICAL_KEYS:
            occ = key_occurrences.get(req_key, [])
            if not occ:
                raise AuthConfigurationError(f"Missing required protected environment key in hosted mode: {req_key}")
            if occ[0][0] != req_key:
                raise AuthConfigurationError(f"Protected key must be exact uppercase in hosted mode: {req_key}")

        # AUTH_BYPASS must be exact "false"
        bypass_occ = key_occurrences.get("AUTH_BYPASS", [])
        if not bypass_occ or bypass_occ[0][1] != "false":
            raise AuthConfigurationError("AUTH_BYPASS must be exact 'false' in hosted-pilot mode")

        if resolved_settings is not None:
            if resolved_settings.tars_runtime_mode != "hosted-pilot":
                raise AuthConfigurationError("Resolved settings runtime mode mismatch with hosted raw environment")
            if resolved_settings.auth_bypass is not False:
                raise AuthConfigurationError("Resolved settings auth_bypass must be False in hosted mode")
            for req_key in PROTECTED_LOGICAL_KEYS:
                raw_v = key_occurrences[req_key][0][1]
                if req_key == "TARS_RUNTIME_MODE" and resolved_settings.tars_runtime_mode != raw_v:
                    raise AuthConfigurationError("Resolved TARS_RUNTIME_MODE does not match raw environment")
                if req_key == "GOOGLE_CLOUD_PROJECT" and resolved_settings.google_cloud_project != raw_v:
                    raise AuthConfigurationError("Resolved GOOGLE_CLOUD_PROJECT does not match raw environment")
                if req_key == "FIREBASE_PROJECT_ID" and resolved_settings.firebase_project_id != raw_v:
                    raise AuthConfigurationError("Resolved FIREBASE_PROJECT_ID does not match raw environment")
                if req_key == "AUTH_ORG_ID" and resolved_settings.auth_org_id != raw_v:
                    raise AuthConfigurationError("Resolved AUTH_ORG_ID does not match raw environment")
                if req_key == "AUTH_ALLOWED_EMAILS" and resolved_settings.auth_allowed_emails != raw_v:
                    raise AuthConfigurationError("Resolved AUTH_ALLOWED_EMAILS does not match raw environment")

        return "hosted-pilot"
    else:
        if has_k_service:
            raise AuthConfigurationError("K_SERVICE is set but runtime mode is not hosted-pilot")
        if mode_occ and mode_occ[0][1] not in ("local",):
            raise AuthConfigurationError("Invalid TARS_RUNTIME_MODE for local runtime")

        if resolved_settings is not None:
            if resolved_settings.tars_runtime_mode == "hosted-pilot":
                raise AuthConfigurationError("Hosted runtime mode cannot be resolved from secondary source when raw environment is local or absent")

            for protected_key in PROTECTED_LOGICAL_KEYS:
                if protected_key in key_occurrences:
                    raw_v = key_occurrences[protected_key][0][1]
                    if protected_key == "AUTH_BYPASS":
                        expected_bool = True if raw_v == "true" else (False if raw_v == "false" else None)
                        if expected_bool is None or resolved_settings.auth_bypass != expected_bool:
                            raise AuthConfigurationError("Resolved AUTH_BYPASS does not match raw environment")
                    elif protected_key == "TARS_RUNTIME_MODE" and resolved_settings.tars_runtime_mode != raw_v:
                        raise AuthConfigurationError("Resolved TARS_RUNTIME_MODE does not match raw environment")
                    elif protected_key == "GOOGLE_CLOUD_PROJECT" and resolved_settings.google_cloud_project != raw_v:
                        raise AuthConfigurationError("Resolved GOOGLE_CLOUD_PROJECT does not match raw environment")
                    elif protected_key == "FIREBASE_PROJECT_ID" and resolved_settings.firebase_project_id != raw_v:
                        raise AuthConfigurationError("Resolved FIREBASE_PROJECT_ID does not match raw environment")
                    elif protected_key == "AUTH_ORG_ID" and resolved_settings.auth_org_id != raw_v:
                        raise AuthConfigurationError("Resolved AUTH_ORG_ID does not match raw environment")
                    elif protected_key == "AUTH_ALLOWED_EMAILS" and resolved_settings.auth_allowed_emails != raw_v:
                        raise AuthConfigurationError("Resolved AUTH_ALLOWED_EMAILS does not match raw environment")

        return "local"
