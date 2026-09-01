"""Task 07 Auth Source Readiness and Adversarial Mutation Probes.

Comprehensive offline tests for:
1. Strict TARS_RUNTIME_MODE and canonical AUTH_BYPASS parsing.
2. Raw environment validation, case collision detection, and hosted/local matrix.
3. Strict allowlist parsing and content-free error handling.
4. Runtime auth validation (hosted 5-account corporate domain, org slug, project IDs).
5. Firebase Admin ID token decoding, validation, and content-free exception sanitization.
6. Local Firebase app project binding check (without touching lazy project_id).
7. Non-enumerating owner/org postconditions on list_sessions and list_recent_interviews.
8. Offline check_auth_setup pure validator with injected environments.
9. Dockerfile static contract and multi-stage evaluation.
10. .env.example static contract.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Mapping
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend import main
from backend.auth import (
    AuthConfigurationError,
    AuthContext,
    AuthenticationError,
    _allowed_emails,
    initialize_firebase_admin,
    parse_allowed_emails,
    validate_auth_configuration,
    verify_bearer_token,
)
from backend.config import (
    Settings,
    validate_raw_process_env,
)
from backend.scripts.check_auth_setup import check_auth_setup
from backend.tests.test_cloud_run_readiness import (
    _parse_dockerfile_instructions,
    _validate_dockerfile_contract,
    _parse_env_assignment_pairs,
    _validate_env_examples_contract,
    REPO_ROOT,
)


# --- 1. Runtime Mode and Bypass Parsing ---

@pytest.mark.parametrize(
    "val",
    [
        "1", "0", "yes", "no", "on", "off", "TRUE", "False", "True", "FALSE",
        " true", "true ", "\ttrue", "true\n", "'true'", '"false"', "true\u00a0",
        "", " ", "\x00", "true\x00",
    ],
)
def test_auth_bypass_rejects_non_canonical_strings(val):
    with pytest.raises((ValueError, ValidationError)):
        Settings(
            auth_bypass=val,
            google_cloud_project="tars-test",
            auth_allowed_emails="recruiter@ellaexecutivesearch.com",
            auth_org_id="ella-internal",
        )


def test_auth_bypass_accepts_canonical_literals():
    s_false = Settings(
        auth_bypass="false",
        google_cloud_project="tars-test",
        auth_allowed_emails="recruiter@ellaexecutivesearch.com",
        auth_org_id="ella-internal",
    )
    assert s_false.auth_bypass is False

    s_true = Settings(
        auth_bypass="true",
        google_cloud_project="tars-test",
        auth_allowed_emails="recruiter@ellaexecutivesearch.com",
        auth_org_id="ella-internal",
    )
    assert s_true.auth_bypass is True

    s_bool = Settings(
        auth_bypass=False,
        google_cloud_project="tars-test",
        auth_allowed_emails="recruiter@ellaexecutivesearch.com",
        auth_org_id="ella-internal",
    )
    assert s_bool.auth_bypass is False


@pytest.mark.parametrize(
    "mode",
    ["local", "hosted-pilot"],
)
def test_runtime_mode_accepts_valid_modes(mode):
    s = Settings(
        tars_runtime_mode=mode,
        auth_bypass=False,
        google_cloud_project="tars-test",
        auth_allowed_emails="recruiter@ellaexecutivesearch.com",
        auth_org_id="ella-internal",
    )
    assert s.tars_runtime_mode == mode


@pytest.mark.parametrize(
    "mode",
    ["HOSTED-PILOT", "Hosted-Pilot", "production", "staging", "dev", "local-dev", "1", ""],
)
def test_runtime_mode_rejects_invalid_modes(mode):
    with pytest.raises(ValidationError):
        Settings(
            tars_runtime_mode=mode,
            auth_bypass=False,
            google_cloud_project="tars-test",
            auth_allowed_emails="recruiter@ellaexecutivesearch.com",
            auth_org_id="ella-internal",
        )


# --- 2. Raw Environment Validation & Matrix ---

def valid_hosted_raw_env() -> dict[str, str]:
    return {
        "TARS_RUNTIME_MODE": "hosted-pilot",
        "AUTH_BYPASS": "false",
        "GOOGLE_CLOUD_PROJECT": "tars-pilot",
        "FIREBASE_PROJECT_ID": "tars-pilot",
        "AUTH_ORG_ID": "ella-internal",
        "AUTH_ALLOWED_EMAILS": "a@ellaexecutivesearch.com,b@ellaexecutivesearch.com,c@ellaexecutivesearch.com,d@ellaexecutivesearch.com,e@ellaexecutivesearch.com",
    }


def test_validate_raw_process_env_hosted_success():
    mode = validate_raw_process_env(valid_hosted_raw_env())
    assert mode == "hosted-pilot"


def test_validate_raw_process_env_local_success():
    local_env = {
        "TARS_RUNTIME_MODE": "local",
        "AUTH_BYPASS": "false",
        "GOOGLE_CLOUD_PROJECT": "tars-local",
    }
    mode = validate_raw_process_env(local_env)
    assert mode == "local"


@pytest.mark.parametrize(
    "mutation",
    [
        {"TARS_RUNTIME_MODE": "hosted-pilot", "tars_runtime_mode": "hosted-pilot"},
        {"AUTH_BYPASS": "false", "auth_bypass": "false"},
        {"AUTH_BYPASS": "false", "Auth_Bypass": "true"},
        {"GOOGLE_CLOUD_PROJECT": "p", "google_cloud_project": "p"},
        {"FIREBASE_PROJECT_ID": "p", "firebase_project_id": "p"},
        {"AUTH_ORG_ID": "ella-internal", "auth_org_id": "ella-internal"},
        {"AUTH_ALLOWED_EMAILS": "a@b.com", "auth_allowed_emails": "a@b.com"},
    ],
)
def test_validate_raw_process_env_rejects_case_collisions(mutation):
    env = valid_hosted_raw_env()
    env.update(mutation)
    with pytest.raises(AuthConfigurationError):
        validate_raw_process_env(env)


def test_validate_raw_process_env_k_service_requires_hosted():
    env = valid_hosted_raw_env()
    env["K_SERVICE"] = "tars-service"
    assert validate_raw_process_env(env) == "hosted-pilot"

    # K_SERVICE with local mode fails closed
    env["TARS_RUNTIME_MODE"] = "local"
    with pytest.raises(AuthConfigurationError):
        validate_raw_process_env(env)

    # K_SERVICE missing runtime mode fails closed
    del env["TARS_RUNTIME_MODE"]
    with pytest.raises(AuthConfigurationError):
        validate_raw_process_env(env)


def test_raw_resolved_matrix_enforcement(monkeypatch):
    """Table-driven causal matrix verifying raw vs resolved synchronization for hosted and local baselines."""
    # 1. Complete passing hosted baseline
    hosted_raw_base = {
        "TARS_RUNTIME_MODE": "hosted-pilot",
        "AUTH_BYPASS": "false",
        "GOOGLE_CLOUD_PROJECT": "tars-pilot-proj",
        "FIREBASE_PROJECT_ID": "tars-pilot-proj",
        "AUTH_ORG_ID": "ella-internal",
        "AUTH_ALLOWED_EMAILS": "u1@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com,u5@ellaexecutivesearch.com",
        "K_SERVICE": "tars-service",
    }
    hosted_resolved_base = Settings(
        tars_runtime_mode="hosted-pilot",
        auth_bypass=False,
        google_cloud_project="tars-pilot-proj",
        firebase_project_id="tars-pilot-proj",
        auth_org_id="ella-internal",
        auth_allowed_emails="u1@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com,u5@ellaexecutivesearch.com",
    )
    # Baseline passes immediately
    assert validate_raw_process_env(hosted_raw_base, resolved_settings=hosted_resolved_base) == "hosted-pilot"

    # Hosted K_SERVICE missing and empty pass with same semantics
    raw_no_ks = dict(hosted_raw_base)
    del raw_no_ks["K_SERVICE"]
    assert validate_raw_process_env(raw_no_ks, resolved_settings=hosted_resolved_base) == "hosted-pilot"
    raw_empty_ks = dict(hosted_raw_base, K_SERVICE="")
    assert validate_raw_process_env(raw_empty_ks, resolved_settings=hosted_resolved_base) == "hosted-pilot"

    # Hosted failure table
    hosted_failure_cases: list[tuple[dict[str, str], Settings, str]] = []

    # A. Independently remove each protected key
    for k in ("AUTH_BYPASS", "GOOGLE_CLOUD_PROJECT", "FIREBASE_PROJECT_ID", "AUTH_ORG_ID", "AUTH_ALLOWED_EMAILS"):
        env_mod = dict(hosted_raw_base)
        del env_mod[k]
        hosted_failure_cases.append((env_mod, hosted_resolved_base, f"Missing required protected environment key in hosted mode: {k}"))
    env_no_mode = dict(hosted_raw_base)
    del env_no_mode["TARS_RUNTIME_MODE"]
    hosted_failure_cases.append((env_no_mode, hosted_resolved_base, "TARS_RUNTIME_MODE=hosted-pilot required in hosted environment"))

    # B. Lowercase and mixed-case replacements
    for k in ("TARS_RUNTIME_MODE", "AUTH_BYPASS", "GOOGLE_CLOUD_PROJECT", "FIREBASE_PROJECT_ID", "AUTH_ORG_ID", "AUTH_ALLOWED_EMAILS"):
        env_lower = dict(hosted_raw_base)
        del env_lower[k]
        env_lower[k.lower()] = hosted_raw_base[k]
        hosted_failure_cases.append((env_lower, hosted_resolved_base, f"Protected environment key must have exact uppercase spelling: {k}"))

        env_mixed = dict(hosted_raw_base)
        del env_mixed[k]
        env_mixed[k[0].upper() + k[1:].lower()] = hosted_raw_base[k]
        hosted_failure_cases.append((env_mixed, hosted_resolved_base, f"Protected environment key must have exact uppercase spelling: {k}"))

    # C. Duplicate / colliding alias added alongside uppercase
    for k in ("TARS_RUNTIME_MODE", "AUTH_BYPASS", "GOOGLE_CLOUD_PROJECT", "FIREBASE_PROJECT_ID", "AUTH_ORG_ID", "AUTH_ALLOWED_EMAILS"):
        env_dup = dict(hosted_raw_base)
        env_dup[k.lower()] = hosted_raw_base[k]
        hosted_failure_cases.append((env_dup, hosted_resolved_base, f"Duplicate or case-colliding environment key detected for logical key: {k}"))

    # D. Divergence of raw vs resolved (single property per divergence row)
    hosted_failure_cases.extend([
        (hosted_raw_base, Settings(tars_runtime_mode="local", auth_bypass=False, google_cloud_project="tars-pilot-proj", firebase_project_id="tars-pilot-proj", auth_org_id="ella-internal", auth_allowed_emails=hosted_resolved_base.auth_allowed_emails), "Resolved settings runtime mode mismatch with hosted raw environment"),
        (hosted_raw_base, Settings(tars_runtime_mode="hosted-pilot", auth_bypass=True, google_cloud_project="tars-pilot-proj", firebase_project_id="tars-pilot-proj", auth_org_id="ella-internal", auth_allowed_emails=hosted_resolved_base.auth_allowed_emails), "Resolved settings auth_bypass must be False in hosted mode"),
        (hosted_raw_base, Settings(tars_runtime_mode="hosted-pilot", auth_bypass=False, google_cloud_project="other-proj", firebase_project_id="tars-pilot-proj", auth_org_id="ella-internal", auth_allowed_emails=hosted_resolved_base.auth_allowed_emails), "Resolved GOOGLE_CLOUD_PROJECT does not match raw environment"),
        (hosted_raw_base, Settings(tars_runtime_mode="hosted-pilot", auth_bypass=False, google_cloud_project="tars-pilot-proj", firebase_project_id="other-proj", auth_org_id="ella-internal", auth_allowed_emails=hosted_resolved_base.auth_allowed_emails), "Resolved FIREBASE_PROJECT_ID does not match raw environment"),
        (hosted_raw_base, Settings(tars_runtime_mode="hosted-pilot", auth_bypass=False, google_cloud_project="tars-pilot-proj", firebase_project_id="tars-pilot-proj", auth_org_id="other-org", auth_allowed_emails=hosted_resolved_base.auth_allowed_emails), "Resolved AUTH_ORG_ID does not match raw environment"),
        (hosted_raw_base, Settings(tars_runtime_mode="hosted-pilot", auth_bypass=False, google_cloud_project="tars-pilot-proj", firebase_project_id="tars-pilot-proj", auth_org_id="ella-internal", auth_allowed_emails="other@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com,u5@ellaexecutivesearch.com"), "Resolved AUTH_ALLOWED_EMAILS does not match raw environment"),
    ])

    # E. K_SERVICE casing, collision, and local mode conflict
    env_ks_lower = dict(hosted_raw_base)
    del env_ks_lower["K_SERVICE"]
    env_ks_lower["k_service"] = "tars-service"
    hosted_failure_cases.append((env_ks_lower, hosted_resolved_base, "Protected environment key must have exact uppercase spelling: K_SERVICE"))

    env_ks_mixed = dict(hosted_raw_base)
    del env_ks_mixed["K_SERVICE"]
    env_ks_mixed["K_Service"] = "tars-service"
    hosted_failure_cases.append((env_ks_mixed, hosted_resolved_base, "Protected environment key must have exact uppercase spelling: K_SERVICE"))

    env_ks_col = dict(hosted_raw_base)
    env_ks_col["k_service"] = "tars-service"
    hosted_failure_cases.append((env_ks_col, hosted_resolved_base, "Duplicate or case-colliding environment key detected for logical key: K_SERVICE"))

    env_ks_local = dict(hosted_raw_base, TARS_RUNTIME_MODE="local")
    hosted_failure_cases.append((env_ks_local, hosted_resolved_base, "TARS_RUNTIME_MODE must be exact 'hosted-pilot' in hosted environment"))

    for raw_env, res_s, exp_msg in hosted_failure_cases:
        with pytest.raises(AuthConfigurationError) as exc:
            validate_raw_process_env(raw_env, resolved_settings=res_s)
        assert str(exc.value) == exp_msg

    # 2. Complete passing local baseline
    local_raw_base = {
        "TARS_RUNTIME_MODE": "local",
        "AUTH_BYPASS": "false",
        "GOOGLE_CLOUD_PROJECT": "tars-local-proj",
        "FIREBASE_PROJECT_ID": "tars-local-proj",
        "AUTH_ORG_ID": "ella-internal",
        "AUTH_ALLOWED_EMAILS": "local@ellaexecutivesearch.com",
    }
    local_resolved_base = Settings(
        tars_runtime_mode="local",
        auth_bypass=False,
        google_cloud_project="tars-local-proj",
        firebase_project_id="tars-local-proj",
        auth_org_id="ella-internal",
        auth_allowed_emails="local@ellaexecutivesearch.com",
    )
    assert validate_raw_process_env(local_raw_base, resolved_settings=local_resolved_base) == "local"

    local_failure_cases = [
        (dict(local_raw_base, AUTH_BYPASS="true"), local_resolved_base, "Resolved AUTH_BYPASS does not match raw environment"),
        (dict(local_raw_base, TARS_RUNTIME_MODE="invalid"), local_resolved_base, "Invalid TARS_RUNTIME_MODE for local runtime"),
        (dict(local_raw_base, GOOGLE_CLOUD_PROJECT="other-proj"), local_resolved_base, "Resolved GOOGLE_CLOUD_PROJECT does not match raw environment"),
        (dict(local_raw_base, FIREBASE_PROJECT_ID="other-proj"), local_resolved_base, "Resolved FIREBASE_PROJECT_ID does not match raw environment"),
        (dict(local_raw_base, AUTH_ORG_ID="other-org"), local_resolved_base, "Resolved AUTH_ORG_ID does not match raw environment"),
        (dict(local_raw_base, AUTH_ALLOWED_EMAILS="other@ellaexecutivesearch.com"), local_resolved_base, "Resolved AUTH_ALLOWED_EMAILS does not match raw environment"),
    ]
    for raw_env, res_s, exp_msg in local_failure_cases:
        with pytest.raises(AuthConfigurationError) as exc:
            validate_raw_process_env(raw_env, resolved_settings=res_s)
        assert str(exc.value) == exp_msg

    # 3. Local raw-absent secondary source defaults and illegal hosted resolution
    raw_absent: dict[str, str] = {}
    resolved_local_default = Settings(
        tars_runtime_mode="local",
        auth_bypass=True,
        google_cloud_project="tars-dev",
        auth_org_id="ella-internal",
        auth_allowed_emails="dev@ellaexecutivesearch.com",
    )
    assert validate_raw_process_env(raw_absent, resolved_settings=resolved_local_default) == "local"

    resolved_hosted_illegal = Settings(
        tars_runtime_mode="hosted-pilot",
        auth_bypass=False,
        google_cloud_project="tars-pilot",
        firebase_project_id="tars-pilot",
        auth_org_id="ella-internal",
        auth_allowed_emails="u1@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com,u5@ellaexecutivesearch.com",
    )
    with pytest.raises(AuthConfigurationError) as exc_illegal:
        validate_raw_process_env(raw_absent, resolved_settings=resolved_hosted_illegal)
    assert str(exc_illegal.value) == "Hosted runtime mode cannot be resolved from secondary source when raw environment is local or absent"

    with pytest.raises(AuthConfigurationError) as exc_local_raw_illegal:
        validate_raw_process_env(local_raw_base, resolved_settings=resolved_hosted_illegal)
    assert str(exc_local_raw_illegal.value) == "Hosted runtime mode cannot be resolved from secondary source when raw environment is local or absent"


def test_lifespan_raw_resolved_categories_and_publication(monkeypatch):
    """Real main.lifespan table verifying failure categories, prefix counts, zero later effects, and published globals."""
    import asyncio

    hosted_raw_base = {
        "TARS_RUNTIME_MODE": "hosted-pilot",
        "AUTH_BYPASS": "false",
        "GOOGLE_CLOUD_PROJECT": "tars-pilot-proj",
        "FIREBASE_PROJECT_ID": "tars-pilot-proj",
        "AUTH_ORG_ID": "ella-internal",
        "AUTH_ALLOWED_EMAILS": "u1@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com,u5@ellaexecutivesearch.com",
        "K_SERVICE": "tars-service",
    }
    hosted_resolved_base = Settings(
        tars_runtime_mode="hosted-pilot",
        auth_bypass=False,
        google_cloud_project="tars-pilot-proj",
        firebase_project_id="tars-pilot-proj",
        auth_org_id="ella-internal",
        auth_allowed_emails="u1@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com,u5@ellaexecutivesearch.com",
    )

    local_raw_base = {
        "TARS_RUNTIME_MODE": "local",
        "AUTH_BYPASS": "false",
        "GOOGLE_CLOUD_PROJECT": "tars-local-proj",
        "FIREBASE_PROJECT_ID": "tars-local-proj",
        "AUTH_ORG_ID": "ella-internal",
        "AUTH_ALLOWED_EMAILS": "local@ellaexecutivesearch.com",
    }
    local_resolved_base = Settings(
        tars_runtime_mode="local",
        auth_bypass=False,
        google_cloud_project="tars-local-proj",
        firebase_project_id="tars-local-proj",
        auth_org_id="ella-internal",
        auth_allowed_emails="local@ellaexecutivesearch.com",
    )

    class CountingStages:
        def __init__(self):
            self.raw_plain = 0
            self.resolution = 0
            self.raw_bound = 0
            self.auth = 0
            self.existing_app = 0
            self.adc_probe = 0
            self.firebase_init = 0
            self.sm_inst = None
            self.firestore_inst = None
            self.gcs_inst = None
            self.gemini_inst = None
            self.orphan_detect = 0

    stages = CountingStages()

    def assert_no_early_publication():
        assert main.app.state.ready is False
        assert main.settings is None
        assert main.session_mgr is None
        assert main.firestore_storage is None
        assert main.gcs_storage is None
        assert main.gemini_client is None
        assert main.context_window is None
        assert len(main.context_windows) == 0
        assert len(main.pipeline_tasks) == 0

    orig_validate_raw = main.validate_raw_process_env
    def counting_validate_raw(raw_env, resolved=None):
        assert_no_early_publication()
        if resolved is None:
            stages.raw_plain += 1
        else:
            stages.raw_bound += 1
        return orig_validate_raw(raw_env, resolved_settings=resolved)

    orig_validate_auth = main.validate_auth_configuration
    def counting_validate_auth(s):
        assert_no_early_publication()
        stages.auth += 1
        return orig_validate_auth(s)

    def counting_validate_existing(s):
        assert_no_early_publication()
        stages.existing_app += 1

    async def counting_adc(proj=None):
        assert_no_early_publication()
        stages.adc_probe += 1

    def counting_firebase_init(gcp_proj, fb_proj=None):
        assert_no_early_publication()
        stages.firebase_init += 1

    class FakeSessionManager:
        def __init__(self, s):
            assert_no_early_publication()
            stages.sm_inst = self
        def detect_orphaned_sessions(self):
            assert_no_early_publication()
            stages.orphan_detect += 1
            return []

    class FakeFirestoreStorage:
        def __init__(self, s):
            assert_no_early_publication()
            stages.firestore_inst = self

    class FakeGCSStorage:
        def __init__(self, s):
            assert_no_early_publication()
            stages.gcs_inst = self

    class FakeGeminiClient:
        def __init__(self, s):
            assert_no_early_publication()
            stages.gemini_inst = self

    monkeypatch.setattr(main, "validate_raw_process_env", counting_validate_raw)
    monkeypatch.setattr(main, "validate_auth_configuration", counting_validate_auth)
    monkeypatch.setattr(main, "validate_existing_firebase_app", counting_validate_existing)
    monkeypatch.setattr(main, "probe_application_default_credentials", counting_adc)
    monkeypatch.setattr(main, "initialize_firebase_admin", counting_firebase_init)
    monkeypatch.setattr(main, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(main, "FirestoreStorage", FakeFirestoreStorage)
    monkeypatch.setattr(main, "GCSStorage", FakeGCSStorage)
    monkeypatch.setattr(main, "GeminiClient", FakeGeminiClient)

    # Failure scenarios
    raw_no_fb = dict(hosted_raw_base)
    del raw_no_fb["FIREBASE_PROJECT_ID"]

    raw_lower = dict(hosted_raw_base)
    del raw_lower["FIREBASE_PROJECT_ID"]
    raw_lower["firebase_project_id"] = "tars-pilot-proj"

    raw_col = dict(hosted_raw_base)
    raw_col["firebase_project_id"] = "tars-pilot-proj"

    raw_ks_local = dict(hosted_raw_base, TARS_RUNTIME_MODE="local")

    divergent_resolved = Settings(
        tars_runtime_mode="hosted-pilot",
        auth_bypass=False,
        google_cloud_project="other-proj",
        firebase_project_id="tars-pilot-proj",
        auth_org_id="ella-internal",
        auth_allowed_emails=hosted_resolved_base.auth_allowed_emails,
    )

    failure_scenarios = [
        # (raw_env, resolved_provider, expected_exact_error, expected_stage_counts)
        # 1. First raw gate missing FIREBASE_PROJECT_ID
        (raw_no_fb, lambda: hosted_resolved_base, "Missing required protected environment key in hosted mode: FIREBASE_PROJECT_ID", (1, 0, 0, 0)),
        # 2. Wrong-case
        (raw_lower, lambda: hosted_resolved_base, "Protected environment key must have exact uppercase spelling: FIREBASE_PROJECT_ID", (1, 0, 0, 0)),
        # 3. Collision
        (raw_col, lambda: hosted_resolved_base, "Duplicate or case-colliding environment key detected for logical key: FIREBASE_PROJECT_ID", (1, 0, 0, 0)),
        # 4. K_SERVICE / local conflict
        (raw_ks_local, lambda: hosted_resolved_base, "TARS_RUNTIME_MODE must be exact 'hosted-pilot' in hosted environment", (1, 0, 0, 0)),
        # 5. Second raw-binding valid field divergence
        (hosted_raw_base, lambda: divergent_resolved, "Resolved GOOGLE_CLOUD_PROJECT does not match raw environment", (1, 1, 1, 0)),
        # 6. Hosted resolution from absent raw
        ({}, lambda: hosted_resolved_base, "Hosted runtime mode cannot be resolved from secondary source when raw environment is local or absent", (1, 1, 1, 0)),
        # 7. Local raw resolving hosted secondary source
        (local_raw_base, lambda: hosted_resolved_base, "Hosted runtime mode cannot be resolved from secondary source when raw environment is local or absent", (1, 1, 1, 0)),
    ]

    for env_dict, res_fn, exp_err, (exp_raw_p, exp_res, exp_raw_b, exp_auth) in failure_scenarios:
        stages.raw_plain = 0
        stages.resolution = 0
        stages.raw_bound = 0
        stages.auth = 0
        stages.existing_app = 0
        stages.adc_probe = 0
        stages.firebase_init = 0
        stages.sm_inst = None
        stages.firestore_inst = None
        stages.gcs_inst = None
        stages.gemini_inst = None
        stages.orphan_detect = 0

        monkeypatch.setattr(main.os, "environ", env_dict)
        def counting_resolve():
            assert_no_early_publication()
            stages.resolution += 1
            return res_fn()
        monkeypatch.setattr(main, "resolve_settings_safely", counting_resolve)

        main.app.state.ready = False
        async def run_failing_lifespan():
            async with main.lifespan(main.app):
                pass

        with pytest.raises(AuthConfigurationError) as exc_info:
            asyncio.run(run_failing_lifespan())

        assert str(exc_info.value) == exp_err
        assert stages.raw_plain == exp_raw_p
        assert stages.resolution == exp_res
        assert stages.raw_bound == exp_raw_b
        assert stages.auth == exp_auth
        assert stages.existing_app == 0
        assert stages.adc_probe == 0
        assert stages.firebase_init == 0
        assert stages.sm_inst is None
        assert stages.firestore_inst is None
        assert stages.gcs_inst is None
        assert stages.gemini_inst is None
        assert stages.orphan_detect == 0

        assert main.app.state.ready is False
        assert main.settings is None
        assert main.session_mgr is None
        assert main.firestore_storage is None
        assert main.gcs_storage is None
        assert main.gemini_client is None
        assert main.context_window is None

    # 8. Hosted acceptance row: full execution and publication with exact identities
    stages.raw_plain = 0
    stages.resolution = 0
    stages.raw_bound = 0
    stages.auth = 0
    stages.existing_app = 0
    stages.adc_probe = 0
    stages.firebase_init = 0
    stages.sm_inst = None
    stages.firestore_inst = None
    stages.gcs_inst = None
    stages.gemini_inst = None
    stages.orphan_detect = 0

    monkeypatch.setattr(main.os, "environ", hosted_raw_base)
    def hosted_resolve():
        assert_no_early_publication()
        stages.resolution += 1
        return hosted_resolved_base
    monkeypatch.setattr(main, "resolve_settings_safely", hosted_resolve)

    async def run_hosted_success():
        async with main.lifespan(main.app):
            assert main.app.state.ready is True
            assert main.settings is hosted_resolved_base
            assert main.session_mgr is stages.sm_inst
            assert main.firestore_storage is stages.firestore_inst
            assert main.gcs_storage is stages.gcs_inst
            assert main.gemini_client is stages.gemini_inst
            assert main.context_window is None
            assert len(main.context_windows) == 0
            main.context_window = object()
            main.context_windows["s1"] = object()

    asyncio.run(run_hosted_success())

    assert stages.raw_plain == 1
    assert stages.resolution == 1
    assert stages.raw_bound == 1
    assert stages.auth == 1
    assert stages.existing_app == 1
    assert stages.adc_probe == 1
    assert stages.firebase_init == 1
    assert stages.orphan_detect == 1

    assert main.app.state.ready is False
    assert main.settings is None
    assert main.session_mgr is None
    assert main.firestore_storage is None
    assert main.gcs_storage is None
    assert main.gemini_client is None
    assert main.context_window is None
    assert len(main.context_windows) == 0

    # 9. Local acceptance row: full execution and publication with exact identities
    stages.raw_plain = 0
    stages.resolution = 0
    stages.raw_bound = 0
    stages.auth = 0
    stages.existing_app = 0
    stages.adc_probe = 0
    stages.firebase_init = 0
    stages.sm_inst = None
    stages.firestore_inst = None
    stages.gcs_inst = None
    stages.gemini_inst = None
    stages.orphan_detect = 0

    monkeypatch.setattr(main.os, "environ", local_raw_base)
    def local_resolve():
        assert_no_early_publication()
        stages.resolution += 1
        return local_resolved_base
    monkeypatch.setattr(main, "resolve_settings_safely", local_resolve)

    async def run_local_success():
        async with main.lifespan(main.app):
            assert main.app.state.ready is True
            assert main.settings is local_resolved_base
            assert main.session_mgr is stages.sm_inst
            assert main.firestore_storage is stages.firestore_inst
            assert main.gcs_storage is stages.gcs_inst
            assert main.gemini_client is stages.gemini_inst
            assert main.context_window is None
            assert len(main.context_windows) == 0
            main.context_window = object()
            main.context_windows["s1"] = object()

    asyncio.run(run_local_success())

    assert stages.raw_plain == 1
    assert stages.resolution == 1
    assert stages.raw_bound == 1
    assert stages.auth == 1
    assert stages.existing_app == 1
    assert stages.adc_probe == 1
    assert stages.firebase_init == 1
    assert stages.orphan_detect == 1

    assert main.app.state.ready is False
    assert main.settings is None
    assert main.session_mgr is None
    assert main.firestore_storage is None
    assert main.gcs_storage is None
    assert main.gemini_client is None
    assert main.context_window is None


# --- 3. Strict Allowlist Parsing ---

def test_parse_allowed_emails_success():
    raw = "a@ellaexecutivesearch.com, b.c+tag@ellaexecutivesearch.com , d-e@ellaexecutivesearch.com "
    res = parse_allowed_emails(raw)
    assert res == frozenset({
        "a@ellaexecutivesearch.com",
        "b.c+tag@ellaexecutivesearch.com",
        "d-e@ellaexecutivesearch.com",
    })


@pytest.mark.parametrize(
    "bad_input",
    [
        "",  # blank
        "   ",
        ",a@b.com",  # leading comma
        "a@b.com,",  # trailing comma
        "a@b.com,,b@c.com",  # double comma
        "a@b.com, A@B.com",  # case-insensitive duplicate
        "*@ellaexecutivesearch.com",  # wildcard local
        "a@*.com",  # wildcard domain
        "User <a@b.com>",  # display name
        "mailto:a@b.com",  # URI syntax
        '"quoted"@b.com',  # quoted string
        "a/b@c.com",  # slash
        "a\\b@c.com",  # backslash
        "a@b@c.com",  # multiple @
        "a@b.com\r\nb@c.com",  # CRLF injection
        "a\u00a0@b.com",  # NBSP
        "a@b\u200b.com",  # zero-width space
        "a\u212a@b.com",  # non-ASCII confusable
        ".a@b.com",  # local starting with dot
        "a.@b.com",  # local ending with dot
        "a..b@c.com",  # consecutive dots in local
        "a@-b.com",  # domain label starting with dash
        "a@b-.com",  # domain label ending with dash
        "a@singlelabel",  # domain without dot
        "a" * 250 + "@b.com",  # item > 254 chars
        "a" * 65 + "@b.com",  # local > 64 chars
        "a@" + "b" * 64 + ".com",  # domain label > 63 chars
    ],
)
def test_parse_allowed_emails_adversarial_rejections(bad_input):
    with pytest.raises(AuthConfigurationError) as exc_info:
        parse_allowed_emails(bad_input)
    # Ensure error is content-free (no address leaked)
    if bad_input.strip():
        assert bad_input.strip() not in str(exc_info.value)
    assert "@" not in str(exc_info.value)


# --- 4. Runtime Auth Validator ---

def test_validate_auth_configuration_hosted_pilot_success():
    s = Settings(
        tars_runtime_mode="hosted-pilot",
        auth_bypass=False,
        google_cloud_project="tars-pilot-123",
        firebase_project_id="tars-pilot-123",
        auth_org_id="ella-internal",
        auth_allowed_emails="u1@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com,u5@ellaexecutivesearch.com",
    )
    validate_auth_configuration(s)


@pytest.mark.parametrize(
    "overrides,match_pattern",
    [
        ({"auth_bypass": True}, "AUTH_BYPASS is strictly forbidden in hosted-pilot mode"),
        ({"auth_org_id": "other-org"}, "auth_org_id must be ella-internal in hosted-pilot mode"),
        ({"auth_org_id": "ella_internal"}, "Invalid auth_org_id format"),
        ({"firebase_project_id": "other-proj"}, "firebase_project_id must match google_cloud_project"),
        ({"google_cloud_project": "BAD_PROJECT"}, "Invalid google_cloud_project format"),
        ({"google_cloud_project": "proj"}, "Invalid google_cloud_project format"),  # too short
        ({"google_cloud_project": "p" * 31}, "Invalid google_cloud_project format"),  # too long
        (
            {"auth_allowed_emails": "u1@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com"},
            "Hosted pilot requires exactly 5 authorized recruiter accounts",
        ),  # 4 emails
        (
            {"auth_allowed_emails": "u1@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com,u5@ellaexecutivesearch.com,u6@ellaexecutivesearch.com"},
            "Hosted pilot requires exactly 5 authorized recruiter accounts",
        ),  # 6 emails
        (
            {"auth_allowed_emails": "u1@otherdomain.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com,u5@ellaexecutivesearch.com"},
            "All authorized accounts must belong to the corporate domain ellaexecutivesearch.com",
        ),  # wrong domain
    ],
)
def test_validate_auth_configuration_hosted_pilot_rejections(overrides, match_pattern):
    base = {
        "tars_runtime_mode": "hosted-pilot",
        "auth_bypass": False,
        "google_cloud_project": "tars-pilot-123",
        "firebase_project_id": "tars-pilot-123",
        "auth_org_id": "ella-internal",
        "auth_allowed_emails": "u1@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com,u5@ellaexecutivesearch.com",
    }
    base.update(overrides)
    s = Settings.model_construct(**base)
    with pytest.raises(AuthConfigurationError, match=match_pattern):
        validate_auth_configuration(s)


# --- 5. Firebase Admin and ID-Token Admission Hardening ---

def test_verify_bearer_token_content_free_exception_on_provider_error():
    secret_sentinel = "SECRET_FIREBASE_API_KEY_12345"
    s = Settings(
        tars_runtime_mode="local",
        auth_bypass=False,
        google_cloud_project="tars-test",
        auth_allowed_emails="recruiter@ellaexecutivesearch.com",
        auth_org_id="ella-internal",
    )
    with patch("backend.auth.firebase_auth.verify_id_token", side_effect=RuntimeError(secret_sentinel)):
        with pytest.raises(AuthenticationError) as exc_info:
            verify_bearer_token("Bearer some-token", s)

    # Prove sentinel is absent from __cause__, __context__, and str/repr
    assert secret_sentinel not in str(exc_info.value)
    assert secret_sentinel not in repr(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def make_valid_claims(**overrides) -> dict:
    base = {
        "sub": "uid-1",
        "uid": "uid-1",
        "email": "a@b.com",
        "email_verified": True,
        "aud": "tars-test",
        "iss": "https://securetoken.google.com/tars-test",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "bad_claims",
    [
        None,  # non-mapping
        "claims-string",
        ["claims-list"],
        make_valid_claims(uid=12345),  # numeric uid
        make_valid_claims(sub=12345),  # numeric sub
        make_valid_claims(email=["a@b.com"]),  # list email
        make_valid_claims(aud=["tars-test"]),  # list aud
        make_valid_claims(iss={"url": "https://securetoken.google.com/tars-test"}),  # structured iss
        make_valid_claims(uid="uid 1"),  # space in uid
        make_valid_claims(uid="uid\x001"),  # control in uid
        make_valid_claims(email="a@b.com,c@d.com"),  # comma email
        make_valid_claims(sub="sub-mismatch"),  # sub/uid mismatch
        make_valid_claims(iss="https://session.firebase.google.com/tars-test"),  # session cookie issuer rejected
        dict((k, v) for k, v in make_valid_claims().items() if k != "sub"),  # missing sub
        make_valid_claims(sub=""),  # blank sub
        make_valid_claims(sub=" uid-1 "),  # padded sub
        make_valid_claims(sub="uid\x001"),  # control sub
        make_valid_claims(sub="uid-ñ-1"),  # non-ASCII sub
    ],
)
def test_verify_bearer_token_rejects_malformed_claims(bad_claims):
    s = Settings(
        tars_runtime_mode="local",
        auth_bypass=False,
        google_cloud_project="tars-test",
        auth_allowed_emails="a@b.com",
        auth_org_id="ella-internal",
    )
    with patch("backend.auth.firebase_auth.verify_id_token", return_value=bad_claims):
        with pytest.raises(AuthenticationError):
            verify_bearer_token("Bearer token", s)


def test_verify_bearer_token_positive_sub_cases():
    s = Settings(
        tars_runtime_mode="local",
        auth_bypass=False,
        google_cloud_project="tars-test",
        auth_allowed_emails="a@b.com",
        auth_org_id="ella-internal",
    )
    # 1. uid absent positive (sub used as principal UID)
    claims_no_uid = dict((k, v) for k, v in make_valid_claims().items() if k != "uid")
    with patch("backend.auth.firebase_auth.verify_id_token", return_value=claims_no_uid):
        user = verify_bearer_token("Bearer token", s)
        assert user.uid == "uid-1"

    # 2. Dotted sub positive
    claims_dotted = make_valid_claims(sub="user.123.abc", uid="user.123.abc")
    with patch("backend.auth.firebase_auth.verify_id_token", return_value=claims_dotted):
        user = verify_bearer_token("Bearer token", s)
        assert user.uid == "user.123.abc"


# --- 6. Firebase Admin Reused App Project Binding ---

def test_initialize_firebase_admin_checks_project_binding_without_touching_lazy_property():
    class SentinelApp:
        def __init__(self, project_id_option: str | None):
            self._options = {"projectId": project_id_option} if project_id_option else {}

        @property
        def project_id(self):
            raise AssertionError("Lazy App.project_id property must not be accessed during binding check!")

    s = Settings(
        tars_runtime_mode="local",
        auth_bypass=False,
        google_cloud_project="tars-test",
        firebase_project_id="tars-test",
        auth_allowed_emails="a@b.com",
        auth_org_id="ella-internal",
    )

    # 1. Matching app succeeds
    matching_app = SentinelApp("tars-test")
    with patch("backend.auth.firebase_admin.get_app", return_value=matching_app):
        initialize_firebase_admin(s.google_cloud_project, s.firebase_project_id)

    # 2. Mismatched app raises AuthConfigurationError
    mismatched_app = SentinelApp("wrong-project")
    with patch("backend.auth.firebase_admin.get_app", return_value=mismatched_app):
        with pytest.raises(AuthConfigurationError):
            initialize_firebase_admin(s.google_cloud_project, s.firebase_project_id)


def test_cors_settings_resolution_sentinel_scrubbing(monkeypatch):
    sentinel = "SUPER_SECRET_CORS_SENTINEL_789"
    with patch("backend.config.CorsSettings", side_effect=ValueError(sentinel)):
        with pytest.raises(AuthConfigurationError) as exc_info:
            from backend.config import resolve_cors_settings_safely
            resolve_cors_settings_safely()
        err_str = str(exc_info.value)
        err_repr = repr(exc_info.value)
        assert sentinel not in err_str
        assert sentinel not in err_repr
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None


def test_firebase_app_validation_sentinel_scrubbing():
    sentinel = "SUPER_SECRET_FIREBASE_APP_SENTINEL_999"
    s = Settings(
        tars_runtime_mode="local",
        auth_bypass=False,
        google_cloud_project="tars-test",
        firebase_project_id="tars-test",
        auth_allowed_emails="a@b.com",
        auth_org_id="ella-internal",
    )

    # Subclass whose __str__ raises with sentinel
    class MaliciousValueError(ValueError):
        def __str__(self):
            raise RuntimeError(sentinel)

    with patch("backend.auth.firebase_admin.get_app", side_effect=MaliciousValueError("boom")):
        with pytest.raises(AuthConfigurationError) as exc_info:
            from backend.auth import validate_existing_firebase_app
            validate_existing_firebase_app(s)
        assert sentinel not in str(exc_info.value)
        assert sentinel not in repr(exc_info.value)
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None

    # Unrelated ValueError merely mentioning substring
    with patch("backend.auth.firebase_admin.get_app", side_effect=ValueError(f"Unrelated {sentinel} default Firebase app does not exist")):
        with pytest.raises(AuthConfigurationError) as exc_info:
            from backend.auth import validate_existing_firebase_app
            validate_existing_firebase_app(s)
        assert sentinel not in str(exc_info.value)
        assert sentinel not in repr(exc_info.value)
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None


# --- 7. Malicious Adapter Postconditions on List Routes ---

class PhaseAwareRecordMapping(Mapping):
    def __init__(self, data: Mapping[str, Any] | None):
        self._data = dict(data) if data is not None else {}
        self.ownership_reads = {"ownerId": 0, "orgId": 0}
        self.post_ownership_reads: dict[str, int] = {}
        self.ownership_passed = False

    def __getitem__(self, key: str) -> Any:
        if key in ("ownerId", "orgId"):
            if self.ownership_passed:
                self.post_ownership_reads[key] = self.post_ownership_reads.get(key, 0) + 1
            else:
                self.ownership_reads[key] += 1
            return self._data[key]
        if not self.ownership_passed:
            raise AssertionError(f"PREMATURE_RECORD_ACCESS: {key} accessed before ownership verification passed")
        self.post_ownership_reads[key] = self.post_ownership_reads.get(key, 0) + 1
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        if key in ("ownerId", "orgId"):
            if self.ownership_passed:
                self.post_ownership_reads[key] = self.post_ownership_reads.get(key, 0) + 1
            else:
                self.ownership_reads[key] += 1
            return self._data.get(key, default)
        if not self.ownership_passed:
            raise AssertionError(f"PREMATURE_RECORD_ACCESS: {key} accessed before ownership verification passed")
        self.post_ownership_reads[key] = self.post_ownership_reads.get(key, 0) + 1
        return self._data.get(key, default)

    def __iter__(self):
        if not self.ownership_passed:
            raise AssertionError("PREMATURE_RECORD_ACCESS: iteration before ownership verification passed")
        self.post_ownership_reads["__iter__"] = self.post_ownership_reads.get("__iter__", 0) + 1
        return iter(self._data)

    def __len__(self):
        if not self.ownership_passed:
            raise AssertionError("PREMATURE_RECORD_ACCESS: length before ownership verification passed")
        self.post_ownership_reads["__len__"] = self.post_ownership_reads.get("__len__", 0) + 1
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        if key in ("ownerId", "orgId"):
            key_str = str(key)
            if self.ownership_passed:
                self.post_ownership_reads[key_str] = self.post_ownership_reads.get(key_str, 0) + 1
            else:
                self.ownership_reads[key_str] += 1
        elif not self.ownership_passed:
            raise AssertionError(f"PREMATURE_RECORD_ACCESS: {key} checked before ownership verification passed")
        else:
            self.post_ownership_reads[str(key)] = self.post_ownership_reads.get(str(key), 0) + 1
        return key in self._data


def test_list_sessions_and_recent_interviews_adapter_causality(monkeypatch, caplog):
    """Adapter causality: single storage call, wrapped ownership, real deserialization, and strict failure containment."""
    import traceback
    from backend.sessions.review import RecentInterview

    valid_record = {
        "id": "s1",
        "mode": "interview",
        "status": "completed",
        "ownerId": "uid-a",
        "orgId": "ella-internal",
        "title": "Valid Interview",
        "startedAt": "2026-08-24T12:00:00Z",
        "endedAt": "2026-08-24T12:45:00Z",
        "lastActive": "2026-08-24T12:45:00Z",
        "createdAt": "2026-08-24T12:00:00Z",
        "updatedAt": "2026-08-24T12:45:00Z",
        "transcript": [],
        "analysis": {},
        "notes": [],
    }

    class SpyFirestore:
        def __init__(self, records):
            self.records = records
            self.calls: list[dict] = []

        async def list_sessions(self, **kwargs):
            self.calls.append(kwargs)
            return self.records

    orig_assert_persisted = main._assert_persisted_session_access
    ownership_calls = 0
    def wrapped_ownership(record):
        nonlocal ownership_calls
        ownership_calls += 1
        res = orig_assert_persisted(record)
        if isinstance(record, PhaseAwareRecordMapping):
            record.ownership_passed = True
        return res

    orig_deserialize = main.deserialize_session
    deserialize_calls = 0
    def wrapped_deserialize(session_id, record):
        nonlocal deserialize_calls
        deserialize_calls += 1
        return orig_deserialize(session_id, record)

    orig_build_recent = main.build_recent_interview
    build_calls = 0
    def wrapped_build(session):
        nonlocal build_calls
        build_calls += 1
        return orig_build_recent(session)

    orig_corrupt = main.corrupt_recent_interview
    corrupt_calls = 0
    def wrapped_corrupt(session_id):
        nonlocal corrupt_calls
        corrupt_calls += 1
        return orig_corrupt(session_id)

    orig_model_dump = RecentInterview.model_dump
    model_dump_calls = 0
    def wrapped_model_dump(self, *a, **k):
        nonlocal model_dump_calls
        model_dump_calls += 1
        return orig_model_dump(self, *a, **k)

    monkeypatch.setattr(main, "_assert_persisted_session_access", wrapped_ownership)
    monkeypatch.setattr(main, "deserialize_session", wrapped_deserialize)
    monkeypatch.setattr(main, "build_recent_interview", wrapped_build)
    monkeypatch.setattr(main, "corrupt_recent_interview", wrapped_corrupt)
    monkeypatch.setattr(RecentInterview, "model_dump", wrapped_model_dump)

    token = main.set_current_auth(AuthContext("uid-a", "a@example.com", "ella-internal"))
    enforced = main.set_auth_enforced()

    def assert_sentinel_hygiene(exc, sentinel, caplog_text):
        visited = set()
        curr = exc
        while curr is not None and id(curr) not in visited:
            visited.add(id(curr))
            assert sentinel not in str(curr)
            assert sentinel not in repr(curr)
            formatted = "".join(traceback.format_exception(type(curr), curr, curr.__traceback__))
            assert sentinel not in formatted
            curr = getattr(curr, "__cause__", None) or getattr(curr, "__context__", None)
        assert sentinel not in caplog_text

    try:
        # Causal assertion: accessing mode/id before ownership verification raises AssertionError
        premature_spy = PhaseAwareRecordMapping(valid_record)
        with pytest.raises(AssertionError, match="PREMATURE_RECORD_ACCESS: mode accessed before"):
            _ = premature_spy.get("mode")
        with pytest.raises(AssertionError, match="^PREMATURE_RECORD_ACCESS: mode accessed before ownership verification passed$"):
            _ = premature_spy["mode"]
        with pytest.raises(AssertionError, match="^PREMATURE_RECORD_ACCESS: mode checked before ownership verification passed$"):
            "mode" in premature_spy
        with pytest.raises(AssertionError, match="^PREMATURE_RECORD_ACCESS: iteration before ownership verification passed$"):
            iter(premature_spy)
        with pytest.raises(AssertionError, match="^PREMATURE_RECORD_ACCESS: length before ownership verification passed$"):
            len(premature_spy)
        assert premature_spy.ownership_reads == {"ownerId": 0, "orgId": 0}
        assert premature_spy.post_ownership_reads == {}
        premature_spy.ownership_passed = True
        assert premature_spy.get("mode") == "interview"
        assert premature_spy["mode"] == "interview"
        assert "mode" in premature_spy
        assert tuple(iter(premature_spy)) == tuple(valid_record)
        assert len(premature_spy) == len(valid_record)
        assert premature_spy.post_ownership_reads == {"mode": 3, "__iter__": 1, "__len__": 1}
        assert premature_spy.ownership_reads == {"ownerId": 0, "orgId": 0}

        # 1. Positive list_sessions: storage=1 with exact kwargs, ownership=1, successful return, 0 deserializer/formatter
        pos_rec_sess = PhaseAwareRecordMapping(valid_record)
        store_pos = SpyFirestore([pos_rec_sess])
        monkeypatch.setattr(main, "firestore_storage", store_pos)
        ownership_calls = 0
        deserialize_calls = 0
        build_calls = 0
        corrupt_calls = 0
        model_dump_calls = 0

        res_sessions = asyncio.run(main.list_sessions())
        assert store_pos.calls == [{"owner_id": "uid-a", "org_id": "ella-internal"}]
        assert ownership_calls == 1
        assert pos_rec_sess.ownership_passed is True
        assert pos_rec_sess.ownership_reads == {"ownerId": 1, "orgId": 1}
        assert pos_rec_sess.ownership_reads["ownerId"] == 1
        assert pos_rec_sess.ownership_reads["orgId"] == 1
        assert pos_rec_sess.post_ownership_reads == {}
        assert len(pos_rec_sess.post_ownership_reads) == 0
        assert deserialize_calls == 0
        assert build_calls == 0
        assert corrupt_calls == 0
        assert model_dump_calls == 0
        assert len(res_sessions["sessions"]) == 1
        assert res_sessions["sessions"][0] is pos_rec_sess

        # 2. Positive list_recent_interviews: storage=1 with exact kwargs, ownership=1, real deserialize=1, build=1, corrupt=0, model_dump=1
        pos_rec_rec = PhaseAwareRecordMapping(valid_record)
        ownership_calls = 0
        deserialize_calls = 0
        build_calls = 0
        corrupt_calls = 0
        model_dump_calls = 0
        store_pos = SpyFirestore([pos_rec_rec])
        monkeypatch.setattr(main, "firestore_storage", store_pos)

        res_interviews = asyncio.run(main.list_recent_interviews())
        assert store_pos.calls == [{"owner_id": "uid-a", "org_id": "ella-internal"}]
        assert ownership_calls == 1
        assert pos_rec_rec.ownership_passed is True
        assert pos_rec_rec.ownership_reads == {"ownerId": 1, "orgId": 1}
        assert pos_rec_rec.ownership_reads["ownerId"] == 1
        assert pos_rec_rec.ownership_reads["orgId"] == 1
        assert pos_rec_rec.post_ownership_reads.get("ownerId", 0) == 1
        assert pos_rec_rec.post_ownership_reads.get("orgId", 0) == 1
        assert pos_rec_rec.post_ownership_reads.get("mode", 0) == 2
        assert pos_rec_rec.post_ownership_reads.get("id", 0) == 1
        assert deserialize_calls == 1
        assert build_calls == 1
        assert corrupt_calls == 0
        assert model_dump_calls == 1
        assert len(res_interviews["interviews"]) == 1
        assert res_interviews["interviews"][0]["id"] == "s1"

        # 3. Negative inventory: None, string, list, missing/wrong ownerId, wrong orgId
        negative_rows = [
            ("none", None, "SENTINEL_NONE_98765"),
            ("string", "string-record-SENTINEL_STR_98765", "SENTINEL_STR_98765"),
            ("list", ["list-record-SENTINEL_LIST_98765"], "SENTINEL_LIST_98765"),
            ("missing_owner", lambda s: PhaseAwareRecordMapping({"id": "s1", "orgId": "ella-internal", "title": s}), "SENTINEL_MISS_OWNER_98765"),
            ("wrong_owner", lambda s: PhaseAwareRecordMapping(dict(valid_record, ownerId="uid-other", title=s)), "SENTINEL_WRONG_OWNER_98765"),
            ("wrong_org", lambda s: PhaseAwareRecordMapping(dict(valid_record, orgId="other-org", title=s)), "SENTINEL_WRONG_ORG_98765"),
        ]
        expected_ownership_reads = {
            "missing_owner": {"ownerId": 1, "orgId": 0},
            "wrong_owner": {"ownerId": 1, "orgId": 0},
            "wrong_org": {"ownerId": 1, "orgId": 1},
        }

        for name, rec_factory, sentinel in negative_rows:
            # A. list_sessions negative with fresh mapping
            caplog.clear()
            bad_rec_sess = rec_factory(sentinel) if callable(rec_factory) else rec_factory
            store_neg_sess = SpyFirestore([bad_rec_sess])
            monkeypatch.setattr(main, "firestore_storage", store_neg_sess)

            ownership_calls = 0
            deserialize_calls = 0
            build_calls = 0
            corrupt_calls = 0
            model_dump_calls = 0

            with pytest.raises(HTTPException) as exc_sess:
                asyncio.run(main.list_sessions())
            assert exc_sess.value.status_code == 404
            assert exc_sess.value.detail == "Session not found"
            assert exc_sess.value.__cause__ is None
            assert exc_sess.value.__context__ is None
            assert store_neg_sess.calls == [{"owner_id": "uid-a", "org_id": "ella-internal"}]
            assert ownership_calls == 1
            if isinstance(bad_rec_sess, PhaseAwareRecordMapping):
                assert bad_rec_sess.ownership_passed is False
                assert bad_rec_sess.ownership_reads == expected_ownership_reads[name]
                assert bad_rec_sess.post_ownership_reads == {}
                assert len(bad_rec_sess.post_ownership_reads) == 0
            assert deserialize_calls == 0
            assert build_calls == 0
            assert corrupt_calls == 0
            assert model_dump_calls == 0
            assert_sentinel_hygiene(exc_sess.value, sentinel, caplog.text)

            # B. list_recent_interviews negative with fresh mapping
            caplog.clear()
            bad_rec_recent = rec_factory(sentinel) if callable(rec_factory) else rec_factory
            store_neg_recent = SpyFirestore([bad_rec_recent])
            monkeypatch.setattr(main, "firestore_storage", store_neg_recent)

            ownership_calls = 0
            deserialize_calls = 0
            build_calls = 0
            corrupt_calls = 0
            model_dump_calls = 0

            with pytest.raises(HTTPException) as exc_recent:
                asyncio.run(main.list_recent_interviews())
            assert exc_recent.value.status_code == 404
            assert exc_recent.value.detail == "Session not found"
            assert exc_recent.value.__cause__ is None
            assert exc_recent.value.__context__ is None
            assert store_neg_recent.calls == [{"owner_id": "uid-a", "org_id": "ella-internal"}]
            assert ownership_calls == 1
            if isinstance(bad_rec_recent, PhaseAwareRecordMapping):
                assert bad_rec_recent.ownership_passed is False
                assert bad_rec_recent.ownership_reads == expected_ownership_reads[name]
                assert bad_rec_recent.post_ownership_reads == {}
                assert len(bad_rec_recent.post_ownership_reads) == 0
            assert deserialize_calls == 0
            assert build_calls == 0
            assert corrupt_calls == 0
            assert model_dump_calls == 0
            assert_sentinel_hygiene(exc_recent.value, sentinel, caplog.text)

    finally:
        main.reset_current_auth(token)
        main.reset_auth_enforced(enforced)


# --- 8. Offline check_auth_setup Validator ---

REQUIRED_EXACT_VARS_AUTH = (
    "TARS_RUNTIME_MODE",
    "GOOGLE_CLOUD_PROJECT",
    "FIREBASE_PROJECT_ID",
    "AUTH_ORG_ID",
    "AUTH_ALLOWED_EMAILS",
    "AUTH_BYPASS",
    "NEXT_PUBLIC_AUTH_BYPASS",
    "NEXT_PUBLIC_FIREBASE_API_KEY",
    "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN",
    "NEXT_PUBLIC_FIREBASE_PROJECT_ID",
    "NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET",
    "NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID",
    "NEXT_PUBLIC_FIREBASE_APP_ID",
    "NEXT_PUBLIC_API_URL",
    "NEXT_PUBLIC_WS_URL",
    "NEXT_PUBLIC_WS_STREAM_URL",
)


def valid_setup_env() -> dict[str, str]:
    return {
        "TARS_RUNTIME_MODE": "hosted-pilot",
        "GOOGLE_CLOUD_PROJECT": "tars-hosted-pilot",
        "FIREBASE_PROJECT_ID": "tars-hosted-pilot",
        "AUTH_ORG_ID": "ella-internal",
        "AUTH_ALLOWED_EMAILS": "u1@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com,u5@ellaexecutivesearch.com",
        "AUTH_BYPASS": "false",
        "NEXT_PUBLIC_AUTH_BYPASS": "0",
        "NEXT_PUBLIC_FIREBASE_API_KEY": "AIza" + "A" * 35,
        "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN": "auth.ellaexecutivesearch.com",
        "NEXT_PUBLIC_FIREBASE_PROJECT_ID": "tars-hosted-pilot",
        "NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET": "bucket.ellaexecutivesearch.com",
        "NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID": "123456789012",
        "NEXT_PUBLIC_FIREBASE_APP_ID": "1:123456789012:web:abcdef0123456789",
        "NEXT_PUBLIC_API_URL": "https://backend.ellaexecutivesearch.com",
        "NEXT_PUBLIC_WS_URL": "wss://backend.ellaexecutivesearch.com/ws",
        "NEXT_PUBLIC_WS_STREAM_URL": "wss://backend.ellaexecutivesearch.com/api/stream/native",
    }


def test_check_auth_setup_success():
    ok, msg = check_auth_setup(valid_setup_env())
    assert ok is True
    assert "count 5" in msg
    assert "no provider or real account was contacted" in msg.lower()


def test_check_auth_setup_missing_casing_and_duplicates():
    base = valid_setup_env()

    # 1. Missing each required variable one by one
    for req_key in REQUIRED_EXACT_VARS_AUTH:
        env = dict(base)
        del env[req_key]
        ok, msg = check_auth_setup(env)
        assert ok is False
        assert msg == f"FAIL: missing required environment variable {req_key}"

    # 2. Lowercase replacement alone
    for req_key in REQUIRED_EXACT_VARS_AUTH:
        env = dict(base)
        del env[req_key]
        env[req_key.lower()] = base[req_key]
        ok, msg = check_auth_setup(env)
        assert ok is False
        assert msg == f"FAIL: {req_key} must have exact uppercase spelling"

    # 3. Mixed-case replacement alone
    for req_key in REQUIRED_EXACT_VARS_AUTH:
        env = dict(base)
        del env[req_key]
        env[req_key[0].upper() + req_key[1:].lower()] = base[req_key]
        ok, msg = check_auth_setup(env)
        assert ok is False
        assert msg == f"FAIL: {req_key} must have exact uppercase spelling"

    # 4. Duplicate / colliding alias added alongside uppercase (lowercase and mixed-case)
    for req_key in REQUIRED_EXACT_VARS_AUTH:
        env = dict(base)
        env[req_key.lower()] = base[req_key]
        ok, msg = check_auth_setup(env)
        assert ok is False
        assert msg == f"FAIL: duplicate/colliding environment variable for logical key {req_key}"

        env_mixed = dict(base)
        env_mixed[req_key[0].upper() + req_key[1:].lower()] = base[req_key]
        ok, msg = check_auth_setup(env_mixed)
        assert ok is False
        assert msg == f"FAIL: duplicate/colliding environment variable for logical key {req_key}"

    # 5. K_SERVICE collision, lowercase, and mixed-case
    env_k_service_lower = dict(base)
    env_k_service_lower["k_service"] = "tars-service"
    ok, msg = check_auth_setup(env_k_service_lower)
    assert ok is False
    assert msg == "FAIL: K_SERVICE must have exact uppercase spelling"

    env_k_service_mixed = dict(base)
    env_k_service_mixed["k_Service"] = "tars-service"
    ok, msg = check_auth_setup(env_k_service_mixed)
    assert ok is False
    assert msg == "FAIL: K_SERVICE must have exact uppercase spelling"

    env_k_service_dup_lower = dict(base)
    env_k_service_dup_lower["K_SERVICE"] = "tars-service"
    env_k_service_dup_lower["k_service"] = "tars-service"
    ok, msg = check_auth_setup(env_k_service_dup_lower)
    assert ok is False
    assert msg == "FAIL: duplicate/colliding environment variable for logical key K_SERVICE"

    env_k_service_dup_mixed = dict(base)
    env_k_service_dup_mixed["K_SERVICE"] = "tars-service"
    env_k_service_dup_mixed["k_Service"] = "tars-service"
    ok, msg = check_auth_setup(env_k_service_dup_mixed)
    assert ok is False
    assert msg == "FAIL: duplicate/colliding environment variable for logical key K_SERVICE"


@pytest.mark.parametrize(
    "mutate_key,bad_val,expected_reason",
    [
        ("TARS_RUNTIME_MODE", "local", "FAIL: TARS_RUNTIME_MODE must be exact 'hosted-pilot'"),
        ("AUTH_BYPASS", "true", "FAIL: AUTH_BYPASS must be exact 'false'"),
        ("NEXT_PUBLIC_AUTH_BYPASS", "1", "FAIL: NEXT_PUBLIC_AUTH_BYPASS must be exact '0'"),
        ("AUTH_ORG_ID", "wrong-org", "FAIL: AUTH_ORG_ID must be exact 'ella-internal'"),
        ("AUTH_ORG_ID", "INVALID_ORG", "FAIL: AUTH_ORG_ID has invalid syntax"),
    ],
)
def test_check_auth_setup_mutations_rejected(mutate_key, bad_val, expected_reason):
    env = valid_setup_env()
    env[mutate_key] = bad_val
    ok, msg = check_auth_setup(env)
    assert ok is False
    assert msg == expected_reason


@pytest.mark.parametrize(
    "proj_key",
    ["GOOGLE_CLOUD_PROJECT", "FIREBASE_PROJECT_ID", "NEXT_PUBLIC_FIREBASE_PROJECT_ID"],
)
@pytest.mark.parametrize(
    "bad_proj",
    [
        "abcde",  # length 5 too short
        "a" + "b" * 29 + "c",  # length 31 too long
        "1abcde",  # digit start
        "-abcde",  # hyphen start
        "abcde-",  # hyphen end
        " abcdef ",  # whitespace padding
        "abc\x00def",  # control character
        "abc/def",  # slash
        "Abcdef",  # uppercase
        "abc-déf-ghi",  # Unicode
    ],
)
def test_check_auth_setup_project_id_matrix(proj_key, bad_proj):
    env = valid_setup_env()
    env[proj_key] = bad_proj
    ok, msg = check_auth_setup(env)
    assert ok is False
    assert msg == f"FAIL: {proj_key} has invalid syntax"


def test_check_auth_setup_project_id_mismatch():
    base = valid_setup_env()
    for proj_key in ("GOOGLE_CLOUD_PROJECT", "FIREBASE_PROJECT_ID", "NEXT_PUBLIC_FIREBASE_PROJECT_ID"):
        env = dict(base)
        env[proj_key] = "tars-other-proj"
        ok, msg = check_auth_setup(env)
        assert ok is False
        assert msg == "FAIL: GOOGLE_CLOUD_PROJECT, FIREBASE_PROJECT_ID, and NEXT_PUBLIC_FIREBASE_PROJECT_ID must match"


def test_check_auth_setup_allowlist_matrix():
    base = valid_setup_env()
    # Positive 5 accounts
    ok, msg = check_auth_setup(base)
    assert ok is True

    # 4 accounts
    env_4 = dict(base, AUTH_ALLOWED_EMAILS="u1@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com")
    ok, msg = check_auth_setup(env_4)
    assert ok is False
    assert msg == "FAIL: AUTH_ALLOWED_EMAILS must contain exactly 5 accounts"

    # 6 accounts
    env_6 = dict(base, AUTH_ALLOWED_EMAILS="u1@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com,u5@ellaexecutivesearch.com,u6@ellaexecutivesearch.com")
    ok, msg = check_auth_setup(env_6)
    assert ok is False
    assert msg == "FAIL: AUTH_ALLOWED_EMAILS must contain exactly 5 accounts"

    # Duplicate
    env_dup = dict(base, AUTH_ALLOWED_EMAILS="u1@ellaexecutivesearch.com,u1@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com")
    ok, msg = check_auth_setup(env_dup)
    assert ok is False
    assert msg == "FAIL: AUTH_ALLOWED_EMAILS syntax is invalid"

    # Wrong domain
    env_domain = dict(base, AUTH_ALLOWED_EMAILS="u1@ellaexecutivesearch.com,u2@ellaexecutivesearch.com,u3@ellaexecutivesearch.com,u4@ellaexecutivesearch.com,u5@otherdomain.com")
    ok, msg = check_auth_setup(env_domain)
    assert ok is False
    assert msg == "FAIL: all AUTH_ALLOWED_EMAILS must belong to corporate domain ellaexecutivesearch.com"


# --- 9. Multi-Stage Dockerfile Static Guard ---

def test_dockerfile_multi_stage_evaluation_rejects_safe_values_only_in_early_stage():
    safe_baseline_dockerfile = """
    FROM python:3.12-slim AS builder
    RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 libportaudio2 && rm -rf /var/lib/apt/lists/*
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TARS_RUNTIME_MODE=hosted-pilot AUTH_BYPASS=false
    RUN useradd -r -u 1001 appuser

    FROM python:3.12-slim
    RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 libportaudio2 && rm -rf /var/lib/apt/lists/*
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TARS_RUNTIME_MODE=hosted-pilot AUTH_BYPASS=false
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
    """
    # 1. Prove complete safe baseline passes
    _validate_dockerfile_contract(_parse_dockerfile_instructions(safe_baseline_dockerfile))

    # 2. Mutate ONLY the final stage ENV (removing TARS_RUNTIME_MODE and AUTH_BYPASS)
    mutated_dockerfile = """
    FROM python:3.12-slim AS builder
    RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 libportaudio2 && rm -rf /var/lib/apt/lists/*
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TARS_RUNTIME_MODE=hosted-pilot AUTH_BYPASS=false
    RUN useradd -r -u 1001 appuser

    FROM python:3.12-slim
    RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 libportaudio2 && rm -rf /var/lib/apt/lists/*
    ENV HOST_AUDIO_CAPTURE_ENABLED=false AUDIO_BACKUP_ENABLED=false PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
    RUN useradd -r -u 1001 appuser
    USER appuser
    CMD ["sh", "-c", "exec python -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
    """
    with pytest.raises(AssertionError):
        _validate_dockerfile_contract(_parse_dockerfile_instructions(mutated_dockerfile))


# --- 10. Root .env.example Static Guard & Causal Probes ---

def test_env_example_requires_tars_runtime_mode_local():
    example_path = REPO_ROOT / ".env.example"
    assert example_path.exists()
    content = example_path.read_text(encoding="utf-8")
    pairs = _parse_env_assignment_pairs(content)
    mode_pairs = [v for k, v in pairs if k.upper() == "TARS_RUNTIME_MODE"]
    assert len(mode_pairs) == 1, "Expected exactly one TARS_RUNTIME_MODE entry in .env.example"
    assert mode_pairs[0] == "local"


def test_dotenv_syntax_guard_probes_causal():
    valid_root = (
        "TARS_RUNTIME_MODE=local\n"
        "AUTH_ALLOWED_EMAILS=authorized-recruiter@example.com\n"
        "AUTH_BYPASS=false\n"
        "CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3003,http://127.0.0.1:3000,http://127.0.0.1:3003,chrome-extension://fhnadcdkfgdlkomjpilmgehhpgmkjnga\n"
        "NEXT_PUBLIC_API_URL=http://localhost:8000\n"
        "NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws\n"
        "NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native\n"
        "NEXT_PUBLIC_AUTH_BYPASS=0"
    )
    valid_frontend = (
        "NEXT_PUBLIC_API_URL=http://localhost:8000\n"
        "NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws\n"
        "NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native\n"
        "# Only Playwright sets this to 1. Never enable it in a production build.\n"
        "NEXT_PUBLIC_AUTH_BYPASS=0"
    )
    # 1. Prove baseline accepted before each table
    _validate_env_examples_contract(valid_root, valid_frontend)

    # 2. Leading whitespace
    with pytest.raises(AssertionError):
        _validate_env_examples_contract("  " + valid_root, valid_frontend)

    # 3. Trailing ASCII whitespace
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(valid_root + "  ", valid_frontend)

    # 4. Trailing NBSP whitespace
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(valid_root + "\u00a0", valid_frontend)

    # 5. Whitespace around equals
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(valid_root.replace("AUTH_BYPASS=false", "AUTH_BYPASS = false"), valid_frontend)

    # 6. Export prefix
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(valid_root.replace("AUTH_BYPASS=false", "export AUTH_BYPASS=false"), valid_frontend)

    # 7. Duplicate/case-collision
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(valid_root + "\nauth_bypass=false", valid_frontend)

    # 8. Padded value
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(valid_root.replace("AUTH_BYPASS=false", "AUTH_BYPASS= false "), valid_frontend)

    # 9. Later unsafe TARS_RUNTIME_MODE override
    with pytest.raises(AssertionError):
        _validate_env_examples_contract(valid_root + "\nTARS_RUNTIME_MODE=hosted-pilot", valid_frontend)


# --- 11. Fixes-2: Exact Project & Org Grammars ---

@pytest.mark.parametrize(
    "proj,valid",
    [
        ("abcdef", True),  # min length 6, letter start
        ("a" + "b" * 28 + "c", True),  # max length 30
        ("a-b-c-d", True),
        ("1abcde", False),  # digit start rejected
        ("-abcde", False),  # hyphen start rejected
        ("abcde-", False),  # hyphen end rejected
        ("abcde", False),  # length 5 too short
        ("a" + "b" * 29 + "c", False),  # length 31 too long
        ("Project", False),  # uppercase rejected
        ("proj_123", False),  # underscore rejected
        ("proj/123", False),  # slash rejected
        ("https://proj", False),  # URL rejected
        ("proj ", False),  # trailing space rejected
        ("proj\x00ect", False),  # control character rejected
        ("proj-høsted", False),  # Unicode rejected
    ],
)
def test_project_id_grammar_boundaries(proj, valid):
    from backend.auth import PROJECT_ID_PATTERN
    assert bool(PROJECT_ID_PATTERN.fullmatch(proj)) == valid


@pytest.mark.parametrize(
    "org,valid",
    [
        ("abc", True),  # min length 3, letter start
        ("a" + "b" * 61 + "c", True),  # max length 63
        ("ella-internal", True),
        ("1ab", False),  # digit start rejected
        ("-ab", False),  # hyphen start rejected
        ("ab", False),  # length 2 too short
        ("a" + "b" * 62 + "c", False),  # length 64 too long
        ("Ella-Internal", False),  # uppercase rejected
        ("ella_internal", False),  # underscore rejected
        ("ella/internal", False),  # slash rejected
    ],
)
def test_org_id_grammar_boundaries(org, valid):
    from backend.auth import ORG_ID_PATTERN
    assert bool(ORG_ID_PATTERN.fullmatch(org)) == valid


# --- 13. Fixes-2: Throwing Firebase Options Sanitized ---

def test_validate_existing_firebase_app_throwing_options_sanitized():
    from backend.auth import validate_existing_firebase_app

    class ThrowingOptions:
        def get(self, key):
            raise RuntimeError("CRITICAL_SENTINEL_KEY_LEAK")

    class ThrowingApp:
        def __init__(self):
            self._options = ThrowingOptions()

        @property
        def project_id(self):
            raise AssertionError("Lazy project_id accessed")

    s = Settings(
        tars_runtime_mode="local",
        auth_bypass=False,
        google_cloud_project="tars-test",
        auth_allowed_emails="a@b.com",
        auth_org_id="ella-internal",
    )

    with patch("backend.auth.firebase_admin.get_app", return_value=ThrowingApp()):
        with pytest.raises(AuthConfigurationError) as exc_info:
            validate_existing_firebase_app(s)

    assert "CRITICAL_SENTINEL_KEY_LEAK" not in str(exc_info.value)
    assert "CRITICAL_SENTINEL_KEY_LEAK" not in repr(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


# --- 14. Fixes-2: Local Mode Accepts Distinct Valid Projects ---

def test_local_mode_accepts_distinct_valid_projects():
    s = Settings(
        tars_runtime_mode="local",
        auth_bypass=False,
        google_cloud_project="tars-gcp-project",
        firebase_project_id="tars-fb-project",
        auth_allowed_emails="a@ellaexecutivesearch.com",
        auth_org_id="ella-internal",
    )
    validate_auth_configuration(s)
