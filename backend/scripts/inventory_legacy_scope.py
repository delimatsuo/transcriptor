"""Versioned, non-mutating inventory for legacy session ownership scope.

This command is intentionally read-only. It reports session identifiers and
ownership metadata only; it never reads transcript/document content, writes a
quarantine marker, backfills ownership, or deletes anything. Run it only after
the operator has separately authorized a current cloud readback.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterable, Mapping
from typing import Any

from backend.config import get_settings
from backend.startup_credentials import probe_application_default_credentials
from backend.storage.firestore import FirestoreStorage

INVENTORY_VERSION = "week4-auth-legacy-scope-v1"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_scope_inventory(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return stable ownership-only facts suitable for an approval memo."""
    owned: list[dict[str, str | None]] = []
    unowned: list[dict[str, str | None]] = []
    for record in records:
        entry = {
            "id": _optional_text(record.get("id")),
            "ownerId": _optional_text(record.get("ownerId")),
            "orgId": _optional_text(record.get("orgId")),
            "status": _optional_text(record.get("status")),
            "startedAt": _optional_text(record.get("startedAt")),
        }
        target = owned if entry["ownerId"] and entry["orgId"] else unowned
        target.append(entry)

    key = lambda item: (item["id"] or "")
    owned.sort(key=key)
    unowned.sort(key=key)
    return {
        "version": INVENTORY_VERSION,
        "total": len(owned) + len(unowned),
        "ownedCount": len(owned),
        "unownedCount": len(unowned),
        "owned": owned,
        "unowned": unowned,
        "mutation": "none",
    }


async def _run() -> int:
    settings = get_settings()
    await probe_application_default_credentials()
    storage = FirestoreStorage(settings)
    records = await storage.list_sessions(limit=500)
    print(
        json.dumps(
            build_scope_inventory(records),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
