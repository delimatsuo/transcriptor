"""Input sanitization for Chrome extension data."""

from __future__ import annotations

import re
import unicodedata

_NAME_PATTERN = re.compile(r"^[\w\s\-''.]+$", re.UNICODE)
MAX_NAME_LENGTH = 80


def sanitize_participant_name(name: str) -> str:
    """Sanitize participant name from Chrome extension.

    Strips control chars, newlines, limits length, validates characters.
    Returns empty string for invalid names.
    """
    # Strip control characters (category C*)
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C")
    name = name.strip()[:MAX_NAME_LENGTH]
    if not name or not _NAME_PATTERN.match(name):
        return ""
    return name
