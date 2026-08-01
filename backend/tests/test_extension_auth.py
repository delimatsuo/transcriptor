"""Tests for _validate_extension_token — the only auth check gating the
Chrome-extension endpoints (active-speaker, participants, heartbeat, clock-sync).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend import main


@pytest.fixture(autouse=True)
def _clean_extension_tokens():
    main.extension_tokens.clear()
    yield
    main.extension_tokens.clear()


def test_rejects_unlinked_session():
    with pytest.raises(HTTPException) as exc_info:
        main._validate_extension_token("no-such-session", "Bearer anything")
    assert exc_info.value.status_code == 403


def test_rejects_missing_authorization_header():
    main.extension_tokens["s1"] = "secret-token"
    with pytest.raises(HTTPException) as exc_info:
        main._validate_extension_token("s1", None)
    assert exc_info.value.status_code == 401


def test_rejects_non_bearer_authorization_header():
    main.extension_tokens["s1"] = "secret-token"
    with pytest.raises(HTTPException) as exc_info:
        main._validate_extension_token("s1", "Basic secret-token")
    assert exc_info.value.status_code == 401


def test_rejects_wrong_token():
    main.extension_tokens["s1"] = "secret-token"
    with pytest.raises(HTTPException) as exc_info:
        main._validate_extension_token("s1", "Bearer wrong-token")
    assert exc_info.value.status_code == 403


def test_accepts_correct_bearer_token():
    main.extension_tokens["s1"] = "secret-token"
    # Must not raise.
    main._validate_extension_token("s1", "Bearer secret-token")


def test_one_session_token_does_not_validate_another_session():
    main.extension_tokens["s1"] = "token-for-s1"
    main.extension_tokens["s2"] = "token-for-s2"
    with pytest.raises(HTTPException) as exc_info:
        main._validate_extension_token("s1", "Bearer token-for-s2")
    assert exc_info.value.status_code == 403
