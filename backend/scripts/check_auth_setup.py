"""Offline validation of the Week 4 auth configuration.

This deliberately does not contact Firebase, Firestore, GCS, or a provider.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    required = {
        "GOOGLE_CLOUD_PROJECT": os.getenv("GOOGLE_CLOUD_PROJECT", ""),
        "AUTH_ALLOWED_EMAILS": os.getenv("AUTH_ALLOWED_EMAILS", ""),
    }
    missing = [name for name, value in required.items() if not value.strip()]
    if missing:
        print("FAIL: configure " + ", ".join(missing))
        return 1
    emails = [item.strip() for item in required["AUTH_ALLOWED_EMAILS"].split(",") if item.strip()]
    if any("@" not in email or "*" in email for email in emails):
        print("FAIL: AUTH_ALLOWED_EMAILS must contain exact email addresses only")
        return 1
    print("PASS: internal Firebase auth configuration is syntactically present")
    print("NEXT: run gcloud auth application-default login before backend startup")
    print("NEXT: configure NEXT_PUBLIC_FIREBASE_* in frontend/.env.local")
    print("NOTE: this check is offline and is not deployment or real-interview evidence")
    return 0


if __name__ == "__main__":
    sys.exit(main())
