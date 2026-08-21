from unittest.mock import patch

import pytest
from fastapi import HTTPException

from backend.auth import AuthenticationError, AuthContext, verify_bearer_token
from backend.config import Settings
from backend import main


def auth_settings(**overrides):
    values = {
        "google_cloud_project": "tars-test",
        "auth_allowed_emails": "recruiter@example.com",
        "auth_org_id": "ella-internal",
        "auth_bypass": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_token_admission_requires_revocation_check_and_derives_org():
    claims = {
        "uid": "uid-a",
        "email": "Recruiter@Example.com",
        "email_verified": True,
        "aud": "tars-test",
        "iss": "https://securetoken.google.com/tars-test",
    }
    with patch("backend.auth.firebase_auth.verify_id_token", return_value=claims) as verify:
        context = verify_bearer_token("Bearer firebase-token", auth_settings())

    verify.assert_called_once_with("firebase-token", check_revoked=True)
    assert context == AuthContext(
        uid="uid-a", email="recruiter@example.com", org_id="ella-internal"
    )


@pytest.mark.parametrize(
    "claims",
    [
        {"uid": "uid-a", "email": "recruiter@example.com", "email_verified": False},
        {"uid": "uid-a", "email": "other@example.com", "email_verified": True},
        {"uid": "uid-a", "email": "recruiter@example.com", "email_verified": True, "aud": "wrong"},
    ],
)
def test_token_admission_rejects_unverified_unallowlisted_or_wrong_project(claims):
    with patch("backend.auth.firebase_auth.verify_id_token", return_value=claims):
        with pytest.raises(AuthenticationError):
            verify_bearer_token("Bearer token", auth_settings())


def test_invalid_or_missing_bearer_is_rejected():
    with pytest.raises(AuthenticationError):
        verify_bearer_token(None, auth_settings())
    with patch(
        "backend.auth.firebase_auth.verify_id_token",
        side_effect=RuntimeError("revoked"),
    ):
        with pytest.raises(AuthenticationError):
            verify_bearer_token("Bearer revoked", auth_settings())


def test_foreign_session_is_non_enumerating_not_found():
    previous = main.current_auth()
    token = main.set_current_auth(AuthContext("uid-b", "b@example.com", "ella-internal"))
    enforced = main.set_auth_enforced()
    try:
        session = type("Session", (), {"owner_id": "uid-a", "org_id": "ella-internal"})()
        with pytest.raises(HTTPException) as exc_info:
            main._assert_session_access(session)
        assert exc_info.value.status_code == 404
    finally:
        main.reset_current_auth(token)
        main.reset_auth_enforced(enforced)
        assert main.current_auth() is previous


@pytest.mark.parametrize(
    "record",
    [
        {"ownerId": "uid-b", "orgId": "ella-internal", "mode": object()},
        {"ownerId": "uid-a", "orgId": "other-org", "mode": object()},
        {"mode": object()},
    ],
)
def test_raw_persisted_foreign_or_unowned_session_is_non_enumerating_not_found(record):
    previous = main.current_auth()
    token = main.set_current_auth(AuthContext("uid-a", "a@example.com", "ella-internal"))
    enforced = main.set_auth_enforced()
    try:
        with pytest.raises(HTTPException) as exc_info:
            main._assert_persisted_session_access(record)
        assert exc_info.value.status_code == 404
    finally:
        main.reset_current_auth(token)
        main.reset_auth_enforced(enforced)
        assert main.current_auth() is previous
