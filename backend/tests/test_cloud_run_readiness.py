"""Unit tests for Cloud Run pilot source readiness, CORS configuration,
readiness probes, bucket binding, container contract, and security invariants."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
import shlex
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from fastapi import status

from backend import config, main


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# --- Isolation Fixture ---

@pytest.fixture(autouse=True)
def _isolate_readiness_globals():
    orig_settings = main.settings
    orig_session_mgr = main.session_mgr
    orig_firestore = main.firestore_storage
    orig_gcs = main.gcs_storage
    orig_gemini = main.gemini_client
    orig_cw = main.context_window
    orig_cws = dict(main.context_windows)
    orig_ready = getattr(main.app.state, "ready", False)

    main.app.state.ready = False

    yield

    main.settings = orig_settings
    main.session_mgr = orig_session_mgr
    main.firestore_storage = orig_firestore
    main.gcs_storage = orig_gcs
    main.gemini_client = orig_gemini
    main.context_window = orig_cw
    main.context_windows.clear()
    main.context_windows.update(orig_cws)
    main.app.state.ready = orig_ready


# --- Section 1 / 3: CORS configuration ---

def test_cors_absent_returns_exact_default_local_origins():
    origins = config.parse_cors_allowed_origins(None)
    assert origins == [
        "http://localhost:3000",
        "http://localhost:3003",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3003",
        "chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga",
    ]


def test_cors_custom_origins_parsed_normalized_and_deduplicated():
    raw = (
        "https://app.example.com/, https://admin.example.com, https://app.example.com, "
        "chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga, http://192.168.1.1:8080, http://[::1]:3000"
    )
    origins = config.parse_cors_allowed_origins(raw)
    assert origins == [
        "https://app.example.com",
        "https://admin.example.com",
        "chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga",
        "http://192.168.1.1:8080",
        "http://[::1]:3000",
    ]


@pytest.mark.parametrize(
    "invalid_raw",
    [
        "",
        "   ",
        "*",
        "https://*.example.com",
        "https://user:pass@example.com",
        "http://@",
        "http://:80",
        "http://example.com:bad",
        "http://example.com:99999",
        "http://exa mple.com",
        "http://example.com\\evil",
        "chrome-extension://id:99",
        "chrome-extension://short",
        "chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga:8080",
        "chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjngz",  # 'z' not in a-p
        "https://example.com/path/not/root",
        "https://example.com?query=1",
        "https://example.com#fragment",
        "ftp://example.com",
        "http://",
        "not-a-url",
        "http://example.com\t",
        "http://example.com\n",
        "http://example.com\x00",
        "http://example.com\x7f",
        "https://example.com?",
        "https://example.com#",
        "https://example.com?#",
        "http://example.com:",
        "https://example.com:",
        "http://[::1]:",
        "chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga:",
        "chrome-extension://FHNADCDKFGDLKOMJPILMGEHHPGMKJNGA",
        "https://\u212a.example",
        "http://[v1.foo]",
        "http://[::1%25lo0]",
        "chrome-extension://" + "a" * 31 + "\u212a",
    ],
)
def test_cors_invalid_raw_fails_closed(invalid_raw):
    with pytest.raises(ValueError):
        config.parse_cors_allowed_origins(invalid_raw)


def test_cors_userinfo_rejection_is_content_free():
    with pytest.raises(ValueError) as exc_info:
        config.parse_cors_allowed_origins("https://alice:TASK06_TOPSECRET@example.com")
    error_msg = str(exc_info.value)
    assert "TASK06_TOPSECRET" not in error_msg
    assert "alice" not in error_msg
    assert "https://alice:TASK06_TOPSECRET@example.com" not in error_msg


def test_cors_settings_isolated_without_full_settings_requirements(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://tars.example.com")
    cors_settings = config.CorsSettings()
    assert cors_settings.cors_allowed_origins == "https://tars.example.com"
    origins = config.parse_cors_allowed_origins(cors_settings.cors_allowed_origins)
    assert origins == ["https://tars.example.com"]


# --- Section 2 / 5: GCS Bucket Configuration ---

def test_gcs_bucket_name_setting_default_and_override():
    # Absent -> None -> default
    s_default = config.Settings(
        google_cloud_project="my-project",
        auth_allowed_emails="test@example.com",
    )
    assert s_default.gcs_bucket_name is None
    assert s_default.effective_gcs_bucket_name == "my-project-tars"

    # Empty -> None -> default
    s_empty = config.Settings(
        google_cloud_project="my-project",
        auth_allowed_emails="test@example.com",
        gcs_bucket_name="",
    )
    assert s_empty.gcs_bucket_name is None
    assert s_empty.effective_gcs_bucket_name == "my-project-tars"

    # Whitespace-only -> None -> default
    s_ws_only = config.Settings(
        google_cloud_project="my-project",
        auth_allowed_emails="test@example.com",
        gcs_bucket_name="   ",
    )
    assert s_ws_only.gcs_bucket_name is None
    assert s_ws_only.effective_gcs_bucket_name == "my-project-tars"

    # Surrounding whitespace -> trimmed -> custom
    s_surrounding_ws = config.Settings(
        google_cloud_project="my-project",
        auth_allowed_emails="test@example.com",
        gcs_bucket_name="  custom-bucket-name  ",
    )
    assert s_surrounding_ws.gcs_bucket_name == "custom-bucket-name"
    assert s_surrounding_ws.effective_gcs_bucket_name == "custom-bucket-name"

    # Normal custom
    s_custom = config.Settings(
        google_cloud_project="my-project",
        auth_allowed_emails="test@example.com",
        gcs_bucket_name="custom-bucket-name",
    )
    assert s_custom.effective_gcs_bucket_name == "custom-bucket-name"


@pytest.mark.parametrize(
    "invalid_bucket",
    [
        "gs://my-bucket",
        "my/bucket/name",
        "bucket with space",
        "my\tbucket",
        "my\nbucket",
        "my\u00a0bucket",
    ],
)
def test_gcs_bucket_name_invalid_rejected(invalid_bucket):
    with pytest.raises(ValueError):
        config.Settings(
            google_cloud_project="my-project",
            auth_allowed_emails="test@example.com",
            gcs_bucket_name=invalid_bucket,
        )


def test_gcs_storage_never_calls_exists_or_create_bucket():
    from backend.storage.gcs import GCSStorage

    mock_client = MagicMock()
    mock_bucket = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    settings = config.Settings(
        google_cloud_project="test-proj",
        auth_allowed_emails="test@example.com",
        gcs_bucket_name="custom-bucket",
    )
    storage = GCSStorage(settings=settings, client=mock_client)
    bucket = storage._get_bucket()

    assert bucket == mock_bucket
    mock_client.bucket.assert_called_once_with("custom-bucket")
    mock_client.create_bucket.assert_not_called()
    mock_bucket.exists.assert_not_called()


# --- Section 4: Liveness and Readiness Probes ---

def test_healthz_and_readyz_asgi_routes_without_lifespan(monkeypatch):
    def fail_call(*args, **kwargs):
        raise RuntimeError("Provider call forbidden during ASGI route probe")

    monkeypatch.setattr(main, "probe_application_default_credentials", fail_call)
    monkeypatch.setattr(main, "initialize_firebase_admin", fail_call)
    monkeypatch.setattr(main, "FirestoreStorage", fail_call)
    monkeypatch.setattr(main, "GCSStorage", fail_call)
    monkeypatch.setattr(main, "SessionManager", fail_call)
    monkeypatch.setattr(main, "GeminiClient", fail_call)

    async def run_asgi_probes():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. When ready is False: /healthz is 200 ok, /readyz is 503 not_ready
            main.app.state.ready = False
            resp_health = await client.get("/healthz")
            assert resp_health.status_code == status.HTTP_200_OK
            assert resp_health.json() == {"status": "ok"}

            resp_ready_false = await client.get("/readyz")
            assert resp_ready_false.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
            assert resp_ready_false.json() == {"status": "not_ready"}

            # 2. When ready is True: /readyz is 200 ready
            main.app.state.ready = True
            resp_ready_true = await client.get("/readyz")
            assert resp_ready_true.status_code == status.HTTP_200_OK
            assert resp_ready_true.json() == {"status": "ready"}

    asyncio.run(run_asgi_probes())


def test_lifespan_ready_state_transition(monkeypatch):
    monkeypatch.setattr(main, "get_settings", lambda: config.Settings(
        google_cloud_project="test-proj",
        auth_allowed_emails="test@example.com",
    ))

    test_app = main.FastAPI(lifespan=main.lifespan)
    test_app.state.ready = False

    adc_observed_ready: list[bool] = []

    async def mock_probe_adc():
        adc_observed_ready.append(getattr(test_app.state, "ready", False))

    monkeypatch.setattr(main, "probe_application_default_credentials", mock_probe_adc)
    monkeypatch.setattr(main, "initialize_firebase_admin", MagicMock())
    monkeypatch.setattr(main, "FirestoreStorage", MagicMock())
    monkeypatch.setattr(main, "GCSStorage", MagicMock())
    monkeypatch.setattr(main, "SessionManager", MagicMock())
    monkeypatch.setattr(main, "GeminiClient", MagicMock())
    monkeypatch.setattr(main, "_stop_pipeline", AsyncMock())

    # 1. Before lifespan entry: ready must be False
    assert getattr(test_app.state, "ready", False) is False

    async def run_check():
        async with main.lifespan(test_app):
            # 2. Inside running lifespan: ready must be True
            assert getattr(test_app.state, "ready", False) is True

    asyncio.run(run_check())

    # 3. ADC probe observed readiness False when invoked
    assert len(adc_observed_ready) == 1
    assert adc_observed_ready[0] is False

    # 4. After shutdown: ready must be False
    assert getattr(test_app.state, "ready", False) is False


# --- Section 6: Dockerfile & .dockerignore Static Contract ---

def _parse_dockerfile_instructions(content: str) -> list[tuple[str, str]]:
    """Parse effective non-comment Dockerfile instructions supporting multiline continuations and case-insensitive directives, rejecting non-ASCII before normalization."""
    try:
        content.encode("ascii")
    except UnicodeEncodeError as e:
        raise AssertionError(f"Non-ASCII character in Dockerfile content: {e}") from e

    logical_lines: list[str] = []
    current_line = ""

    for raw_line in content.splitlines():
        try:
            raw_line.encode("ascii")
        except UnicodeEncodeError as e:
            raise AssertionError(f"Non-ASCII character in raw Dockerfile line: {e}") from e

        line = raw_line.strip(" \t\r\n")
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            current_line += line[:-1].strip(" \t\r\n") + " "
        else:
            current_line += line
            logical_lines.append(current_line.strip(" \t\r\n"))
            current_line = ""

    if current_line.strip(" \t\r\n"):
        logical_lines.append(current_line.strip(" \t\r\n"))

    instructions: list[tuple[str, str]] = []
    for line in logical_lines:
        parts = re.split(r"[ \t]+", line, maxsplit=1)
        if not parts or not parts[0]:
            continue
        directive = parts[0].upper()
        body = parts[1].strip(" \t\r\n") if len(parts) > 1 else ""
        instructions.append((directive, body))

    return instructions


def _strip_shell_comments(cmd_str: str) -> str:
    """Strip shell comments (# ...) from effective commands to prevent comment-only bypasses, rejecting non-ASCII."""
    try:
        cmd_str.encode("ascii")
    except UnicodeEncodeError as e:
        raise AssertionError(f"Non-ASCII character in RUN command: {e}") from e
    cleaned_lines = []
    for line in cmd_str.splitlines():
        cleaned = re.sub(r'(?:^|[ \t]+)#.*$', '', line)
        cleaned_lines.append(cleaned)
    return " ".join(" ".join(cleaned_lines).split())


def _parse_env_instruction_assignments(body: str) -> list[tuple[str, str]]:
    """Parse actual key=value assignments from an ENV instruction body using shlex with comments=False, rejecting non-ASCII."""
    try:
        body.encode("ascii")
    except UnicodeEncodeError as e:
        raise AssertionError(f"Non-ASCII character in ENV instruction body: {e}") from e

    try:
        tokens = shlex.split(body, comments=False)
    except Exception as e:
        raise ValueError(f"Malformed ENV instruction body: {body}") from e

    assignments: list[tuple[str, str]] = []
    for token in tokens:
        if "=" not in token:
            raise ValueError(f"Legacy space-separated or non-key=value ENV token not allowed: {token}")
        k, v = token.split("=", 1)
        assignments.append((k.strip(), v.strip()))
    return assignments


def _validate_dockerfile_contract(instructions: list[tuple[str, str]]) -> None:
    # Reject non-ASCII across all instructions
    for d, body in instructions:
        try:
            body.encode("ascii")
        except UnicodeEncodeError as e:
            raise AssertionError(f"Non-ASCII character in Dockerfile {d} instruction: {e}") from e

    # Prove exactly one effective CMD and exact approved command
    cmd_instructions = [body for d, body in instructions if d == "CMD"]
    assert len(cmd_instructions) == 1, "There must be exactly one effective CMD instruction"
    exact_cmd = '["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]'
    assert cmd_instructions[0] == exact_cmd

    # Prove zero effective ENTRYPOINT instructions
    entrypoint_instructions = [body for d, body in instructions if d == "ENTRYPOINT"]
    assert len(entrypoint_instructions) == 0, "There must be zero effective ENTRYPOINT instructions"

    # Prove exactly one effective USER instruction and that it is USER appuser
    user_instructions = [body for d, body in instructions if d == "USER"]
    assert len(user_instructions) == 1, "There must be exactly one effective USER instruction"
    assert user_instructions[0] == "appuser"

    # Normalize whitespace in each comment-stripped RUN body
    run_instructions = [body for d, body in instructions if d == "RUN"]
    effective_run_commands = [_strip_shell_comments(body) for body in run_instructions]

    # Require exact canonical user creation RUN body (equality, not substring)
    exact_useradd = "useradd -r -u 1001 appuser"
    assert exact_useradd in effective_run_commands, (
        f"Effective RUN instructions must contain exact canonical user creation: '{exact_useradd}'"
    )

    # Require exact canonical system dependency RUN body matching checked-in Dockerfile (equality, not substring)
    exact_deps = (
        "apt-get update && apt-get install -y --no-install-recommends libsndfile1 libportaudio2 && rm -rf /var/lib/apt/lists/*"
    )
    assert exact_deps in effective_run_commands, (
        f"Effective RUN instructions must contain exact canonical dependency installation: '{exact_deps}'"
    )

    # Parse ENV instructions as actual key=value assignments
    env_instructions = [body for d, body in instructions if d == "ENV"]
    all_env_assignments: list[tuple[str, str]] = []
    for env_body in env_instructions:
        all_env_assignments.extend(_parse_env_instruction_assignments(env_body))

    required_safe_envs = {
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOST_AUDIO_CAPTURE_ENABLED": "false",
        "AUDIO_BACKUP_ENABLED": "false",
    }

    # 1. Exact uppercase source spelling and expected value required
    for env_key, expected_val in required_safe_envs.items():
        exact_matching = [v for k, v in all_env_assignments if k == env_key]
        assert len(exact_matching) == 1, f"Expected exactly one uppercase source assignment for {env_key}, found {len(exact_matching)}"
        assert exact_matching[0] == expected_val, f"Expected {env_key}={expected_val}, found {exact_matching[0]}"

        # 2. Case-insensitive logical-key multiplicity: no lowercase or mixed-case duplicates/overrides allowed
        logical_matching = [v for k, v in all_env_assignments if k.upper() == env_key]
        assert len(logical_matching) == 1, (
            f"Expected exactly one assignment for logical ENV key {env_key}, found {len(logical_matching)} (case-insensitive collision or duplicate)"
        )

    # 3. Every occurrence across all case variants must be strictly safe
    for k, v in all_env_assignments:
        k_upper = k.upper()
        if k_upper in required_safe_envs:
            expected = required_safe_envs[k_upper]
            assert v == expected, f"Logical ENV key {k} must be '{expected}', found '{v}'"

    # Reject --reload and --workers across all CMD and ENTRYPOINT instructions
    for d, body in instructions:
        if d in ("CMD", "ENTRYPOINT"):
            assert "--reload" not in body
            assert "--workers" not in body


def test_dockerfile_static_source_contract():
    dockerfile_path = REPO_ROOT / "Dockerfile"
    assert dockerfile_path.exists(), "Dockerfile must exist"
    content = dockerfile_path.read_text(encoding="utf-8")
    instructions = _parse_dockerfile_instructions(content)
    _validate_dockerfile_contract(instructions)


def test_dockerfile_static_source_guard_regression_probes():
    canonical_deps = (
        "apt-get update && apt-get install -y --no-install-recommends libsndfile1 libportaudio2 && rm -rf /var/lib/apt/lists/*"
    )

    # Probe 1: Case-insensitive lowercase cmd/user/entrypoint (valid lowercase directives parsed case-insensitively)
    sample_lowercase = f"""
    from python:3.12-slim
    run {canonical_deps}
    env HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    run useradd -r -u 1001 appuser
    user appuser
    cmd ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    instructions = _parse_dockerfile_instructions(sample_lowercase)
    _validate_dockerfile_contract(instructions)

    # Probe 2: Comment-only user directive fails
    sample_comment_user = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    # RUN useradd -r -u 1001 appuser
    # USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_comment_user))

    # Probe 3: Inert RUN comment with useradd fails
    sample_inert_useradd_run = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN true # useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_inert_useradd_run))

    # Probe 4: Inert RUN comment with library installs fails
    sample_inert_libs_run = """
    FROM python:3.12-slim
    RUN true # apt-get update && apt-get install -y --no-install-recommends libsndfile1 libportaudio2 && rm -rf /var/lib/apt/lists/*
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_inert_libs_run))

    # Probe 5: Inert NOTE ENV substring fails
    sample_inert_env_note = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV NOTE="HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1"
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_inert_env_note))

    # Probe 6: Unsafe duplicate ENV override fails
    sample_duplicate_env = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=true HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_duplicate_env))

    # Probe 7: Lowercase entrypoint overriding process fails
    sample_override_entrypoint = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser
    entrypoint ["sh", "-c", "override"]
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_override_entrypoint))

    # Probe 8: --reload in CMD fails
    sample_reload = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_reload))

    # Probe 9: Inert echo useradd command fails exact equality
    sample_echo_useradd = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN echo useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_echo_useradd))

    # Probe 10: Inert echo dependency installation command fails exact equality
    sample_echo_deps = """
    FROM python:3.12-slim
    RUN echo apt-get install libsndfile1 libportaudio2
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_echo_deps))

    # Probe 11: ENV inline # is not comment, fails exact value check
    sample_inline_hash_env = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false#unsafe AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_inline_hash_env))

    # Probe 12: NBSP-separated useradd fails non-ASCII check
    sample_nbsp_useradd = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd\u00a0-r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_nbsp_useradd))

    # Probe 13: NBSP-separated dependency RUN fails non-ASCII check
    sample_nbsp_deps = """
    FROM python:3.12-slim
    RUN apt-get\u00a0update && apt-get install -y --no-install-recommends libsndfile1 libportaudio2 && rm -rf /var/lib/apt/lists/*
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_nbsp_deps))

    # Probe 14: NBSP between RUN directive and body fails
    sample_nbsp_run_directive = f"""
    FROM python:3.12-slim
    RUN\u00a0{canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_nbsp_run_directive))

    # Probe 15: NBSP between ENV directive and body fails
    sample_nbsp_env_directive = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV\u00a0HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_nbsp_env_directive))

    # Probe 16: NBSP between USER directive and body fails
    sample_nbsp_user_directive = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER\u00a0appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_nbsp_user_directive))

    # Probe 17: NBSP between CMD directive and body fails
    sample_nbsp_cmd_directive = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD\u00a0["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_nbsp_cmd_directive))

    # Probe 18: Trailing NBSP on required dependency RUN fails
    sample_trailing_nbsp_deps = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}\u00a0
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_trailing_nbsp_deps))

    # Probe 19: Trailing NBSP on required ENV line fails
    sample_trailing_nbsp_env = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1\u00a0
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_trailing_nbsp_env))

    # Probe 20: Trailing NBSP on useradd RUN fails
    sample_trailing_nbsp_useradd = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser\u00a0
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_trailing_nbsp_useradd))

    # Probe 21: Trailing NBSP on USER fails
    sample_trailing_nbsp_user = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser\u00a0
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_trailing_nbsp_user))

    # Probe 22: Trailing NBSP on CMD fails
    sample_trailing_nbsp_cmd = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]\u00a0
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_trailing_nbsp_cmd))

    # Probe 23: Combined mixed/lowercase unsafe ENV duplicate fails
    sample_combined_mixed_env_override = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    ENV Host_Audio_Capture_Enabled=true audio_backup_enabled=true
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_combined_mixed_env_override))

    # Probe 24: Individual mixed-case Host_Audio_Capture_Enabled=true duplicate fails
    sample_mixed_host_audio_override = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    ENV Host_Audio_Capture_Enabled=true
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_mixed_host_audio_override))

    # Probe 25: Individual lowercase host_audio_capture_enabled=true duplicate fails
    sample_lower_host_audio_override = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    ENV host_audio_capture_enabled=true
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_lower_host_audio_override))

    # Probe 26: Individual mixed-case Audio_Backup_Enabled=true duplicate fails
    sample_mixed_audio_backup_override = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    ENV Audio_Backup_Enabled=true
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_mixed_audio_backup_override))

    # Probe 27: Individual lowercase audio_backup_enabled=true duplicate fails
    sample_lower_audio_backup_override = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    ENV audio_backup_enabled=true
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_lower_audio_backup_override))

    # Probe 28: Individual lowercase pythonunbuffered=0 duplicate fails
    sample_lower_unbuffered_override = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    ENV pythonunbuffered=0
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_lower_unbuffered_override))

    # Probe 29: Individual lowercase pythondontwritebytecode=0 duplicate fails
    sample_lower_dontwritebytecode_override = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    ENV pythondontwritebytecode=0
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_lower_dontwritebytecode_override))

    # Probe 30: Multi-assignment ENV line containing safe canonical plus unsafe case variant fails
    sample_inline_case_collision_env = f"""
    FROM python:3.12-slim
    RUN {canonical_deps}
    ENV HOST_AUDIO_CAPTURE_ENABLED=false host_audio_capture_enabled=true AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${{PORT:-8080}}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(sample_inline_case_collision_env))


def test_dockerignore_static_contract():
    dockerignore_path = REPO_ROOT / ".dockerignore"
    assert dockerignore_path.exists(), ".dockerignore must exist"
    content = dockerignore_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]

    assert lines == [
        "**",
        "!Dockerfile",
        "!requirements.txt",
        "!backend/",
        "!backend/**/",
        "!backend/**/*.py",
        "backend/tests/",
        "backend/tests/**",
    ]


# --- Section 7: Environment Examples Static Contract ---

def _parse_env_assignment_pairs(text: str) -> list[tuple[str, str]]:
    """Parse all non-comment key=value assignments preserving multiplicity and order, failing closed on noncanonical syntax."""
    pairs: list[tuple[str, str]] = []
    for line in text.splitlines():
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("#"):
            continue

        # Optional lowercase export prefix followed by one or more ASCII spaces/tabs
        if line_clean.startswith("export"):
            if not re.match(r"^export[ \t]+", line_clean):
                raise ValueError(f"Noncanonical export prefix syntax in .env: '{line}'")
            line_clean = re.sub(r"^export[ \t]+", "", line_clean)

        if "=" not in line_clean:
            raise ValueError(f"Non-assignment line in .env: '{line}'")

        k_raw, v_raw = line_clean.split("=", 1)
        k = k_raw.strip()

        # Reject quoted keys, non-ASCII, NBSP, whitespace inside key, non-identifier key
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
            raise ValueError(f"Noncanonical or invalid identifier key in .env: '{k_raw}'")

        pairs.append((k, v_raw.strip()))
    return pairs


def _validate_env_examples_contract(root_content: str, frontend_content: str) -> None:
    try:
        root_pairs = _parse_env_assignment_pairs(root_content)
        frontend_pairs = _parse_env_assignment_pairs(frontend_content)
    except ValueError as e:
        raise AssertionError(f"Noncanonical .env syntax rejected: {e}") from e

    # 1. Exact uppercase source spellings required (exact key text):
    root_exact_keys = [k for k, _ in root_pairs]
    frontend_exact_keys = [k for k, _ in frontend_pairs]

    assert "AUTH_ALLOWED_EMAILS" in root_exact_keys, "Root .env.example must have exact uppercase AUTH_ALLOWED_EMAILS"
    assert "AUTH_BYPASS" in root_exact_keys, "Root .env.example must have exact uppercase AUTH_BYPASS"
    assert "NEXT_PUBLIC_AUTH_BYPASS" in root_exact_keys, "Root .env.example must have exact uppercase NEXT_PUBLIC_AUTH_BYPASS"
    assert "NEXT_PUBLIC_AUTH_BYPASS" in frontend_exact_keys, "Frontend .env.example must have exact uppercase NEXT_PUBLIC_AUTH_BYPASS"
    assert "CORS_ALLOWED_ORIGINS" in root_exact_keys, "Root .env.example must have exact uppercase CORS_ALLOWED_ORIGINS"

    for var in ["NEXT_PUBLIC_API_URL", "NEXT_PUBLIC_WS_URL", "NEXT_PUBLIC_WS_STREAM_URL"]:
        assert var in root_exact_keys, f"Root .env.example must have exact uppercase {var}"
        assert var in frontend_exact_keys, f"Frontend .env.example must have exact uppercase {var}"

    # 2. Case-insensitive logical-key multiplicity and exact values:
    # Root has exactly one AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com (any case variant)
    root_emails = [v for k, v in root_pairs if k.upper() == "AUTH_ALLOWED_EMAILS"]
    assert len(root_emails) == 1, f"Root .env.example must have exactly one AUTH_ALLOWED_EMAILS (found {len(root_emails)})"
    assert root_emails[0] == "authorized-recruiter@example.com"

    # Frontend has zero AUTH_ALLOWED_EMAILS (any case variant)
    frontend_emails = [v for k, v in frontend_pairs if k.upper() == "AUTH_ALLOWED_EMAILS"]
    assert len(frontend_emails) == 0, f"Frontend .env.example must have zero AUTH_ALLOWED_EMAILS (found {len(frontend_emails)})"

    # Root has exactly one AUTH_BYPASS=false (any case variant)
    root_bypass = [v for k, v in root_pairs if k.upper() == "AUTH_BYPASS"]
    assert len(root_bypass) == 1, f"Root .env.example must have exactly one AUTH_BYPASS (found {len(root_bypass)})"
    assert root_bypass[0] == "false"

    # Root has exactly one NEXT_PUBLIC_AUTH_BYPASS=0 (any case variant)
    root_public_bypass = [v for k, v in root_pairs if k.upper() == "NEXT_PUBLIC_AUTH_BYPASS"]
    assert len(root_public_bypass) == 1, f"Root .env.example must have exactly one NEXT_PUBLIC_AUTH_BYPASS (found {len(root_public_bypass)})"
    assert root_public_bypass[0] == "0"

    # Frontend has exactly one NEXT_PUBLIC_AUTH_BYPASS=0 (any case variant)
    frontend_public_bypass = [v for k, v in frontend_pairs if k.upper() == "NEXT_PUBLIC_AUTH_BYPASS"]
    assert len(frontend_public_bypass) == 1, f"Frontend .env.example must have exactly one NEXT_PUBLIC_AUTH_BYPASS (found {len(frontend_public_bypass)})"
    assert frontend_public_bypass[0] == "0"

    # 3. Every case-variant occurrence across both examples must be strictly safe:
    for k, v in root_pairs + frontend_pairs:
        k_upper = k.upper()
        if k_upper == "AUTH_BYPASS":
            assert v == "false", f"AUTH_BYPASS must be 'false', found '{k}={v}'"
        if k_upper == "NEXT_PUBLIC_AUTH_BYPASS":
            assert v == "0", f"NEXT_PUBLIC_AUTH_BYPASS must be '0', found '{k}={v}'"
        if k_upper == "AUTH_ALLOWED_EMAILS":
            assert v == "authorized-recruiter@example.com", f"AUTH_ALLOWED_EMAILS must be placeholder, found '{k}={v}'"

    # 4. Case-insensitive credential check across combined content and keys
    forbidden_indicators = [
        "google_application_credentials",
        "credential_path",
        "credentials_path",
        "credential-path",
        "credentials-path",
        "service_account",
        "private_key",
        "client_email",
    ]
    combined_lower = (root_content + "\n" + frontend_content).lower()
    for indicator in forbidden_indicators:
        assert indicator not in combined_lower, f"Forbidden credential indicator '{indicator}' found"

    # 5. Wildcard CORS check from EVERY case-variant parsed CORS_ALLOWED_ORIGINS
    cors_values = [v for k, v in root_pairs + frontend_pairs if k.upper() == "CORS_ALLOWED_ORIGINS"]
    assert len(cors_values) >= 1
    for val in cors_values:
        assert "*" not in val, "Wildcard found in CORS_ALLOWED_ORIGINS"
        parsed = config.parse_cors_allowed_origins(val)
        assert len(parsed) >= 1

    # 6. All 5 local defaults listed in root example
    for origin in config.DEFAULT_LOCAL_CORS_ORIGINS:
        assert origin in root_content

    # 7. Frontend comment restored
    assert "Only Playwright sets this to 1. Never enable it in a production build." in frontend_content


def test_environment_examples_static_contract():
    root_env_path = REPO_ROOT / ".env.example"
    frontend_env_path = REPO_ROOT / "frontend" / ".env.example"

    assert root_env_path.exists(), ".env.example must exist"
    assert frontend_env_path.exists(), "frontend/.env.example must exist"

    root_content = root_env_path.read_text(encoding="utf-8")
    frontend_content = frontend_env_path.read_text(encoding="utf-8")

    _validate_env_examples_contract(root_content, frontend_content)


def test_environment_examples_guard_regression_probes():
    valid_root = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=false
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    """
    valid_frontend = """
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    # Only Playwright sets this to 1. Never enable it in a production build.
    NEXT_PUBLIC_AUTH_BYPASS=0
    """

    # Probe 1: Duplicate unsafe bypass followed by safe bypass fails
    root_duplicate_unsafe = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=true
    AUTH_BYPASS=false
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_duplicate_unsafe, valid_frontend)

    # Probe 2: Removal of root NEXT_PUBLIC_AUTH_BYPASS fails
    root_missing_public_bypass = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=false
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_missing_public_bypass, valid_frontend)

    # Probe 3: export AUTH_BYPASS=true fails even if safe line exists
    root_export_bypass_true = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    export AUTH_BYPASS=true
    AUTH_BYPASS=false
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_export_bypass_true, valid_frontend)

    # Probe 4: export NEXT_PUBLIC_AUTH_BYPASS=1 fails even if safe line exists
    frontend_export_public_bypass = """
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    # Only Playwright sets this to 1. Never enable it in a production build.
    export NEXT_PUBLIC_AUTH_BYPASS=1
    NEXT_PUBLIC_AUTH_BYPASS=0
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(valid_root, frontend_export_public_bypass)

    # Probe 5: export CORS_ALLOWED_ORIGINS=* fails even if safe line exists
    root_export_cors_wildcard = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=false
    export CORS_ALLOWED_ORIGINS=*
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_export_cors_wildcard, valid_frontend)

    # Probe 6: Uppercase PRIVATE_KEY or CREDENTIAL_PATH fails
    root_with_private_key = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=false
    PRIVATE_KEY=secret
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_with_private_key, valid_frontend)

    root_with_credential_path = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=false
    CREDENTIAL_PATH=/path/to/key.json
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_with_credential_path, valid_frontend)

    # Probe 7: Duplicate CORS with wildcard fails
    root_duplicate_cors_wildcard = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=false
    CORS_ALLOWED_ORIGINS=*
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_duplicate_cors_wildcard, valid_frontend)

    # Probe 8: Lowercase auth_bypass=true fails
    root_lowercase_bypass_true = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=false
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    auth_bypass=true
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_lowercase_bypass_true, valid_frontend)

    # Probe 9: Mixed-case Auth_Bypass=true fails
    root_mixed_case_bypass_true = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=false
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    Auth_Bypass=true
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_mixed_case_bypass_true, valid_frontend)

    # Probe 10: export auth_bypass=true fails
    root_export_mixed_bypass_true = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=false
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    export auth_bypass=true
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_export_mixed_bypass_true, valid_frontend)

    # Probe 11: Mixed-case real allowlist duplicate fails
    root_mixed_allowlist_duplicate = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    Auth_Allowed_Emails=attacker@example.com
    AUTH_BYPASS=false
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_mixed_allowlist_duplicate, valid_frontend)

    # Probe 12: Mixed-case public-bypass=1 duplicate fails
    frontend_mixed_public_bypass_duplicate = """
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    # Only Playwright sets this to 1. Never enable it in a production build.
    NEXT_PUBLIC_AUTH_BYPASS=0
    Next_Public_Auth_Bypass=1
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(valid_root, frontend_mixed_public_bypass_duplicate)

    # Probe 13: Mixed-case/exported wildcard CORS duplicate fails
    root_mixed_cors_wildcard = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=false
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    export Cors_Allowed_Origins=*
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_mixed_cors_wildcard, valid_frontend)

    # Probe 14: Single-quoted key 'AUTH_BYPASS'=true fails noncanonical syntax
    root_quoted_bypass_true = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=false
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    'AUTH_BYPASS'=true
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_quoted_bypass_true, valid_frontend)

    # Probe 15: Exported single-quoted key export 'AUTH_BYPASS'=true fails noncanonical syntax
    root_export_quoted_bypass_true = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=false
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    export 'AUTH_BYPASS'=true
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_export_quoted_bypass_true, valid_frontend)

    # Probe 16: NBSP export separator export\u00a0AUTH_BYPASS=true fails
    root_nbsp_export_bypass_true = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=false
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    export\u00a0AUTH_BYPASS=true
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_nbsp_export_bypass_true, valid_frontend)

    # Probe 17: Single-quoted CORS key 'CORS_ALLOWED_ORIGINS'=* fails noncanonical syntax
    root_quoted_cors_wildcard = """
    AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com
    AUTH_BYPASS=false
    CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga
    NEXT_PUBLIC_API_URL=http://localhost:8000
    NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
    NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
    NEXT_PUBLIC_AUTH_BYPASS=0
    'CORS_ALLOWED_ORIGINS'=*
    """
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(root_quoted_cors_wildcard, valid_frontend)


# --- Section 8: CompanionCommand.tsx Static Contract ---

def test_companion_command_tsx_no_visible_stream_key_or_terminal_copy():
    component_path = REPO_ROOT / "frontend" / "src" / "components" / "CompanionCommand.tsx"
    assert component_path.exists(), "CompanionCommand.tsx must exist"
    content = component_path.read_text(encoding="utf-8")

    assert "--stream-key" not in content
    assert "clipboard" not in content.lower()
    assert "Método alternativo" not in content
    assert "buildJoinLink" in content
    assert "Conectar companion" in content
