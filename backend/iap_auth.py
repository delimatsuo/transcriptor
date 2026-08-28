"""Offline-testable verification of Google Cloud IAP assertions.

The application never treats the unsigned ``x-goog-authenticated-user-*``
headers as authority.  Only the signed assertion is accepted, and all
admission policy (including the internal organization) is applied locally.
"""

from __future__ import annotations

import json
import inspect
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from backend.config import Settings

IAP_ISSUER = "https://cloud.google.com/iap"
IAP_PUBLIC_KEY_URL = "https://www.gstatic.com/iap/verify/public_key"
IAP_CLOCK_SKEW_SECONDS = 30
IAP_MAX_JWT_LIFETIME_SECONDS = 600
IAP_MAX_GCIP_BYTES = 4096
IAP_MAX_SUBJECT_LENGTH = 256
IAP_MAX_EMAIL_LENGTH = 254
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


class AuthenticationError(ValueError):
    """Content-free authentication admission failure."""


# Authentication telemetry is intentionally a closed vocabulary.  The source
# messages remain useful to local callers/tests, but only these stable codes
# may cross the logging boundary.  Unknown or dynamically composed messages
# always collapse to the generic code.
IAP_REJECTION_REASON_GENERIC = "generic_iap_rejection"
IAP_REJECTION_REASON_BY_MESSAGE: Mapping[str, str] = MappingProxyType(
    {
        "IAP authentication is disabled": "iap_authentication_disabled",
        "invalid IAP assertion": "invalid_iap_assertion",
        "missing IAP assertion": "missing_iap_assertion",
        "malformed IAP assertion": "malformed_iap_assertion",
        "invalid IAP signature": "invalid_iap_signature",
        "malformed IAP email": "malformed_iap_email",
        "malformed IAP subject": "malformed_iap_subject",
        "unverified IAP email": "unverified_iap_email",
        "malformed IAP auth time": "malformed_iap_auth_time",
        "malformed IAP gcip": "malformed_iap_gcip",
        "wrong IAP issuer": "wrong_iap_issuer",
        "wrong IAP audience": "wrong_iap_audience",
        "malformed IAP lifetime": "malformed_iap_lifetime",
        "excessive IAP lifetime": "excessive_iap_lifetime",
        "future IAP assertion": "future_iap_assertion",
        "expired IAP assertion": "expired_iap_assertion",
        "future IAP authentication": "future_iap_authentication",
        "malformed IAP authentication": "malformed_iap_authentication",
        "unsupported IAP provider": "unsupported_iap_provider",
        "account is not provisioned": "account_not_provisioned",
        "principal is revoked": "principal_revoked",
    }
)
IAP_REJECTION_REASON_CODES = frozenset(
    {*IAP_REJECTION_REASON_BY_MESSAGE.values(), IAP_REJECTION_REASON_GENERIC}
)


def iap_rejection_reason(error: BaseException | str | None) -> str:
    """Return one allowlisted, content-free reason code for an IAP rejection."""
    message: str | None = None
    if isinstance(error, str):
        message = error
    elif isinstance(error, BaseException) and len(error.args) == 1:
        candidate = error.args[0]
        if isinstance(candidate, str):
            message = candidate
    return IAP_REJECTION_REASON_BY_MESSAGE.get(
        message,
        IAP_REJECTION_REASON_GENERIC,
    )


# Backward-compatible explicit name for callers that want to distinguish the
# IAP verifier seam while still matching ``backend.auth.AuthenticationError``.
IAPAuthenticationError = AuthenticationError


@dataclass(frozen=True)
class IAPIdentity:
    """Validated principal returned by :func:`verify_iap_assertion`."""

    uid: str
    email: str
    org_id: str
    auth_time: int


class _DuplicateKey(ValueError):
    pass


def _reject(message: str = "invalid IAP assertion") -> None:
    # Keep this helper central so no provider payload, token, claim, or email
    # can accidentally leak through an admission error.
    raise AuthenticationError(message)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _single_assertion(value: str | Sequence[str] | None) -> str:
    if value is None:
        _reject("missing IAP assertion")
    if isinstance(value, str):
        if not value.strip() or "," in value:
            _reject("malformed IAP assertion")
        return value.strip()
    if isinstance(value, (bytes, bytearray)):
        _reject("malformed IAP assertion")
    if not isinstance(value, (list, tuple)):
        _reject("malformed IAP assertion")
    try:
        values = list(value)
    except (TypeError, ValueError):
        _reject("malformed IAP assertion")
    if len(values) != 1 or not isinstance(values[0], str) or not values[0].strip():
        _reject("malformed IAP assertion")
    if "," in values[0]:
        _reject("malformed IAP assertion")
    return values[0].strip()


def verify_iap_signature(
    token: str,
    audience: str,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Mapping[str, Any]:
    """Verify the JWT signature using Google's official IAP key endpoint.

    Tests inject a verifier into :func:`verify_iap_assertion`; therefore this
    production seam is never reached by the offline suite.  ``clock`` is
    accepted for a stable seam even though Google's verifier handles JWT time
    checks itself; the application repeats those checks below.
    """
    del clock
    verification_failed = False
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token

        verify_kwargs: dict[str, Any] = {
            "request": Request(),
            "audience": audience,
            "certs_url": IAP_PUBLIC_KEY_URL,
        }
        # google-auth added this keyword after the original IAP verifier seam.
        # Keep the source compatible with older installed SDKs while ensuring
        # every supported verifier receives the documented clock tolerance.
        try:
            parameters = inspect.signature(id_token.verify_token).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "clock_skew_in_seconds" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        ):
            verify_kwargs["clock_skew_in_seconds"] = IAP_CLOCK_SKEW_SECONDS
        claims = id_token.verify_token(token, **verify_kwargs)
    except Exception:
        # Discard provider exception objects before raising the content-free
        # error outside the sensitive exception scope.
        verification_failed = True
        claims = None
    if verification_failed:
        raise AuthenticationError("invalid IAP signature")
    if not isinstance(claims, Mapping):
        _reject("invalid IAP signature")
    return claims


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def canonicalize_email(value: Any) -> str:
    if not isinstance(value, str):
        _reject("malformed IAP email")
    email = value.strip()
    if (
        not email
        or len(email) > IAP_MAX_EMAIL_LENGTH
        or not email.isascii()
    ):
        _reject("malformed IAP email")
    email = email.casefold()
    if not _EMAIL_RE.fullmatch(email):
        _reject("malformed IAP email")
    return email


def _parse_gcip(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        _reject("malformed IAP gcip")
    parsed: Any = None
    try:
        if len(raw.encode("utf-8")) > IAP_MAX_GCIP_BYTES:
            _reject("malformed IAP gcip")
        parsed = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except AuthenticationError:
        raise
    except Exception:
        parsed = None
    if not isinstance(parsed, Mapping):
        _reject("malformed IAP gcip")
    return parsed


def _provider_is_google(gcip: Mapping[str, Any]) -> bool:
    firebase = gcip.get("firebase")
    return isinstance(firebase, Mapping) and firebase.get("sign_in_provider") == "google.com"


def _validated_gcip(gcip: Mapping[str, Any]) -> tuple[str, str, int]:
    sub = gcip.get("sub")
    if (
        not isinstance(sub, str)
        or not sub
        or len(sub) > IAP_MAX_SUBJECT_LENGTH
        or not sub.isascii()
        or any(character.isspace() or ord(character) < 32 for character in sub)
    ):
        _reject("malformed IAP subject")
    email = canonicalize_email(gcip.get("email"))
    if gcip.get("email_verified") is not True:
        _reject("unverified IAP email")
    auth_time = gcip.get("auth_time")
    if not _integer(auth_time) or auth_time < 0:
        _reject("malformed IAP auth time")
    if not _provider_is_google(gcip):
        _reject("unsupported IAP provider")
    return sub, email, auth_time


def _claims_mapping(claims: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(claims, Mapping):
        _reject("invalid IAP assertion")
    return claims


def verify_iap_assertion(
    assertion: str | Sequence[str] | None,
    settings: Settings,
    *,
    verifier: Callable[[str, str], Mapping[str, Any]] | None = None,
    now: datetime | int | float | None = None,
) -> IAPIdentity:
    """Verify and locally admit one signed IAP assertion.

    ``verifier`` is deliberately injectable so all tests use synthetic claims
    and make zero network calls.  The verifier receives only the token and the
    configured audience; all semantic claims are checked independently here.
    """
    if settings.auth_mode != "iap":
        _reject("IAP authentication is disabled")
    token = _single_assertion(assertion)
    signature_verifier = verifier or verify_iap_signature
    verification_failed = False
    raw_claims: Any = None
    try:
        raw_claims = signature_verifier(token, settings.auth_iap_audience or "")
    except Exception:
        # Keep provider/verifier exception objects, tokens, and payloads out of
        # the raised exception's cause/context chain.
        verification_failed = True
    if verification_failed:
        raise AuthenticationError("invalid IAP signature")
    claims = _claims_mapping(raw_claims)

    if claims.get("iss") != IAP_ISSUER:
        _reject("wrong IAP issuer")
    if claims.get("aud") != settings.auth_iap_audience:
        _reject("wrong IAP audience")

    iat = claims.get("iat")
    exp = claims.get("exp")
    if not _integer(iat) or not _integer(exp) or exp <= iat:
        _reject("malformed IAP lifetime")
    if exp - iat > IAP_MAX_JWT_LIFETIME_SECONDS + IAP_CLOCK_SKEW_SECONDS:
        _reject("excessive IAP lifetime")

    if now is None:
        current = datetime.now(timezone.utc).timestamp()
    elif isinstance(now, datetime):
        current = now.timestamp()
    else:
        current = float(now)
    if iat > current + IAP_CLOCK_SKEW_SECONDS:
        _reject("future IAP assertion")
    if exp < current - IAP_CLOCK_SKEW_SECONDS:
        _reject("expired IAP assertion")

    sub, email, auth_time = _validated_gcip(_parse_gcip(claims.get("gcip")))
    # The external identity's authentication time cannot be in the future or
    # before the assertion's issued time beyond the documented skew.
    if auth_time > current + IAP_CLOCK_SKEW_SECONDS:
        _reject("future IAP authentication")
    if auth_time > iat + IAP_CLOCK_SKEW_SECONDS:
        _reject("malformed IAP authentication")
    if auth_time > exp + IAP_CLOCK_SKEW_SECONDS:
        _reject("malformed IAP authentication")

    allowed = {
        canonicalize_email(item)
        for item in settings.auth_allowed_emails.split(",")
        if item.strip()
    }
    if email not in allowed:
        _reject("account is not provisioned")
    return IAPIdentity(uid=sub, email=email, org_id=settings.auth_org_id, auth_time=auth_time)


def verify_iap_headers(
    headers: Mapping[str, Any],
    settings: Settings,
    *,
    verifier: Callable[[str, str], Mapping[str, Any]] | None = None,
    now: datetime | int | float | None = None,
) -> IAPIdentity:
    """Header-oriented convenience seam used by HTTP and socket tests."""
    getlist = getattr(headers, "getlist", None)
    values = getlist("x-goog-iap-jwt-assertion") if callable(getlist) else None
    if values is None or not values:
        value = headers.get("x-goog-iap-jwt-assertion")
        values = [value] if value is not None else []
    return verify_iap_assertion(values, settings, verifier=verifier, now=now)


verify_iap_request = verify_iap_headers


# Friendly aliases used by route and test seams.
verify_iap_token = verify_iap_assertion
verify_signed_iap_assertion = verify_iap_assertion
