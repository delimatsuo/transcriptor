from __future__ import annotations

import json
import math
import traceback
from collections.abc import Mapping
from datetime import datetime, timezone

import pytest

import backend.iap_auth as iap_auth
from backend.config import Settings
from backend.iap_auth import (
    IAPAuthenticationError,
    canonicalize_email,
    verify_iap_assertion,
)


AUDIENCE = "/projects/123456789/locations/us-central1/services/tars-api"
ADMITTED = ",".join(
    [
        "task08-recruiter@ellaexecutivesearch.com",
        "task08-operator@ellaexecutivesearch.com",
        "task08-auditor@ellaexecutivesearch.com",
        "task08-reviewer@ellaexecutivesearch.com",
        "task08-backup@ellaexecutivesearch.com",
    ]
)


def iap_settings(**overrides) -> Settings:
    values = {
        "google_cloud_project": "synthetic-project",
        "auth_allowed_emails": ADMITTED,
        "auth_mode": "iap",
        "auth_iap_audience": AUDIENCE,
        "auth_iap_frontend_origin": "https://tars.ellaexecutivesearch.com",
        "auth_task08_operator_emails": "task08-operator@ellaexecutivesearch.com",
    }
    values.update(overrides)
    return Settings(**values)


def claims_for(settings: Settings, **gcip_overrides):
    now = int(datetime.now(timezone.utc).timestamp())
    gcip = {
        "sub": "synthetic-user-1",
        "email": "Task08-Recruiter@EllaExecutiveSearch.com",
        "email_verified": True,
        "auth_time": now - 10,
        "firebase": {"sign_in_provider": "google.com"},
    }
    gcip.update(gcip_overrides)
    return {
        "iss": "https://cloud.google.com/iap",
        "aud": settings.auth_iap_audience,
        "iat": now - 10,
        "exp": now + 590,
        "gcip": json.dumps(gcip, separators=(",", ":")),
    }, now


def admitted(settings: Settings, claims: dict, now: int):
    return verify_iap_assertion(
        "synthetic-signed-assertion",
        settings,
        verifier=lambda token, audience: claims,
        now=now,
    )


def test_positive_signed_iap_admission_is_server_derived_and_injected():
    settings = iap_settings()
    claims, now = claims_for(settings)
    identity = admitted(settings, claims, now)
    assert identity.uid == "synthetic-user-1"
    assert identity.email == "task08-recruiter@ellaexecutivesearch.com"
    assert identity.org_id == "ella-internal"
    assert identity.auth_time == now - 10


def test_iap_rejection_telemetry_is_a_closed_content_free_allowlist():
    expected_mapping = {
        "IAP authentication is disabled": "iap_authentication_disabled",
        "invalid IAP assertion": "invalid_iap_assertion",
        "missing IAP assertion": "missing_iap_assertion",
        "malformed IAP assertion": "malformed_iap_assertion",
        "invalid IAP signature": "invalid_iap_signature",
        "malformed IAP email": "malformed_iap_email",
        "malformed IAP subject": "malformed_iap_subject",
        "unverified IAP email": "unverified_iap_email",
        "malformed IAP auth time": "malformed_iap_auth_time",
        "missing IAP gcip": "missing_iap_gcip",
        "non-string IAP gcip": "non_string_iap_gcip",
        "blank IAP gcip": "blank_iap_gcip",
        "oversized IAP gcip": "oversized_iap_gcip",
        "duplicate IAP gcip key": "duplicate_iap_gcip_key",
        "invalid IAP gcip": "invalid_iap_gcip",
        "non-object IAP gcip": "non_object_iap_gcip",
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
    expected_codes = {
        "iap_authentication_disabled",
        "invalid_iap_assertion",
        "missing_iap_assertion",
        "malformed_iap_assertion",
        "invalid_iap_signature",
        "malformed_iap_email",
        "malformed_iap_subject",
        "unverified_iap_email",
        "malformed_iap_auth_time",
        "missing_iap_gcip",
        "non_string_iap_gcip",
        "blank_iap_gcip",
        "oversized_iap_gcip",
        "duplicate_iap_gcip_key",
        "invalid_iap_gcip",
        "non_object_iap_gcip",
        "wrong_iap_issuer",
        "wrong_iap_audience",
        "malformed_iap_lifetime",
        "excessive_iap_lifetime",
        "future_iap_assertion",
        "expired_iap_assertion",
        "future_iap_authentication",
        "malformed_iap_authentication",
        "unsupported_iap_provider",
        "account_not_provisioned",
        "principal_revoked",
        "generic_iap_rejection",
    }
    assert dict(iap_auth.IAP_REJECTION_REASON_BY_MESSAGE) == expected_mapping
    assert iap_auth.IAP_REJECTION_REASON_CODES == expected_codes
    assert all(
        code and all(character.isalnum() or character == "_" for character in code)
        for code in expected_codes
    )
    for message, reason_code in expected_mapping.items():
        assert iap_auth.iap_rejection_reason(message) == reason_code
        assert iap_auth.iap_rejection_reason(IAPAuthenticationError(message)) == reason_code

    assert (
        iap_auth.iap_rejection_reason(IAPAuthenticationError("principal is revoked"))
        == "principal_revoked"
    )
    for sensitive in [
        "provider payload synthetic-token for sentinel@example.com",
        "x-goog-iap-jwt-assertion: eyJ-sentinel-token",
        "unknown rejection with claims email=sentinel@example.com",
    ]:
        assert iap_auth.iap_rejection_reason(sensitive) == iap_auth.IAP_REJECTION_REASON_GENERIC
        assert (
            iap_auth.iap_rejection_reason(RuntimeError(sensitive))
            == iap_auth.IAP_REJECTION_REASON_GENERIC
        )
    assert iap_auth.iap_rejection_reason(None) == iap_auth.IAP_REJECTION_REASON_GENERIC


@pytest.mark.parametrize(
    "assertion",
    [None, "", "  ", ["first", "second"], ["  "], "a,b"],
)
def test_assertion_requires_exactly_one_nonblank_value(assertion):
    with pytest.raises(IAPAuthenticationError):
        verify_iap_assertion(assertion, iap_settings(), verifier=lambda *_: {})


def test_signature_verifier_failure_is_content_free_and_does_not_leak_token():
    token = "synthetic-secret-token"

    def bad_verifier(*_):
        raise RuntimeError(f"provider payload for {token}")

    with pytest.raises(IAPAuthenticationError) as exc_info:
        verify_iap_assertion(token, iap_settings(), verifier=bad_verifier)
    assert token not in str(exc_info.value)
    assert "provider payload" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    formatted = "".join(traceback.format_exception(exc_info.value))
    assert token not in formatted
    assert "provider payload" not in formatted


def test_production_signature_failure_discards_sensitive_exception_chain(monkeypatch):
    from google.oauth2 import id_token

    secret = "synthetic-provider-secret"

    def bad_verify(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(id_token, "verify_token", bad_verify)
    with pytest.raises(IAPAuthenticationError) as exc_info:
        iap_auth.verify_iap_signature("synthetic-token", AUDIENCE)
    assert str(exc_info.value) == "invalid IAP signature"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert secret not in "".join(traceback.format_exception(exc_info.value))


@pytest.mark.parametrize(
    "claim, value",
    [
        ("iss", "https://wrong.example/iap"),
        ("aud", "/projects/9/locations/us-east1/services/wrong"),
    ],
)
def test_issuer_and_audience_are_independent_exact_checks(claim, value):
    settings = iap_settings()
    claims, now = claims_for(settings)
    claims[claim] = value
    with pytest.raises(IAPAuthenticationError):
        admitted(settings, claims, now)


@pytest.mark.parametrize("kind", ["expired", "future"])
def test_expired_or_future_assertion_is_rejected(kind):
    settings = iap_settings()
    claims, now = claims_for(settings)
    if kind == "expired":
        claims["iat"] = now - 700
        claims["exp"] = now - 100
    else:
        claims["iat"] = now + 100
        claims["exp"] = now + 500
    with pytest.raises(IAPAuthenticationError):
        admitted(settings, claims, now)


def test_excessive_lifetime_and_bool_time_are_rejected():
    settings = iap_settings()
    claims, now = claims_for(settings)
    claims["iat"] = now - 1
    claims["exp"] = now + 632
    with pytest.raises(IAPAuthenticationError):
        admitted(settings, claims, now)

    claims, now = claims_for(settings)
    claims["gcip"] = json.dumps(
        {
            "sub": "synthetic-user-1",
            "email": "task08-recruiter@ellaexecutivesearch.com",
            "email_verified": True,
            "auth_time": claims["iat"] + 31,
            "firebase": {"sign_in_provider": "google.com"},
        }
    )
    with pytest.raises(IAPAuthenticationError):
        admitted(settings, claims, now)

    claims, now = claims_for(settings)
    claims["iat"] = True
    with pytest.raises(IAPAuthenticationError):
        admitted(settings, claims, now)


@pytest.mark.parametrize(
    "gcip, expected",
    [
        (None, "gcip"),
        ("[]", "gcip"),
        ('{"sub":"u","sub":"v","email":"recruiter@example.com","email_verified":true,"auth_time":1,"firebase":{"sign_in_provider":"google.com"}}', "gcip"),
        (json.dumps({"sub": "u", "email": "task08-recruiter@ellaexecutivesearch.com", "email_verified": True, "auth_time": 1, "provider": "google.com"}), "provider"),
        (json.dumps({"sub": "u", "email": "task08-recruiter@ellaexecutivesearch.com", "email_verified": True, "auth_time": 1, "sign_in_provider": "google.com"}), "provider"),
        (json.dumps({"sub": "u", "email": "task08-recruiter@ellaexecutivesearch.com", "email_verified": True, "auth_time": 1, "firebase": {}}), "provider"),
        (json.dumps({"sub": "u", "email": "task08-recruiter@ellaexecutivesearch.com", "email_verified": True, "auth_time": 1, "firebase": "google.com"}), "provider"),
        (json.dumps({"sub": "u", "email": "task08-recruiter@ellaexecutivesearch.com", "email_verified": False, "auth_time": 1, "firebase": {"sign_in_provider": "google.com"}}), "unverified"),
        (json.dumps({"sub": "u", "email": "task08-recruiter@ellaexecutivesearch.com", "email_verified": True, "auth_time": 1, "firebase": {"sign_in_provider": "facebook.com"}}), "provider"),
        (json.dumps({"sub": "u", "email": "task08-recruiter@ellaexecutivesearch.com", "email_verified": True, "auth_time": True, "firebase": {"sign_in_provider": "google.com"}}), "time"),
        (json.dumps({"sub": "", "email": "task08-recruiter@ellaexecutivesearch.com", "email_verified": True, "auth_time": 1, "firebase": {"sign_in_provider": "google.com"}}), "subject"),
        (json.dumps({"sub": "u", "email": "not-an-email", "email_verified": True, "auth_time": 1, "firebase": {"sign_in_provider": "google.com"}}), "email"),
    ],
)
def test_gcip_semantics_are_bounded_duplicate_safe_and_google_only(gcip, expected):
    settings = iap_settings()
    claims, now = claims_for(settings)
    claims["gcip"] = gcip
    with pytest.raises(IAPAuthenticationError) as exc_info:
        admitted(settings, claims, now)
    assert expected in str(exc_info.value)


@pytest.mark.parametrize(
    "gcip, expected_message",
    [
        (None, "missing IAP gcip"),
        (123, "non-string IAP gcip"),
        (b"{}", "non-string IAP gcip"),
        (" \t\n", "blank IAP gcip"),
        ("x" * (iap_auth.IAP_MAX_GCIP_BYTES + 1), "oversized IAP gcip"),
        ("é" * (iap_auth.IAP_MAX_GCIP_BYTES // 2 + 1), "oversized IAP gcip"),
        ('{"sub":"first","sub":"second"}', "duplicate IAP gcip key"),
        ('{"sub":NaN}', "invalid IAP gcip"),
        ('{"sub":', "invalid IAP gcip"),
        ("[]", "non-object IAP gcip"),
    ],
)
def test_gcip_parse_failures_are_fixed_content_free_categories(gcip, expected_message):
    settings = iap_settings()
    claims, now = claims_for(settings)
    claims["gcip"] = gcip
    with pytest.raises(IAPAuthenticationError) as exc_info:
        admitted(settings, claims, now)
    assert str(exc_info.value) == expected_message
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_gcip_parser_discards_hostile_parser_exception_and_stringification(monkeypatch):
    sentinel = "provider-payload-sentinel-for-sentinel@example.com"

    def hostile_json_loads(*_args, **_kwargs):
        raise RuntimeError(sentinel)

    monkeypatch.setattr(iap_auth.json, "loads", hostile_json_loads)
    settings = iap_settings()
    claims, now = claims_for(settings)
    claims["gcip"] = '{"sentinel":"' + sentinel
    with pytest.raises(IAPAuthenticationError) as exc_info:
        admitted(settings, claims, now)
    assert str(exc_info.value) == "invalid IAP gcip"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert sentinel not in rendered


@pytest.mark.parametrize("nested", [False, True])
def test_gcip_rejects_hostile_mapping_results_without_invoking_mapping_methods(
    monkeypatch, nested
):
    sentinel = "hostile-container-sentinel-for-sentinel@example.com"

    class HostileMapping(Mapping):
        def __getitem__(self, _key):
            raise RuntimeError(sentinel)

        def __iter__(self):
            raise RuntimeError(sentinel)

        def __len__(self):
            raise RuntimeError(sentinel)

        def items(self):
            raise RuntimeError(sentinel)

        def get(self, *_args, **_kwargs):
            raise RuntimeError(sentinel)

    hostile = HostileMapping()
    decoded = {"nested": hostile} if nested else hostile
    monkeypatch.setattr(iap_auth.json, "loads", lambda *_args, **_kwargs: decoded)
    settings = iap_settings()
    claims, now = claims_for(settings)
    claims["gcip"] = "{}"

    with pytest.raises(IAPAuthenticationError) as exc_info:
        admitted(settings, claims, now)
    expected_message = "invalid IAP gcip" if nested else "non-object IAP gcip"
    assert str(exc_info.value) == expected_message
    assert type(exc_info.value) is IAPAuthenticationError
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert sentinel not in rendered


def test_gcip_rejects_cyclic_decoded_container_without_recursion_or_leak(monkeypatch):
    cyclic: dict[str, object] = {}
    cyclic["cycle"] = cyclic
    monkeypatch.setattr(iap_auth.json, "loads", lambda *_args, **_kwargs: cyclic)
    settings = iap_settings()
    claims, now = claims_for(settings)
    claims["gcip"] = "{}"

    with pytest.raises(IAPAuthenticationError) as exc_info:
        admitted(settings, claims, now)
    assert str(exc_info.value) == "invalid IAP gcip"
    assert type(exc_info.value) is IAPAuthenticationError
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


@pytest.mark.parametrize("hostile_method", ["strip", "encode"])
def test_gcip_rejects_hostile_str_subclasses_before_overridable_methods(hostile_method):
    sentinel = "hostile-gcip-str-sentinel-for-sentinel@example.com"

    class HostileGcip(str):
        def strip(self, *_args, **_kwargs):
            if hostile_method == "strip":
                raise RuntimeError(sentinel)
            return super().strip(*_args, **_kwargs)

        def encode(self, *_args, **_kwargs):
            if hostile_method == "encode":
                raise RuntimeError(sentinel)
            return super().encode(*_args, **_kwargs)

    settings = iap_settings()
    claims, now = claims_for(settings)
    claims["gcip"] = HostileGcip("{}")
    with pytest.raises(IAPAuthenticationError) as exc_info:
        admitted(settings, claims, now)
    assert type(exc_info.value) is IAPAuthenticationError
    assert str(exc_info.value) == "non-string IAP gcip"
    assert sentinel not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert sentinel not in rendered


@pytest.mark.parametrize(
    ("location", "surrogate"),
    [
        (location, surrogate)
        for surrogate in ["\ud800", "\udc00"]
        for location in ["used_value", "ignored_value", "object_key", "nested"]
    ],
)
def test_gcip_rejects_lone_surrogates_in_every_decoded_json_location(location, surrogate):
    settings = iap_settings()
    claims, now = claims_for(settings)
    gcip = {
        "sub": "synthetic-user-1",
        "email": "task08-recruiter@ellaexecutivesearch.com",
        "email_verified": True,
        "auth_time": now - 10,
        "firebase": {"sign_in_provider": "google.com"},
    }
    if location == "used_value":
        gcip["sub"] = surrogate
    elif location == "ignored_value":
        gcip["ignored"] = surrogate
    elif location == "object_key":
        gcip[surrogate] = "ignored"
    else:
        gcip["nested"] = {"items": [surrogate], "map": {surrogate: "ignored"}}
    claims["gcip"] = json.dumps(gcip, separators=(",", ":"), ensure_ascii=True)

    with pytest.raises(IAPAuthenticationError) as exc_info:
        admitted(settings, claims, now)
    assert str(exc_info.value) == "invalid IAP gcip"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_gcip_accepts_valid_non_ascii_and_escaped_surrogate_pair_unicode():
    settings = iap_settings()
    claims, now = claims_for(settings)
    gcip = {
        "sub": "synthetic-user-1",
        "email": "task08-recruiter@ellaexecutivesearch.com",
        "email_verified": True,
        "auth_time": now - 10,
        "firebase": {"sign_in_provider": "google.com"},
        "ignored": "Olá 😀",
    }
    claims["gcip"] = json.dumps(gcip, separators=(",", ":"), ensure_ascii=False)
    assert admitted(settings, claims, now).uid == "synthetic-user-1"

    claims["gcip"] = json.dumps(gcip, separators=(",", ":"), ensure_ascii=True)
    assert admitted(settings, claims, now).uid == "synthetic-user-1"


@pytest.mark.parametrize("contains_lone_surrogate", [False, True])
def test_gcip_deep_nested_arrays_never_escape_recursion_error(contains_lone_surrogate):
    settings = iap_settings()
    claims, now = claims_for(settings)
    nested: object = "\ud800" if contains_lone_surrogate else "safe"
    for _ in range(500):
        nested = [nested]
    gcip = {
        "sub": "synthetic-user-1",
        "email": "task08-recruiter@ellaexecutivesearch.com",
        "email_verified": True,
        "auth_time": now - 10,
        "firebase": {"sign_in_provider": "google.com"},
        "ignored": nested,
    }
    claims["gcip"] = json.dumps(gcip, separators=(",", ":"), ensure_ascii=True)

    if contains_lone_surrogate:
        with pytest.raises(IAPAuthenticationError) as exc_info:
            admitted(settings, claims, now)
        assert str(exc_info.value) == "invalid IAP gcip"
        assert exc_info.value.__cause__ is None
        assert exc_info.value.__context__ is None
    else:
        assert admitted(settings, claims, now).uid == "synthetic-user-1"


@pytest.mark.parametrize("numeric_literal", ["1e10000", "-1e10000"])
def test_gcip_rejects_numeric_exponent_overflow_without_leak(numeric_literal):
    settings = iap_settings()
    claims, now = claims_for(settings)
    claims["gcip"] = (
        '{"sub":"synthetic-user-1","email":"task08-recruiter@ellaexecutivesearch.com",'
        f'"email_verified":true,"auth_time":{now - 10},'
        f'"firebase":{{"sign_in_provider":"google.com"}},"ignored":{numeric_literal}}}'
    )

    with pytest.raises(IAPAuthenticationError) as exc_info:
        admitted(settings, claims, now)
    assert str(exc_info.value) == "invalid IAP gcip"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert numeric_literal not in rendered


@pytest.mark.parametrize("numeric", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("nested", [False, True])
def test_gcip_rejects_monkeypatched_nonfinite_numeric_values(numeric, nested, monkeypatch):
    parsed = {
        "sub": "synthetic-user-1",
        "email": "task08-recruiter@ellaexecutivesearch.com",
        "email_verified": True,
        "auth_time": 1,
        "firebase": {"sign_in_provider": "google.com"},
    }
    parsed["ignored"] = {"deep": [numeric]} if nested else numeric
    monkeypatch.setattr(iap_auth.json, "loads", lambda *_args, **_kwargs: parsed)
    settings = iap_settings()
    claims, now = claims_for(settings)
    claims["gcip"] = "{}"

    with pytest.raises(IAPAuthenticationError) as exc_info:
        admitted(settings, claims, now)
    assert str(exc_info.value) == "invalid IAP gcip"
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert "inf" not in rendered.lower()


@pytest.mark.parametrize("value", [0, -1, 1.5, -3.25, True, None])
def test_gcip_accepts_finite_json_scalars_in_ignored_values(value):
    settings = iap_settings()
    claims, now = claims_for(settings)
    gcip = {
        "sub": "synthetic-user-1",
        "email": "task08-recruiter@ellaexecutivesearch.com",
        "email_verified": True,
        "auth_time": now - 10,
        "firebase": {"sign_in_provider": "google.com"},
        "ignored": value,
    }
    claims["gcip"] = json.dumps(gcip, separators=(",", ":"))
    assert admitted(settings, claims, now).uid == "synthetic-user-1"


def test_gcip_utf8_byte_limit_accepts_4096_and_rejects_4097():
    settings = iap_settings()
    claims, now = claims_for(settings)
    prefix = (
        '{"sub":"synthetic-user-1","email":"task08-recruiter@ellaexecutivesearch.com",'
        f'"email_verified":true,"auth_time":{now - 10},'
        '"firebase":{"sign_in_provider":"google.com"},"ignored":"'
    )
    suffix = '"}'
    filler_size = iap_auth.IAP_MAX_GCIP_BYTES - len((prefix + suffix).encode("utf-8"))
    accepted = prefix + ("x" * filler_size) + suffix
    assert len(accepted.encode("utf-8")) == iap_auth.IAP_MAX_GCIP_BYTES
    claims["gcip"] = accepted
    assert admitted(settings, claims, now).uid == "synthetic-user-1"

    claims["gcip"] = prefix + ("x" * (filler_size + 1)) + suffix
    with pytest.raises(IAPAuthenticationError) as exc_info:
        admitted(settings, claims, now)
    assert str(exc_info.value) == "oversized IAP gcip"


@pytest.mark.parametrize(
    "allowed, operators",
    [
        ("a@ellaexecutivesearch.com,b@ellaexecutivesearch.com,c@ellaexecutivesearch.com,d@ellaexecutivesearch.com", "a@ellaexecutivesearch.com"),
        ("a@ellaexecutivesearch.com,b@ellaexecutivesearch.com,c@ellaexecutivesearch.com,d@ellaexecutivesearch.com,e@ellaexecutivesearch.com,a@ellaexecutivesearch.com", "a@ellaexecutivesearch.com"),
        ("a@ellaexecutivesearch.com,b@ellaexecutivesearch.com,c@ellaexecutivesearch.com,d@ellaexecutivesearch.com,e@other.example", "a@ellaexecutivesearch.com"),
        ("a@ellaexecutivesearch.com,b@ellaexecutivesearch.com,c@ellaexecutivesearch.com,d@ellaexecutivesearch.com,not-an-email", "a@ellaexecutivesearch.com"),
        (ADMITTED, "outside@ellaexecutivesearch.com"),
    ],
)
def test_iap_admission_requires_exact_five_corporate_unique_addresses(allowed, operators):
    with pytest.raises(ValueError):
        iap_settings(auth_allowed_emails=allowed, auth_task08_operator_emails=operators)


def test_nonallowlisted_and_sixth_address_configuration_fail_closed():
    with pytest.raises(ValueError):
        iap_settings(auth_allowed_emails=ADMITTED + ",task08-sixth@ellaexecutivesearch.com", auth_task08_operator_emails="task08-operator@ellaexecutivesearch.com")

    settings = iap_settings()
    claims, now = claims_for(settings, email="other@example.com")
    with pytest.raises(IAPAuthenticationError):
        admitted(settings, claims, now)


def test_iap_configuration_rejects_bypass_missing_audience_origin_or_operator():
    with pytest.raises(ValueError):
        iap_settings(auth_bypass=True)
    with pytest.raises(ValueError):
        iap_settings(auth_iap_audience=None)
    with pytest.raises(ValueError):
        iap_settings(auth_iap_frontend_origin="https://attacker.example")
    with pytest.raises(ValueError):
        iap_settings(auth_task08_operator_emails="")
    with pytest.raises(ValueError):
        iap_settings(auth_org_id="another-org")


def test_iap_configuration_rejects_websocket_lifetime_above_absolute_bound():
    with pytest.raises(ValueError):
        iap_settings(auth_iap_ws_max_lifetime_seconds=3301)


@pytest.mark.parametrize("email", [
    "task08-recruiter@ellaexecutive\u017fearch.com",
    "tas\u212a08-recruiter@ellaexecutivesearch.com",
])
def test_email_aliases_are_rejected_before_casefold_normalization(email):
    with pytest.raises(IAPAuthenticationError):
        canonicalize_email(email)
    with pytest.raises(ValueError):
        iap_settings(
            auth_allowed_emails=ADMITTED.replace(
                "task08-recruiter@ellaexecutivesearch.com", email
            )
        )


def test_production_signature_seam_forwards_exact_audience_certs_and_skew(monkeypatch):
    calls: dict[str, object] = {}

    def fake_verify(token, *, request, audience, certs_url, clock_skew_in_seconds):
        calls.update(
            token=token,
            request=request,
            audience=audience,
            certs_url=certs_url,
            clock_skew_in_seconds=clock_skew_in_seconds,
        )
        if token != "synthetic-valid-signed-fixture":
            raise ValueError("tampered")
        return {"iss": "https://cloud.google.com/iap"}

    # Importing through the same production path makes this an offline seam
    # test: no transport is contacted and tampering still reaches the verifier.
    from google.oauth2 import id_token

    monkeypatch.setattr(id_token, "verify_token", fake_verify)
    claims = iap_auth.verify_iap_signature(
        "synthetic-valid-signed-fixture", AUDIENCE
    )
    assert claims["iss"] == "https://cloud.google.com/iap"
    assert calls["token"] == "synthetic-valid-signed-fixture"
    assert calls["audience"] == AUDIENCE
    assert calls["certs_url"] == iap_auth.IAP_PUBLIC_KEY_URL
    assert calls["clock_skew_in_seconds"] == iap_auth.IAP_CLOCK_SKEW_SECONDS
    with pytest.raises(IAPAuthenticationError):
        iap_auth.verify_iap_signature("synthetic-tampered-fixture", AUDIENCE)
