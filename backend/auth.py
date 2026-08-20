"""Firebase authentication and server-derived internal tenancy context."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import firebase_admin
from firebase_admin import auth as firebase_auth
from fastapi import HTTPException

from backend.config import Settings


@dataclass(frozen=True)
class AuthContext:
    uid: str
    email: str
    org_id: str


class AuthenticationError(ValueError):
    """Raised when a bearer token cannot be admitted."""


_current_auth: ContextVar[AuthContext | None] = ContextVar(
    "tars_current_auth", default=None
)
_auth_enforced: ContextVar[bool] = ContextVar("tars_auth_enforced", default=False)


def set_current_auth(user: AuthContext | None):
    return _current_auth.set(user)


def reset_current_auth(token: Any) -> None:
    _current_auth.reset(token)


def set_auth_enforced(value: bool = True):
    return _auth_enforced.set(value)


def reset_auth_enforced(token: Any) -> None:
    _auth_enforced.reset(token)


def auth_is_enforced() -> bool:
    return _auth_enforced.get()


def current_auth() -> AuthContext | None:
    return _current_auth.get()


def initialize_firebase_admin(settings: Settings) -> None:
    """Initialize the Admin SDK once, after the startup ADC probe."""
    try:
        firebase_admin.get_app()
        return
    except ValueError:
        pass

    firebase_admin.initialize_app(
        options={"projectId": settings.firebase_project_id or settings.google_cloud_project}
    )


def _allowed_emails(settings: Settings) -> set[str]:
    return {
        email.strip().lower()
        for email in settings.auth_allowed_emails.split(",")
        if email.strip()
    }


def verify_bearer_token(authorization: str | None, settings: Settings) -> AuthContext:
    """Verify and admit a Firebase ID token without trusting request ownership."""
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("missing bearer token")
    token = authorization[7:].strip()
    if not token:
        raise AuthenticationError("missing bearer token")

    try:
        claims = firebase_auth.verify_id_token(token, check_revoked=True)
    except Exception as exc:  # Firebase exposes several provider-specific errors.
        raise AuthenticationError("invalid bearer token") from exc

    uid = str(claims.get("uid") or claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip().lower()
    if not uid or not email or claims.get("email_verified") is not True:
        raise AuthenticationError("unverified identity")
    if email not in _allowed_emails(settings):
        raise AuthenticationError("account is not provisioned")

    expected_project = settings.firebase_project_id or settings.google_cloud_project
    audience = claims.get("aud")
    issuer = claims.get("iss")
    if audience != expected_project or issuer not in {
        f"https://securetoken.google.com/{expected_project}",
        f"https://session.firebase.google.com/{expected_project}",
    }:
        raise AuthenticationError("wrong token audience")

    # org_id is deliberately configured server-side for this internal phase.
    return AuthContext(uid=uid, email=email, org_id=settings.auth_org_id)


def require_current_auth() -> AuthContext:
    """Return the request principal for route-level ownership checks."""
    user = current_auth()
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user
