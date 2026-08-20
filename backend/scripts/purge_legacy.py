"""Enumerate (default) or purge (--confirm) ALL sessions and GCS candidate docs.

Owner decision 2026-08-03 (spec header, decision #7): delete the 16 legacy
sessions + 4 private PDFs before the first W1 dogfood interview.

Usage:
  .venv/bin/python3 -m backend.scripts.purge_legacy            # list only
  .venv/bin/python3 -m backend.scripts.purge_legacy --confirm  # DELETE ALL
"""
import argparse
import asyncio

from backend.config import get_settings
from backend.storage.deletion import delete_session_everywhere
from backend.storage.firestore import FirestoreStorage
from backend.storage.gcs import GCSStorage


async def main(confirm: bool) -> None:
    settings = get_settings()
    fs = FirestoreStorage(settings)
    gcs = GCSStorage(settings)
    db = await fs._get_db()

    sessions = await fs.list_sessions(limit=500)
    print(f"{len(sessions)} session(s) in project {settings.google_cloud_project}:")
    for session in sessions:
        print(
            f"  {session['id']}  status={session.get('status')}  "
            f"started={session.get('startedAt')}  title={session.get('title', '')!r}"
        )

    blobs = list(gcs._get_bucket().list_blobs())
    print(f"\n{len(blobs)} GCS blob(s):")
    for blob in blobs:
        print(f"  {blob.name}  ({blob.size} bytes)")

    if not confirm:
        print("\nDry run. Re-run with --confirm to DELETE EVERYTHING above.")
        return

    for session in sessions:
        result = await delete_session_everywhere(
            session["id"], db, gcs, reason="legacy_purge_2026-08-03"
        )
        print(f"deleted {session['id']}: {result}")
    # Re-list: the cascade above already deletes session-referenced blobs, so
    # the pre-deletion listing is stale (first run 2026-08-03 crashed 404 here).
    remaining = list(gcs._get_bucket().list_blobs())
    for blob in remaining:  # true orphans not referenced by any session doc
        blob.delete()
        print(f"deleted orphan blob {blob.name}")
    print("PURGE COMPLETE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    asyncio.run(main(parser.parse_args().confirm))
