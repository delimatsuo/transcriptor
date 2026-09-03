"""Firebase authentication and server-derived internal tenancy context."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
import re
from typing import Any, Mapping

import firebase_admin
from firebase_admin import auth as firebase_auth
from fastapi import HTTPException

from backend.config import AuthConfigurationError, Settings


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


PROJECT_ID_PATTERN = re.compile(r"\A[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
ORG_ID_PATTERN = re.compile(r"\A[a-z][a-z0-9-]{1,61}[a-z0-9]\Z")
LOCAL_PART_ALLOWED_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'+=?^_`{|}~.-")


def parse_allowed_emails(raw: str) -> frozenset[str]:
    """Pure allowlist parser for exact comma-separated email addresses.

    Raises content-free AuthConfigurationError on any malformed or noncanonical input.
    """
    if not isinstance(raw, str):
        raise AuthConfigurationError("Allowlist must be a string")

    # Reject non-ASCII
    try:
        raw.encode("ascii")
    except UnicodeEncodeError:
        raise AuthConfigurationError("Non-ASCII character in allowlist") from None

    # Reject CRLF
    if "\r" in raw or "\n" in raw:
        raise AuthConfigurationError("CRLF injection in allowlist")

    # Reject controls other than HTAB
    if any((ord(c) < 32 and c != "\t") or ord(c) == 127 for c in raw):
        raise AuthConfigurationError("Control characters in allowlist")

    # Check commas: cannot start or end with comma, no double commas
    stripped_raw = raw.strip(" \t")
    if not stripped_raw:
        raise AuthConfigurationError("Empty allowlist")
    if raw.startswith(",") or raw.endswith(","):
        raise AuthConfigurationError("Leading or trailing comma in allowlist")
    if ",," in raw:
        raise AuthConfigurationError("Empty entry in allowlist")

    raw_items = raw.split(",")
    admitted: set[str] = set()

    for raw_item in raw_items:
        # HTAB accepted only as surrounding padding
        item = raw_item.strip(" \t")
        if not item:
            raise AuthConfigurationError("Empty item in allowlist")
        if "\t" in item or " " in item:
            raise AuthConfigurationError("Embedded whitespace or tab in allowlist item")
        if len(item) > 254:
            raise AuthConfigurationError("Email address exceeds maximum length")
        if item.count("@") != 1:
            raise AuthConfigurationError("Email address must contain exactly one at-sign separator")

        local, domain = item.split("@", 1)
        if not local or len(local) > 64:
            raise AuthConfigurationError("Invalid email local part length")
        if local.startswith(".") or local.endswith("."):
            raise AuthConfigurationError("Local part cannot start or end with a dot")
        if ".." in local:
            raise AuthConfigurationError("Consecutive dots in local part")
        if not all(c in LOCAL_PART_ALLOWED_CHARS for c in local):
            raise AuthConfigurationError("Invalid character in email local part")

        if not domain or len(domain) > 253:
            raise AuthConfigurationError("Invalid email domain length")
        domain_labels = domain.split(".")
        if len(domain_labels) < 2:
            raise AuthConfigurationError("Domain must contain at least two dot-separated labels")

        for label in domain_labels:
            if not label or len(label) > 63:
                raise AuthConfigurationError("Invalid domain label length")
            if label.startswith("-") or label.endswith("-"):
                raise AuthConfigurationError("Domain label cannot start or end with a hyphen")
            if not all(c.isalnum() or c == "-" for c in label):
                raise AuthConfigurationError("Invalid character in domain label")

        canonical_email = item.lower()
        if canonical_email in admitted:
            raise AuthConfigurationError("Duplicate email in allowlist")
        admitted.add(canonical_email)

    if not admitted:
        raise AuthConfigurationError("Allowlist must contain at least one valid email")

    return frozenset(admitted)


def validate_auth_configuration(settings: Settings) -> None:
    """Validate project IDs, org slugs, allowlist constraints, and hosted invariants before provider initialization."""
    # Validate project syntax with true full-string matching
    if not PROJECT_ID_PATTERN.fullmatch(settings.google_cloud_project):
        raise AuthConfigurationError("Invalid google_cloud_project format")

    if settings.firebase_project_id is not None:
        if not PROJECT_ID_PATTERN.fullmatch(settings.firebase_project_id):
            raise AuthConfigurationError("Invalid firebase_project_id format")

    # In hosted-pilot mode, firebase_project_id is mandatory, nonblank, and must equal google_cloud_project
    if settings.tars_runtime_mode == "hosted-pilot":
        if not settings.firebase_project_id:
            raise AuthConfigurationError("firebase_project_id is required in hosted-pilot mode")
        if not PROJECT_ID_PATTERN.fullmatch(settings.firebase_project_id):
            raise AuthConfigurationError("Invalid firebase_project_id format")
        if settings.firebase_project_id != settings.google_cloud_project:
            raise AuthConfigurationError("firebase_project_id must match google_cloud_project")

    # Validate org slug
    if not ORG_ID_PATTERN.fullmatch(settings.auth_org_id):
        raise AuthConfigurationError("Invalid auth_org_id format")

    if settings.auth_bypass:
        if settings.tars_runtime_mode == "hosted-pilot":
            raise AuthConfigurationError("AUTH_BYPASS is strictly forbidden in hosted-pilot mode")
        return

    # Strict allowlist parsing when auth_bypass is False
    emails = parse_allowed_emails(settings.auth_allowed_emails)

    # In hosted-pilot mode, require exact 5 corporate accounts and org slug
    if settings.tars_runtime_mode == "hosted-pilot":
        if settings.auth_org_id != "ella-internal":
            raise AuthConfigurationError("auth_org_id must be ella-internal in hosted-pilot mode")
        if len(emails) != 5:
            raise AuthConfigurationError("Hosted pilot requires exactly 5 authorized recruiter accounts")
        for email in emails:
            if not email.endswith("@ellaexecutivesearch.com"):
                raise AuthConfigurationError(
                    "All authorized accounts must belong to the corporate domain ellaexecutivesearch.com"
                )


_CANONICAL_NO_DEFAULT_APP_STRING = (
    "The default Firebase app does not exist. Make sure to initialize the SDK by calling initialize_app()."
)


def _is_canonical_no_default_app_error(e: BaseException) -> bool:
    """Classify only the single exact built-in ValueError shape indicating no default app without str/repr."""
    if type(e) is not ValueError:
        return False
    args = getattr(e, "args", None)
    if type(args) is not tuple or len(args) != 1:
        return False
    first_arg = args[0]
    if type(first_arg) is not str:
        return False
    return first_arg == _CANONICAL_NO_DEFAULT_APP_STRING


def _extract_app_project_id(app: Any) -> str | None:
    """Safely extract projectId from app._options without invoking lazy accessors."""
    if app is None:
        return None
    options = None
    try:
        options = getattr(app, "_options", None)
    except Exception:
        return None
    if options is None:
        return None
    try:
        if isinstance(options, Mapping) or hasattr(options, "get"):
            val = options.get("projectId")
            if type(val) is str:
                return val
    except Exception:
        return None
    return None


def validate_existing_firebase_app(settings: Settings) -> None:
    """Pre-ADC validation of already-initialized default Firebase app."""
    target_project = settings.firebase_project_id or settings.google_cloud_project
    no_app = False
    app = None
    err_msg: str | None = None

    try:
        app = firebase_admin.get_app()
    except BaseException as e:
        if _is_canonical_no_default_app_error(e):
            no_app = True
        else:
            err_msg = "Existing Firebase app lookup failed"

    if not no_app and err_msg is None:
        if app is None:
            err_msg = "Existing Firebase app lookup failed"
        else:
            bound_project = _extract_app_project_id(app)
            if bound_project is None or bound_project != target_project:
                err_msg = "Existing Firebase app project binding mismatch or missing"

    if err_msg is not None:
        err = AuthConfigurationError(err_msg)
        err.__cause__ = None
        err.__context__ = None
        raise err


def initialize_firebase_admin(google_cloud_project: str, firebase_project_id: str | None = None) -> None:
    """Initialize Firebase Admin SDK with explicit project binding after ADC probe."""
    target_project = firebase_project_id or google_cloud_project
    no_app = False
    app = None
    err_msg: str | None = None

    try:
        app = firebase_admin.get_app()
    except BaseException as e:
        if _is_canonical_no_default_app_error(e):
            no_app = True
        else:
            err_msg = "Existing Firebase app lookup failed"

    if not no_app and err_msg is None:
        if app is None:
            err_msg = "Existing Firebase app lookup failed"
        else:
            bound_project = _extract_app_project_id(app)
            if bound_project is None or bound_project != target_project:
                err_msg = "Firebase Admin default app project ID mismatch"

    if no_app and err_msg is None:
        new_app = None
        try:
            new_app = firebase_admin.initialize_app(
                options={"projectId": target_project}
            )
        except Exception:
            err_msg = "Firebase Admin SDK initialization failed"

        if err_msg is None:
            if new_app is None:
                err_msg = "Firebase Admin SDK initialization failed"
            else:
                bound_project = _extract_app_project_id(new_app)
                if bound_project is None or bound_project != target_project:
                    err_msg = "Firebase Admin SDK initialization failed"

    if err_msg is not None:
        err = AuthConfigurationError(err_msg)
        err.__cause__ = None
        err.__context__ = None
        raise err


def _allowed_emails(settings: Settings) -> frozenset[str]:
    return parse_allowed_emails(settings.auth_allowed_emails)


def verify_bearer_token(authorization: str | None, settings: Settings) -> AuthContext:
    """Verify and admit a Firebase ID token without trusting request ownership."""
    if settings.auth_bypass:
        return AuthContext(
            uid="local-recruiter-dev",
            email="recruiter-pilot@example.com",
            org_id=settings.auth_org_id,
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("missing bearer token")
    token = authorization[7:].strip()
    if not token:
        raise AuthenticationError("missing bearer token")

    claims = None
    try:
        claims = firebase_auth.verify_id_token(token, check_revoked=True)
    except Exception:
        pass

    if claims is None or not isinstance(claims, Mapping):
        raise AuthenticationError("invalid bearer token")

    # Mandatory sub claim: 1-128 printable ASCII characters, unpadded, control-free, no whitespace
    raw_sub = claims.get("sub")
    if not isinstance(raw_sub, str) or not (1 <= len(raw_sub) <= 128):
        raise AuthenticationError("invalid bearer token")
    try:
        raw_sub.encode("ascii")
    except UnicodeEncodeError:
        raise AuthenticationError("invalid bearer token")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in raw_sub):
        raise AuthenticationError("invalid bearer token")

    # If uid is present, independently validate under identical grammar and require exact equality with sub
    if "uid" in claims:
        raw_uid = claims.get("uid")
        if not isinstance(raw_uid, str) or not (1 <= len(raw_uid) <= 128):
            raise AuthenticationError("invalid bearer token")
        try:
            raw_uid.encode("ascii")
        except UnicodeEncodeError:
            raise AuthenticationError("invalid bearer token")
        if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in raw_uid):
            raise AuthenticationError("invalid bearer token")
        if raw_uid != raw_sub:
            raise AuthenticationError("invalid bearer token")

    uid = raw_sub

    raw_email = claims.get("email")
    if not isinstance(raw_email, str) or not raw_email or any(c.isspace() for c in raw_email):
        raise AuthenticationError("unverified identity")

    try:
        parsed_email_set = parse_allowed_emails(raw_email)
        if len(parsed_email_set) != 1:
            raise AuthenticationError("invalid bearer token")
        email = list(parsed_email_set)[0]
    except Exception:
        raise AuthenticationError("invalid bearer token")

    if claims.get("email_verified") is not True:
        raise AuthenticationError("unverified identity")

    if email not in _allowed_emails(settings):
        raise AuthenticationError("account is not provisioned")

    expected_project = settings.firebase_project_id or settings.google_cloud_project
    audience = claims.get("aud")
    issuer = claims.get("iss")
    if audience != expected_project or issuer != f"https://securetoken.google.com/{expected_project}":
        raise AuthenticationError("wrong token audience")

    # org_id is deliberately configured server-side for this internal phase.
    return AuthContext(uid=uid, email=email, org_id=settings.auth_org_id)


def require_current_auth() -> AuthContext:
    """Return the request principal for route-level ownership checks."""
    user = current_auth()
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user
