# Legacy Data Purge — Evidence

**Date executed:** 2026-08-03 (evening, America/New_York)
**Operator:** Claude (main session), on explicit owner confirmation
**Authorization:** Owner decision #7 in `docs/superpowers/specs/2026-08-03-launch-vision-and-scope-design.md` ("delete the 16 legacy sessions + 4 PDFs before the first W1 dogfood interview"), plus the owner's explicit "proceed" in chat after reviewing the full dry-run enumeration on 2026-08-03.
**Mechanism:** `backend/scripts/purge_legacy.py --confirm`, which routes every session through `delete_session_everywhere` (`backend/storage/deletion.py`) — recursive subcollection deletion, GCS blob deletion via `gcsPath` references, and an append-only tombstone per session in the `deletions` collection.

## What was enumerated (dry run, pre-deletion)

- **19 sessions** in Firestore project `transcriptor-490222`:
  - 16 legacy sessions from 2026-03-14 (11: the original build night — titles like `test`, `test2`, `debug`, `mic-test`) and 2026-03-16 (5: the first real-interview test day). This matches the README's "16 session records" containment inventory exactly.
  - 3 additional test sessions from 2026-08-01 (the speaker-correlation live-debugging day; owner's own test audio, no candidates). Delta from the expected 16 was explained and included in the owner's confirmation.
- **4 GCS blobs** (the "four private PDFs" from the containment inventory): `Product Executive at Gran.pdf`, `Profile (94).pdf`, and `Profile (98).pdf` ×2, attached to three of the March sessions.
- **28 local FLAC files, 90 MB** in `recordings/` (raw dual-stream audio backups of the legacy sessions).

## What was deleted

| Artifact | Count | Mechanism |
|---|---|---|
| Firestore sessions (incl. all subcollections: transcript, summaries, documents) | 19 | Cascade, per session |
| GCS candidate PDFs | 4 | Cascade (via `gcsPath` on documents) |
| Local raw-audio FLAC backups (`recordings/`) | 28 files, 90 MB | `rm -rf recordings/` |

## Post-deletion verification (all run 2026-08-03)

- Dry-run re-enumeration: **`0 session(s)`**, **`0 GCS blob(s)`**.
- Tombstone count in `deletions` collection: **19** (one per session, each with `reason="legacy_purge_2026-08-03"`, artifact counts, and timestamp).
- `recordings/`: directory no longer exists.

## Incident note

The first `--confirm` run completed all 19 session cascades, then crashed with a 404 in the orphan-blob pass: that pass iterated the **pre-deletion** blob listing, but the cascade had already deleted all 4 blobs (they were session-referenced, not orphans). No data issue — the crash was after all deletions succeeded. The script was fixed the same evening to re-list blobs after the cascade (see `backend/scripts/purge_legacy.py`).

## Note on ADC

The purge was delayed by expired Application Default Credentials, which caused Firestore calls to hang silently (no error surfaced). Root-caused via: TCP ✓ → TLS ✓ → gRPC channel READY ✓ → `RefreshError: Reauthentication is needed`. Fixed by the owner running `gcloud auth application-default login`. A startup credential probe that fails loudly is recommended for the backend (fix-reserve candidate) so expired ADC can never again present as a silent hang — the same failure would have taken down a live interview.
