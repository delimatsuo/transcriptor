"""Offline validation of the auth configuration.

This validator is completely offline and does not contact Firebase, Google Cloud,
or any external provider.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.auth import ORG_ID_PATTERN, PROJECT_ID_PATTERN, parse_allowed_emails


REQUIRED_EXACT_VARS = (
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


def check_auth_setup(env: Mapping[str, str]) -> tuple[bool, str]:
    """Pure offline validator for injected environment mapping."""
    key_occurrences: dict[str, list[tuple[str, str]]] = {}
    for k, v in env.items():
        k_upper = k.upper()
        if k_upper in REQUIRED_EXACT_VARS or k_upper == "K_SERVICE":
            key_occurrences.setdefault(k_upper, []).append((k, v))

    # 1. Case-insensitive collision / exact spelling check
    for req_key in REQUIRED_EXACT_VARS:
        occ = key_occurrences.get(req_key, [])
        if len(occ) > 1:
            return False, f"FAIL: duplicate/colliding environment variable for logical key {req_key}"
        if len(occ) == 1 and occ[0][0] != req_key:
            return False, f"FAIL: {req_key} must have exact uppercase spelling"
        if len(occ) == 0:
            return False, f"FAIL: missing required environment variable {req_key}"

    k_service_occs = key_occurrences.get("K_SERVICE", [])
    if len(k_service_occs) > 1:
        return False, "FAIL: duplicate/colliding environment variable for logical key K_SERVICE"
    if len(k_service_occs) == 1 and k_service_occs[0][0] != "K_SERVICE":
        return False, "FAIL: K_SERVICE must have exact uppercase spelling"

    # 2. Check exact values
    if key_occurrences["TARS_RUNTIME_MODE"][0][1] != "hosted-pilot":
        return False, "FAIL: TARS_RUNTIME_MODE must be exact 'hosted-pilot'"
    if key_occurrences["AUTH_BYPASS"][0][1] != "false":
        return False, "FAIL: AUTH_BYPASS must be exact 'false'"
    if key_occurrences["NEXT_PUBLIC_AUTH_BYPASS"][0][1] != "0":
        return False, "FAIL: NEXT_PUBLIC_AUTH_BYPASS must be exact '0'"

    auth_org = key_occurrences["AUTH_ORG_ID"][0][1]
    if not ORG_ID_PATTERN.fullmatch(auth_org):
        return False, "FAIL: AUTH_ORG_ID has invalid syntax"
    if auth_org != "ella-internal":
        return False, "FAIL: AUTH_ORG_ID must be exact 'ella-internal'"

    # 3. Project ID checks
    gcp_proj = key_occurrences["GOOGLE_CLOUD_PROJECT"][0][1]
    firebase_proj = key_occurrences["FIREBASE_PROJECT_ID"][0][1]
    public_fb_proj = key_occurrences["NEXT_PUBLIC_FIREBASE_PROJECT_ID"][0][1]

    if not PROJECT_ID_PATTERN.fullmatch(gcp_proj):
        return False, "FAIL: GOOGLE_CLOUD_PROJECT has invalid syntax"
    if not PROJECT_ID_PATTERN.fullmatch(firebase_proj):
        return False, "FAIL: FIREBASE_PROJECT_ID has invalid syntax"
    if not PROJECT_ID_PATTERN.fullmatch(public_fb_proj):
        return False, "FAIL: NEXT_PUBLIC_FIREBASE_PROJECT_ID has invalid syntax"
    if not (gcp_proj == firebase_proj == public_fb_proj):
        return False, "FAIL: GOOGLE_CLOUD_PROJECT, FIREBASE_PROJECT_ID, and NEXT_PUBLIC_FIREBASE_PROJECT_ID must match"

    # 4. Allowed emails check
    raw_emails = key_occurrences["AUTH_ALLOWED_EMAILS"][0][1]
    try:
        emails = parse_allowed_emails(raw_emails)
    except Exception:
        return False, "FAIL: AUTH_ALLOWED_EMAILS syntax is invalid"

    if len(emails) != 5:
        return False, "FAIL: AUTH_ALLOWED_EMAILS must contain exactly 5 accounts"
    for email in emails:
        if not email.endswith("@ellaexecutivesearch.com"):
            return False, "FAIL: all AUTH_ALLOWED_EMAILS must belong to corporate domain ellaexecutivesearch.com"

    # 5. Check public Firebase and URL variables are non-empty and unpadded
    for pub_var in (
        "NEXT_PUBLIC_FIREBASE_API_KEY",
        "NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN",
        "NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET",
        "NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID",
        "NEXT_PUBLIC_FIREBASE_APP_ID",
        "NEXT_PUBLIC_API_URL",
        "NEXT_PUBLIC_WS_URL",
        "NEXT_PUBLIC_WS_STREAM_URL",
    ):
        raw_val = key_occurrences[pub_var][0][1]
        if not raw_val or raw_val != raw_val.strip():
            return False, f"FAIL: {pub_var} must not be blank or padded"

    msg = (
        "PASS: internal auth configuration verified (count 5)\n"
        "NOTE: offline source verification passed; no provider or real account was contacted"
    )
    return True, msg


def main() -> int:
    ok, msg = check_auth_setup(os.environ)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
