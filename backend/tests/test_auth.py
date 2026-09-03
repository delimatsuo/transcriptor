import asyncio
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException

from backend.auth import AuthenticationError, AuthContext, verify_bearer_token
from backend.config import Settings
from backend import main


def auth_settings(**overrides):
    values = {
        "google_cloud_project": "tars-test-project",
        "auth_allowed_emails": "recruiter@example.com",
        "auth_org_id": "ella-internal",
        "auth_bypass": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_token_admission_requires_revocation_check_and_derives_org():
    claims = {
        "sub": "uid-a",
        "uid": "uid-a",
        "email": "Recruiter@Example.com",
        "email_verified": True,
        "aud": "tars-test-project",
        "iss": "https://securetoken.google.com/tars-test-project",
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
        {"sub": "uid-a", "uid": "uid-a", "email": "recruiter@example.com", "email_verified": False},
        {"sub": "uid-a", "uid": "uid-a", "email": "other@example.com", "email_verified": True},
        {"sub": "uid-a", "uid": "uid-a", "email": "recruiter@example.com", "email_verified": True, "aud": "wrong"},
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


def test_positive_asgi_api_me_with_auth_bypass_false_returns_principal():
    claims = {
        "sub": "uid-recruiter",
        "uid": "uid-recruiter",
        "email": "recruiter@example.com",
        "email_verified": True,
        "aud": "tars-test-project",
        "iss": "https://securetoken.google.com/tars-test-project",
    }
    old_settings = main.settings
    old_ready = main.app.state.ready
    main.settings = auth_settings()
    main.app.state.ready = True

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("backend.auth.firebase_auth.verify_id_token", return_value=claims) as verify_mock:
                resp = await client.get("/api/me", headers={"Authorization": "Bearer mock-token"})
                verify_mock.assert_called_once_with("mock-token", check_revoked=True)
                return resp

    try:
        response = asyncio.run(run())
        assert response.status_code == 200
        data = response.json()
        assert data["uid"] == "uid-recruiter"
        assert data["email"] == "recruiter@example.com"
        assert data["org_id"] == "ella-internal"
    finally:
        main.settings = old_settings
        main.app.state.ready = old_ready


@pytest.mark.parametrize(
    "auth_header,mock_side_effect,mock_claims",
    [
        (None, None, None),  # Missing header
        ("Bearer revoked-token", RuntimeError("Token revoked"), None),  # Revoked
        ("Bearer bad-claims", None, {"sub": "u", "uid": "u", "email": "recruiter@example.com", "email_verified": False}),  # Unverified
        ("Bearer bad-claims", None, {"sub": "u", "uid": "u", "email": "unallowlisted@example.com", "email_verified": True}),  # Unallowlisted
        ("Bearer bad-claims", None, {"sub": "u", "uid": "u", "email": "recruiter@example.com", "email_verified": True, "aud": "wrong-project"}),  # Wrong aud
        ("Bearer bad-claims", None, {"sub": "u", "uid": "u", "email": "recruiter@example.com", "email_verified": True, "aud": "tars-test-project", "iss": "https://session.firebase.google.com/tars-test-project"}),  # Wrong iss
    ],
)
def test_asgi_api_me_denial_cases_share_generic_401_surface_and_bearer_challenge(auth_header, mock_side_effect, mock_claims):
    old_settings = main.settings
    old_ready = main.app.state.ready
    main.settings = auth_settings()
    main.app.state.ready = True

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": auth_header} if auth_header else {}
            if mock_side_effect:
                with patch("backend.auth.firebase_auth.verify_id_token", side_effect=mock_side_effect):
                    return await client.get("/api/me", headers=headers)
            elif mock_claims:
                with patch("backend.auth.firebase_auth.verify_id_token", return_value=mock_claims):
                    return await client.get("/api/me", headers=headers)
            else:
                return await client.get("/api/me", headers=headers)

    try:
        response = asyncio.run(run())
        assert response.status_code == 401
        assert response.headers.get("www-authenticate") == "Bearer"
        assert response.json() == {"detail": "Authentication required"}
    finally:
        main.settings = old_settings
        main.app.state.ready = old_ready


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
