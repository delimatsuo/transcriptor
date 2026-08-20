# Launch Week 1 Implementation Plan — Safe & Lawful Real Interviews

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** By Friday of week 1, a real 2-person interview can run on macOS without losing audio, without writing raw audio to disk, lawfully (disclosure + deletion + clean legacy data), and the Windows go/no-go gate decision has real spike evidence.

**Architecture:** All changes stay inside the existing local FastAPI monolith per spec §4 (`docs/superpowers/specs/2026-08-03-launch-vision-and-scope-design.md`). No new services, no new runtime dependencies (one dependency *removal*). Backend changes are small, isolated, and individually testable.

**Tech Stack:** Python 3.12 / FastAPI / sounddevice / Google Cloud STT v2 / Firestore / GCS; Next.js frontend; pytest for backend tests (`.venv/bin/python3 -m pytest backend/tests/ -v`).

## Global Constraints

- Spec is authoritative: `docs/superpowers/specs/2026-08-03-launch-vision-and-scope-design.md`. Week 1 scope ONLY — do not pull W2+ items forward.
- All UI copy and any candidate-facing text: **pt-BR**.
- No raw-audio persistence by default (spec §6). No voiceprint/biometric speaker ID, ever.
- Branch mechanics: merge PR #3 first (`gh pr merge 3 --merge`) — it is CLEAN/MERGEABLE and the W1 dogfood doubles as its manual verification. Then create branch `launch-week-1` from local `staging` (which carries the 3 spec-docs commits) and push it; all W1 work lands there; PR to `staging` at week end. Never push `staging` directly (GH006 protected).
- One commit per completed task minimum; run the full backend test suite before every commit: `.venv/bin/python3 -m pytest backend/tests/ -v` (currently 31 tests — all must stay green).
- **Destructive-action gate:** Task 5 (legacy purge) requires the owner's explicit go-ahead in chat AFTER seeing the enumeration output. Do not run `--confirm` without it.
- Do not touch `/Volumes/Extreme Pro/myprojects/Transcriptor-worktrees/offline-companion-qualification` (Phase-1C track) or `build/worktrees/*` labs.
- Ports 3000–3002 belong to an unrelated app (Ella ATS). Frontend runs on 3003 via `.claude/launch.json`.

---

### Task 1: Remove the unwired VAD stack (torch, torchaudio, silero-vad)

**Files:**
- Modify: `requirements.txt` (delete lines 14–16: `silero-vad==5.1.2`, `torch==2.5.1`, `torchaudio==2.5.1`)
- Delete: `backend/audio/vad.py`
- Modify: `backend/config.py:35-44` (remove the three VAD fields)

**Interfaces:**
- Consumes: nothing.
- Produces: a requirements.txt without ML deps (install shrinks by ~GBs — load-bearing for the Windows spike in Task 9).

- [ ] **Step 1: Verify the VAD is truly unwired**

Run: `grep -rn "vad\|silero\|torch" backend/ --include="*.py" | grep -v tests | grep -v __pycache__`
Expected: matches ONLY in `backend/audio/vad.py` and `backend/config.py` (fields `vad_threshold`, `vad_min_speech_duration_ms`, `vad_silence_timeout_ms`). If any other file imports vad, STOP and report — do not delete.

- [ ] **Step 2: Delete the module and config fields**

`git rm backend/audio/vad.py`. In `backend/config.py`, delete the `# VAD` block (lines 35–44, the three `vad_*` fields). `Settings.model_config` has `"extra": "ignore"`, so stale `VAD_*` entries in anyone's `.env` are harmless.

- [ ] **Step 3: Remove the three requirement pins**

In `requirements.txt`, delete the `silero-vad==5.1.2`, `torch==2.5.1`, `torchaudio==2.5.1` lines.

- [ ] **Step 4: Prove the backend still imports and tests pass**

Run: `.venv/bin/python3 -c "from backend.main import app; print('ok')"` → `ok`
Run: `.venv/bin/python3 -m pytest backend/tests/ -v` → 31 passed.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "chore: remove unwired VAD stack (torch, torchaudio, silero-vad)"
```

---

### Task 2: FLAC backup off by default

**Files:**
- Modify: `backend/config.py` (add `audio_backup_enabled` field, after `audio_backup_dir`)
- Modify: `backend/audio/buffer.py:51-54` (`start()` gates `_open_backup_file()` on the flag)
- Create: `backend/tests/test_audio_buffer.py`

**Interfaces:**
- Consumes: `AudioBuffer(settings, input_queue)` (existing).
- Produces: `Settings.audio_backup_enabled: bool` (default `False`). `AudioBuffer.backup_path` is `None` when disabled. Chunk flow unchanged either way.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_audio_buffer.py
"""FLAC backup must be opt-in (spec §6: no persistent raw audio by default)."""
import asyncio
import numpy as np
from backend.audio.buffer import AudioBuffer
from backend.config import Settings


def make_settings(tmp_path, **overrides) -> Settings:
    return Settings(
        google_cloud_project="test-project",
        audio_backup_dir=str(tmp_path / "recordings"),
        **overrides,
    )


def test_backup_disabled_by_default_writes_no_file(tmp_path):
    settings = make_settings(tmp_path)
    assert settings.audio_backup_enabled is False

    async def run():
        queue: asyncio.Queue = asyncio.Queue()
        buf = AudioBuffer(settings, queue)
        await buf.start()
        await queue.put(np.zeros(1600, dtype=np.float32))
        chunk = await anext(buf.chunks())
        await buf.stop()
        return chunk

    chunk = asyncio.run(run())
    assert chunk.shape == (1600,)                      # audio still flows
    assert not (tmp_path / "recordings").exists()      # nothing touched disk


def test_backup_opt_in_still_works(tmp_path):
    settings = make_settings(tmp_path, audio_backup_enabled=True)

    async def run():
        queue: asyncio.Queue = asyncio.Queue()
        buf = AudioBuffer(settings, queue)
        await buf.start()
        await queue.put(np.zeros(1600, dtype=np.float32))
        await anext(buf.chunks())
        await buf.stop()
        return buf.backup_path

    path = asyncio.run(run())
    assert path is not None and path.exists()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv/bin/python3 -m pytest backend/tests/test_audio_buffer.py -v`
Expected: `test_backup_disabled_by_default_writes_no_file` FAILS (`audio_backup_enabled` doesn't exist / file gets created).

- [ ] **Step 3: Implement**

In `backend/config.py`, after `audio_backup_dir`:

```python
    audio_backup_enabled: bool = Field(
        default=False,
        description=(
            "Opt-in local FLAC crash-insurance recording. MUST stay False by "
            "default: spec 2026-08-03 §6 — no persistent raw audio."
        ),
    )
```

In `backend/audio/buffer.py`, replace `start()`:

```python
    async def start(self) -> None:
        """Open the backup file only when explicitly enabled (dev opt-in)."""
        if self.settings.audio_backup_enabled:
            self._open_backup_file()
        self._running = True
```

(`chunks()` already guards on `self._backup_file is not None` — no change needed there.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python3 -m pytest backend/tests/test_audio_buffer.py backend/tests/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/audio/buffer.py backend/tests/test_audio_buffer.py
git commit -m "feat: FLAC audio backup is opt-in, default off (no persistent raw audio)"
```

---

### Task 3: STT rotation must not drop audio

**Files:**
- Modify: `backend/stt/stream_manager.py` (pending-audio buffer in `send_audio`; flush on next active send)
- Create: `backend/tests/test_stream_manager_rotation.py`
- Create: `backend/scripts/soak_rotation.py` (manual 2-rotation soak harness)

**Interfaces:**
- Consumes: `GoogleSTTStream.send_audio(bytes)` (queues only while `_active`, `backend/stt/google_stt.py:124-127`); `is_active` property.
- Produces: `StreamManager.send_audio` never silently drops while `_running`; buffered chunks flush **in order** before the next live chunk once a stream is active. Bounded: `Settings.buffer_max_chunks` (existing property, 30s worth).

**Why this design:** `_process_responses_loop` sets `self._current_stream = stream` before the stream's `start()` generator runs its first iteration (which is what flips `_active` to True) — so during rotation there is always a window where audio arrives and the old fix location (`send_audio`'s early-return) discarded it. Capture produces ~10 chunks/sec, so flushing on the next `send_audio` call bounds added latency to ≤100ms. The docstring's promised A/B overlap is NOT implemented here (YAGNI — dedup via `_last_emitted_end_time` already exists and rotation gaps close with buffering alone).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_stream_manager_rotation.py
"""Audio sent while no STT stream is active must be buffered, not dropped."""
import asyncio
from backend.config import Settings
from backend.stt.stream_manager import StreamManager


class FakeStream:
    def __init__(self, active: bool):
        self._active = active
        self.received: list[bytes] = []

    @property
    def is_active(self) -> bool:
        return self._active

    async def send_audio(self, audio_bytes: bytes) -> None:
        self.received.append(audio_bytes)


def make_manager() -> StreamManager:
    return StreamManager(Settings(google_cloud_project="test-project"))


def test_audio_buffered_while_stream_inactive():
    mgr = make_manager()
    mgr._running = True
    mgr._current_stream = FakeStream(active=False)

    asyncio.run(mgr.send_audio(b"chunk-1"))
    asyncio.run(mgr.send_audio(b"chunk-2"))

    assert list(mgr._pending_audio) == [b"chunk-1", b"chunk-2"]
    assert mgr._current_stream.received == []


def test_pending_flushes_in_order_before_live_chunk():
    mgr = make_manager()
    mgr._running = True
    mgr._current_stream = FakeStream(active=False)
    asyncio.run(mgr.send_audio(b"during-rotation-1"))
    asyncio.run(mgr.send_audio(b"during-rotation-2"))

    fresh = FakeStream(active=True)
    mgr._current_stream = fresh
    asyncio.run(mgr.send_audio(b"live"))

    assert fresh.received == [b"during-rotation-1", b"during-rotation-2", b"live"]
    assert len(mgr._pending_audio) == 0


def test_pending_buffer_is_bounded():
    mgr = make_manager()
    mgr._running = True
    mgr._current_stream = None
    limit = mgr._pending_audio.maxlen
    for i in range(limit + 10):
        asyncio.run(mgr.send_audio(f"c{i}".encode()))
    assert len(mgr._pending_audio) == limit           # oldest dropped, newest kept
    assert mgr._pending_audio[-1] == f"c{limit + 9}".encode()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python3 -m pytest backend/tests/test_stream_manager_rotation.py -v`
Expected: FAIL — `AttributeError: ... has no attribute '_pending_audio'`.

- [ ] **Step 3: Implement**

In `backend/stt/stream_manager.py` — add to imports: `from collections import deque`. In `__init__`, after `self._last_emitted_end_time = 0.0`:

```python
        # Audio arriving while no stream is active (rotation/recovery window)
        # is buffered here and flushed, in order, on the next active send.
        self._pending_audio: deque[bytes] = deque(
            maxlen=settings.buffer_max_chunks
        )
```

Replace `send_audio` (lines 97–100):

```python
    async def send_audio(self, audio_bytes: bytes) -> None:
        """Send audio to the current stream, buffering across rotation gaps."""
        if self._current_stream and self._current_stream.is_active:
            while self._pending_audio:
                await self._current_stream.send_audio(self._pending_audio.popleft())
            await self._current_stream.send_audio(audio_bytes)
        elif self._running:
            self._pending_audio.append(audio_bytes)
            if len(self._pending_audio) == self._pending_audio.maxlen:
                logger.warning(
                    "rotation_pending_buffer_full",
                    source=self.source_label,
                    buffered=len(self._pending_audio),
                )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python3 -m pytest backend/tests/test_stream_manager_rotation.py backend/tests/ -v`
Expected: all pass.

- [ ] **Step 5: Write the soak harness (manual verification for Step 6)**

```python
# backend/scripts/soak_rotation.py
"""Manual soak: run live capture >2 rotations; verify no transcript gap.

Usage: .venv/bin/python3 -m backend.scripts.soak_rotation
Speak continuously (read aloud / play a podcast into the mic) for ~10 min.
The script prints every final segment with its offset; afterwards it reports
the largest inter-segment gap that overlaps a rotation boundary (~270s, ~540s).
A gap > 5s at a boundary while audio was playing = rotation still loses audio.
"""
import asyncio
import time

from backend.audio.capture import AudioCapture
from backend.audio.buffer import AudioBuffer
from backend.config import get_settings
from backend.schemas.models import TranscriptSegment
from backend.stt.stream_manager import StreamManager

ROTATION = 270.0


async def main() -> None:
    settings = get_settings()
    finals: list[tuple[float, str]] = []
    t0 = time.monotonic()

    def on_transcript(seg: TranscriptSegment) -> None:
        if seg.is_final:
            offset = time.monotonic() - t0
            finals.append((offset, seg.text))
            print(f"[{offset:7.1f}s] {seg.text}")

    queue: asyncio.Queue = asyncio.Queue()
    capture = AudioCapture(settings, queue, device_name=settings.microphone_device_name)
    buffer = AudioBuffer(settings, queue)
    mgr = StreamManager(settings, on_transcript=on_transcript, source_label="soak")

    capture.start()
    await buffer.start()
    await mgr.start()
    try:
        async for chunk in buffer.chunks():
            await mgr.send_audio(buffer.float32_to_int16(chunk))
            if time.monotonic() - t0 > 600:
                break
    finally:
        await mgr.stop()
        await buffer.stop()
        capture.stop()

    print("\n--- gaps at rotation boundaries ---")
    for i in range(1, len(finals)):
        gap = finals[i][0] - finals[i - 1][0]
        for k in (1, 2):
            if finals[i - 1][0] < k * ROTATION < finals[i][0] and gap > 5:
                print(f"SUSPECT GAP {gap:.1f}s across {k*ROTATION:.0f}s boundary")
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
```

Note: check `backend/audio/capture.py` for the exact `AudioCapture` constructor/start signature before running and adjust the three call lines if they differ — the harness is manual tooling, not shipped code.

- [ ] **Step 6: Run the soak (manual, ~10 min, real GCP)**

Run: `.venv/bin/python3 -m backend.scripts.soak_rotation` with continuous speech/podcast audio.
Expected: no `SUSPECT GAP` lines; transcript text spans both ~270s and ~540s boundaries without missing sentences.

- [ ] **Step 7: Commit**

```bash
git add backend/stt/stream_manager.py backend/tests/test_stream_manager_rotation.py backend/scripts/soak_rotation.py
git commit -m "fix: buffer audio across STT stream rotation instead of dropping it"
```

---

### Task 4: Deletion cascade (module + endpoint)

**Files:**
- Create: `backend/storage/deletion.py`
- Modify: `backend/storage/gcs.py` (add `delete_blob`)
- Modify: `backend/main.py` (add `DELETE /api/sessions/{session_id}`)
- Create: `backend/tests/test_deletion.py`

**Interfaces:**
- Consumes: `FirestoreStorage._get_db()` (AsyncClient); `GCSStorage` client/bucket helpers (`backend/storage/gcs.py:15-36`).
- Produces: `async def delete_session_everywhere(session_id: str, db, gcs, *, reason: str = "owner_request") -> dict` — deletes every subcollection dynamically, deletes GCS blobs referenced by `documents.*.gcsPath`, deletes the session doc, writes a tombstone to collection `deletions`, returns `{"session_id", "subcollections_deleted", "docs_deleted", "gcs_blobs_deleted"}`. This is the single deletion path — Task 5 and all future retention TTLs call it.

- [ ] **Step 1: Write the failing test (fake Firestore/GCS objects — no emulator)**

```python
# backend/tests/test_deletion.py
"""delete_session_everywhere must cascade: subcollections, GCS blobs, doc, tombstone."""
import asyncio
from backend.storage.deletion import delete_session_everywhere


class FakeDoc:
    def __init__(self, doc_id, data, subcollections=None):
        self.id, self._data = doc_id, data
        self._subs = subcollections or {}
        self.deleted = False

    def to_dict(self):
        return self._data

    # --- Firestore surface used by the cascade ---
    async def delete(self):
        self.deleted = True

    def collections(self):
        async def gen():
            for name, docs in self._subs.items():
                yield FakeCollection(name, docs)
        return gen()

    class _Ref:  # what collection.stream() yields has .reference
        pass


class FakeCollection:
    def __init__(self, name, docs):
        self.id, self._docs = name, docs

    def stream(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class FakeDB:
    def __init__(self, session_doc):
        self._session_doc = session_doc
        self.tombstones = []

    def collection(self, name):
        db = self

        class Col:
            def document(self, doc_id=None):
                if name == "sessions":
                    return db._session_doc
                class T:  # deletions tombstone
                    async def set(self, data):
                        db.tombstones.append(data)
                return T()
        return Col()


class FakeGCS:
    def __init__(self):
        self.deleted = []

    def delete_blob(self, path):
        self.deleted.append(path)
        return True


def test_cascade_deletes_everything_and_tombstones():
    seg = FakeDoc("seg1", {"text": "hi"})
    cv = FakeDoc("d1", {"gcsPath": "sessions/s1/cv.pdf", "type": "resume"})
    session = FakeDoc("s1", {"title": "t"}, {"transcript": [seg], "documents": [cv]})
    # give inner docs their own delete/collections surface
    db, gcs = FakeDB(session), FakeGCS()

    result = asyncio.run(delete_session_everywhere("s1", db, gcs))

    assert seg.deleted and cv.deleted and session.deleted
    assert gcs.deleted == ["sessions/s1/cv.pdf"]
    assert db.tombstones and db.tombstones[0]["sessionId"] == "s1"
    assert result["gcs_blobs_deleted"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python3 -m pytest backend/tests/test_deletion.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.storage.deletion`.

- [ ] **Step 3: Implement the cascade**

```python
# backend/storage/deletion.py
"""Single deletion path: remove a session everywhere (LGPD Art. 18 eliminação).

Dynamically enumerates subcollections so future ones (notes, reports) are
covered without editing this module. Writes an append-only tombstone."""
from __future__ import annotations

from datetime import datetime

import structlog

logger = structlog.get_logger()


async def delete_session_everywhere(
    session_id: str, db, gcs, *, reason: str = "owner_request"
) -> dict:
    session_ref = db.collection("sessions").document(session_id)
    subs_deleted, docs_deleted, blobs_deleted = 0, 0, 0

    async for sub in session_ref.collections():
        subs_deleted += 1
        async for doc in sub.stream():
            data = doc.to_dict() or {}
            gcs_path = data.get("gcsPath")
            if gcs_path and gcs is not None:
                if gcs.delete_blob(gcs_path):
                    blobs_deleted += 1
            await doc.delete()
            docs_deleted += 1

    await session_ref.delete()

    await db.collection("deletions").document().set({
        "sessionId": session_id,
        "deletedAt": datetime.utcnow(),
        "reason": reason,
        "subcollectionsDeleted": subs_deleted,
        "docsDeleted": docs_deleted,
        "gcsBlobsDeleted": blobs_deleted,
    })
    logger.info("session_deleted_everywhere", session_id=session_id,
                docs=docs_deleted, blobs=blobs_deleted)
    return {
        "session_id": session_id,
        "subcollections_deleted": subs_deleted,
        "docs_deleted": docs_deleted,
        "gcs_blobs_deleted": blobs_deleted,
    }
```

Note for implementer: real `AsyncDocumentReference.collections()` / `stream()` are async iterables exactly as faked; `stream()` yields snapshots whose `.reference.delete()` is the real deletion call — adapt: `await doc.reference.delete()` on real snapshots. Make the fake match the real surface you settle on (snapshot with `.reference`), not the other way around; the test above may need `doc.reference` adjusted accordingly — keep the assertions identical.

In `backend/storage/gcs.py`, add (reading the existing `upload_file` to match path format first):

```python
    def delete_blob(self, gcs_path: str) -> bool:
        """Delete a blob by stored path; tolerates gs:// prefix. Returns success."""
        try:
            name = gcs_path
            if name.startswith("gs://"):
                name = name.split("/", 3)[3]  # strip gs://bucket/
            self._get_bucket().blob(name).delete()
            return True
        except Exception:
            logger.warning("gcs_delete_failed", path=gcs_path)
            return False
```

In `backend/main.py`, next to the other session endpoints:

```python
@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session everywhere (Firestore subcollections, GCS docs, tombstone)."""
    assert firestore_storage
    from backend.storage.deletion import delete_session_everywhere
    db = await firestore_storage._get_db()
    return await delete_session_everywhere(session_id, db, gcs_storage)
```

(Match the actual module-level name for the GCS storage instance in `main.py` — grep `GCSStorage(` there; pass `None` if no instance exists at startup.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python3 -m pytest backend/tests/test_deletion.py backend/tests/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/storage/deletion.py backend/storage/gcs.py backend/main.py backend/tests/test_deletion.py
git commit -m "feat: session deletion cascade with GCS cleanup and tombstone audit"
```

---

### Task 5: Legacy data purge (OWNER-GATED)

**Files:**
- Create: `backend/scripts/purge_legacy.py`
- Create: `docs/current-state/2026-08-03-legacy-data-purge-evidence.md` (output of the run)

**Interfaces:**
- Consumes: `delete_session_everywhere` (Task 4), `FirestoreStorage`, `GCSStorage`.
- Produces: an empty `sessions` collection, GCS bucket with no candidate PDFs, evidence doc.

- [ ] **Step 1: Write the enumeration/purge script**

```python
# backend/scripts/purge_legacy.py
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
    for s in sessions:
        print(f"  {s['id']}  status={s.get('status')}  started={s.get('startedAt')}  title={s.get('title','')!r}")

    blobs = list(gcs._get_bucket().list_blobs())
    print(f"\n{len(blobs)} GCS blob(s):")
    for b in blobs:
        print(f"  {b.name}  ({b.size} bytes)")

    if not confirm:
        print("\nDry run. Re-run with --confirm to DELETE EVERYTHING above.")
        return

    for s in sessions:
        result = await delete_session_everywhere(s["id"], db, gcs, reason="legacy_purge_2026-08-03")
        print(f"deleted {s['id']}: {result}")
    for b in blobs:  # orphans not referenced by any session doc
        b.delete()
        print(f"deleted orphan blob {b.name}")
    print("PURGE COMPLETE")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--confirm", action="store_true")
    asyncio.run(main(p.parse_args().confirm))
```

- [ ] **Step 2: Dry run and STOP for the owner**

Run: `.venv/bin/python3 -m backend.scripts.purge_legacy`
Paste the full listing to the owner in chat. **Do not proceed without an explicit "yes, delete" from Deli referencing this listing.** (The decision to delete is made; this gate verifies the *object list* matches expectation — ~16 sessions + 4 PDFs. If the listing differs wildly, stop and investigate.)

- [ ] **Step 3: Purge and capture evidence**

Run: `.venv/bin/python3 -m backend.scripts.purge_legacy --confirm` (save full stdout).
Run the dry-run again → expect `0 session(s)`, `0 GCS blob(s)`.
Also wipe local audio: `ls recordings/ 2>/dev/null` → if files exist, `rm -rf recordings/` (they are the FLAC backups Task 2 stops producing).
Write both outputs into `docs/current-state/2026-08-03-legacy-data-purge-evidence.md` (before-listing, command, after-listing, date, operator).

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/purge_legacy.py docs/current-state/2026-08-03-legacy-data-purge-evidence.md
git commit -m "chore: purge legacy candidate data with evidence (owner decision 2026-08-03 #7)"
```

---

### Task 6: Candidate disclosure flow

**Files:**
- Create: `docs/product/2026-08-03-candidate-disclosure-script.md`
- Modify: `backend/schemas/models.py:71-81` (add `notice_given` to `Session`)
- Modify: `backend/main.py:476-497` (`create_session` accepts `notice_given`)
- Modify: `backend/storage/firestore.py:33-51` (persist `noticeGiven`)
- Modify: `frontend/src/components/SessionControls.tsx` (interview-mode checkbox, gates start)
- Create: `backend/tests/test_session_notice.py`

**Interfaces:**
- Consumes: `POST /api/sessions?mode=&title=` (existing, `SessionControls.tsx:105-106` builds `URLSearchParams({ mode, title })`).
- Produces: `Session.notice_given: bool = False`; query param `notice_given=true|false`; Firestore field `noticeGiven`.

- [ ] **Step 1: Write the disclosure script doc (pt-BR, from spec §6 + privacy research)**

```markdown
# Aviso ao candidato — roteiro de divulgação (v1, 2026-08-03)

## Antes da entrevista (e-mail de confirmação — parágrafo a incluir)
"Para garantir um processo criterioso e fiel ao que for conversado, nossa
entrevista será transcrita em tempo real por uma ferramenta de apoio da Ella.
O áudio não é gravado nem armazenado — apenas a transcrição de texto, suas
anotações de avaliação e o relatório final, com acesso restrito e prazo de
retenção definido. Seus dados são tratados conforme a LGPD; você pode
solicitar acesso ou exclusão a qualquer momento pelo contato
[dpo@ellaexecutivesearch.com — Deli Matsuo, Encarregado]. Caso prefira que a
transcrição não seja utilizada, é só nos avisar — a entrevista acontece
normalmente da mesma forma."

## No início da entrevista (verbal, fica registrado na transcrição)
"Antes de começarmos: como mencionei no e-mail, uso uma ferramenta que
transcreve nossa conversa para eu me concentrar em você em vez de anotações.
O áudio não fica gravado — só o texto. Tudo bem para você?"

## Se o candidato recusar
Encerrar a sessão na ferramenta (ou não iniciá-la) e conduzir a entrevista
com anotações manuais. Registrar a recusa apenas como fato operacional.
```

- [ ] **Step 2: Write failing backend test**

```python
# backend/tests/test_session_notice.py
from backend.schemas.models import Session


def test_session_has_notice_given_defaulting_false():
    s = Session()
    assert s.notice_given is False
    s2 = Session(notice_given=True)
    assert s2.notice_given is True
```

Run: `.venv/bin/python3 -m pytest backend/tests/test_session_notice.py -v` → FAIL (unexpected keyword).

- [ ] **Step 3: Implement backend**

`backend/schemas/models.py` — inside `class Session`, after `status`:

```python
    notice_given: bool = False  # candidate informed of transcription (LGPD notice)
```

`backend/main.py` `create_session` — add parameter `notice_given: bool = False` and pass through:

```python
async def create_session(
    mode: str = "meeting",
    title: str = "",
    notice_given: bool = False,
):
    ...
    session = session_mgr.create_session(mode=session_mode, title=title)
    session.notice_given = notice_given
```

(`create_session` on the manager doesn't take it — setting the attribute before `save_session` is sufficient; check `session_mgr.create_session` signature and inline it there instead if cleaner.)

`backend/storage/firestore.py` `save_session` data dict — add: `"noticeGiven": session.notice_given,`

- [ ] **Step 4: Implement frontend checkbox**

In `SessionControls.tsx`: add state `const [noticeGiven, setNoticeGiven] = useState(false);`. In the interview-mode section of the pre-session form (the component already branches on `mode === "interview"` at line ~113), render:

```tsx
{mode === "interview" && (
  <label className="flex items-start gap-2 text-sm">
    <input
      type="checkbox"
      checked={noticeGiven}
      onChange={(e) => setNoticeGiven(e.target.checked)}
    />
    <span>
      Confirmo que o candidato foi avisado sobre a transcrição desta
      entrevista (roteiro de aviso da Ella).
    </span>
  </label>
)}
```

Extend the params at line 105: `const params = new URLSearchParams({ mode, title, notice_given: String(noticeGiven) });` and disable the start button when `mode === "interview" && !noticeGiven`. Match the surrounding design-token classes (see `frontend/src/lib/tokens.ts` and neighboring controls) — copy in pt-BR as shown.

- [ ] **Step 5: Verify**

Run: `.venv/bin/python3 -m pytest backend/tests/ -v` → all pass.
Run: `cd frontend && npx tsc --noEmit && npm test` → clean, unit tests pass.

- [ ] **Step 6: Commit**

```bash
git add docs/product/2026-08-03-candidate-disclosure-script.md backend/schemas/models.py backend/main.py backend/storage/firestore.py frontend/src/components/SessionControls.tsx backend/tests/test_session_notice.py
git commit -m "feat: candidate disclosure flow — pt-BR script, notice_given flag, interview start gate"
```

---

### Task 7: LIA document + GCP data-posture checklist

**Files:**
- Create: `docs/privacy/2026-08-03-lia-legitimo-interesse.md`
- Create: `docs/privacy/2026-08-03-gcp-data-posture-checklist.md`

**Interfaces:** none (documents). Sources: `docs/superpowers/reviews/2026-08-03-launch-scope-panel/4-privacy.md` and `8-lgpd-retention-controller-research.md` — write from those, citing them; do not re-research.

- [ ] **Step 1: Write the LIA** — sections: (1) processing described (interview transcription, notes, report; no audio retention; no biometrics); (2) legal basis: legítimo interesse Art. 7 IX for in-search processing, with the three-part balancing test (legitimacy/necessity/balancing) filled in concretely for executive search; (3) data-subject expectations + safeguards (disclosure script, deletion cascade, retention table from spec §6, DPO: Deli Matsuo); (4) controller allocation (independent singular controllers, contract clause reference); (5) out-of-basis items (talent pool → consent). Portuguese. ~1 page.
- [ ] **Step 2: Write the GCP checklist** with per-item verification commands where scriptable (`gcloud config list`, project IAM/API state) and console-verification instructions where not (Vertex caching opt-out, abuse-monitoring opt-out, STT data-logging state), each with an EVIDENCE line to fill during execution. Mark owner-only items (billing/console access) clearly.
- [ ] **Step 3: Execute the scriptable checks against the production project**, fill evidence lines; leave console items flagged `PENDENTE (owner)` and tell the owner exactly what to click.
- [ ] **Step 4: Commit**

```bash
git add docs/privacy/2026-08-03-lia-legitimo-interesse.md docs/privacy/2026-08-03-gcp-data-posture-checklist.md
git commit -m "docs: LIA (legitimo interesse) and GCP data-posture checklist with evidence"
```

---

### Task 8: Dogfood pre-flight kit + Friday interview

**Files:**
- Create: `backend/scripts/preflight_audio.py`
- Create: `docs/launch/preflight-checklist.md`

**Interfaces:**
- Consumes: sounddevice device enumeration (same API as `backend/audio/capture.py`).
- Produces: PASS/FAIL routing verdict per channel; used on macOS now and reused verbatim in the Windows spike (Task 9).

- [ ] **Step 1: Write the meter**

```python
# backend/scripts/preflight_audio.py
"""Pre-interview audio routing check: are BOTH capture channels alive?

Usage: play any audio (YouTube) at normal volume, speak into the mic, then:
  .venv/bin/python3 -m backend.scripts.preflight_audio
PASS requires signal on BOTH devices within 10 seconds.
"""
import numpy as np
import sounddevice as sd

from backend.audio.capture import find_input_device
from backend.config import get_settings

THRESHOLD = 0.001  # RMS floor ≈ silence
SECONDS = 10


def rms_meter(device_name: str, label: str, samplerate: int) -> bool:
    idx = find_input_device(device_name) if device_name else None
    peak = 0.0
    with sd.InputStream(device=idx, channels=1, samplerate=samplerate) as stream:
        for _ in range(int(SECONDS * 10)):
            data, _ = stream.read(samplerate // 10)
            peak = max(peak, float(np.sqrt(np.mean(np.square(data)))))
    ok = peak > THRESHOLD
    print(f"{'PASS' if ok else 'FAIL'}  {label:<14} peak RMS={peak:.5f}  (device={device_name or 'system default'})")
    return ok


def main() -> None:
    s = get_settings()
    mic_ok = rms_meter(s.microphone_device_name, "microphone", s.sample_rate)
    sys_ok = rms_meter(s.blackhole_device_name, "system-audio", s.sample_rate)
    if not sys_ok:
        print("\nsystem-audio FAIL → check: macOS output device must be the "
              "Multi-Output Device (containing BlackHole 2ch + your headphones). "
              "Windows: meeting app output → CABLE Input, 'Listen to this device' on.")
    raise SystemExit(0 if (mic_ok and sys_ok) else 1)


if __name__ == "__main__":
    main()
```

(Verify `find_input_device`'s actual name/signature in `backend/audio/capture.py:17-42` and adjust the import — it may be a method or module function.)

- [ ] **Step 2: Write `docs/launch/preflight-checklist.md`** — ordered: (1) System output → Multi-Output Device (macOS) and confirm it contains BlackHole 2ch + real headphones (Audio MIDI Setup); (2) headset on (no speakers — bleed poisons labels); (3) run `preflight_audio.py` → both PASS; (4) disclosure checkbox flow reminder; (5) start backend + frontend (port 3003); (6) after the interview: check transcript for both labels, gaps at ~4:30 multiples, and file every anomaly as a defect.
- [ ] **Step 3: Run the checklist end-to-end yourself (agent) with a YouTube video as the "candidate"** — a smoke rehearsal, NOT the real test: backend up, session created (notice box ticked), both speaker labels appear, stop, report generated. Fix anything broken before Friday.
- [ ] **Step 4: Friday — the owner runs a real 2-person interview.** Agent supports: pre-flight, live log monitoring (`audio_device_silent`, `rotation_pending_buffer_full`, STT errors), collect every issue into the fix-reserve list. This closes PR #3's manual-verification box and the dual-stream baseline question (never yet verified with a real second speaker).
- [ ] **Step 5: Commit**

```bash
git add backend/scripts/preflight_audio.py docs/launch/preflight-checklist.md
git commit -m "feat: pre-interview audio routing preflight (meter + checklist)"
```

---

### Task 9: Windows spike kit + gate decision memo

**Files:**
- Create: `docs/launch/windows-spike-runbook.md`
- Create: `docs/launch/2026-08-0X-windows-gate-decision.md` (filled at the gate)

**Interfaces:** consumes Task 8's `preflight_audio.py` (cross-platform) and Task 1's slim requirements.txt.

- [ ] **Step 1: Write the runbook.** Contents, in order: (1) prerequisites — Windows 11 (`winver` ≥ 22H2; Win10 unsupported since 2025-10-14), Chrome or Edge, headset; (2) install [VB-CABLE](https://vb-audio.com/Cable/) (free driver; reboot); (3) install Python 3.12 (python.org, "Add to PATH"), `git clone`, `python -m venv .venv`, `.venv\Scripts\pip install -r requirements.txt` (fast now — no torch); (4) `.env`: copy `.env.example`, set `GOOGLE_APPLICATION_CREDENTIALS` to the provided service-account JSON, `BLACKHOLE_DEVICE_NAME=CABLE Output`, `MICROPHONE_DEVICE_NAME=` (default mic); (5) audio routing: Windows Settings → Sound → set the **meeting app's** output to `CABLE Input`; Sound Control Panel → Recording → `CABLE Output` → Properties → Listen → **"Listen to this device"** → recruiter's headphones (this is how the recruiter still hears the candidate); (6) validation: run `preflight_audio.py` (both PASS), then a 30-minute real Meet/Zoom call: confirm Entrevistador/Candidato labels correct, no silence warnings, rotation boundaries clean; (7) capture the log + observations for the gate memo. Include a troubleshooting table: no system audio → meeting app output not CABLE Input; recruiter can't hear → Listen-to-device off; both voices as Candidato → mic captured by CABLE too (headset/mic selection).
- [ ] **Step 2: Write the gate-memo template** with the decision criteria from spec §5 verbatim: spike clean + named Windows recruiter in cohort → Option A (Windows lands W5, ≈1.5–2 agent-weeks); spike dirty or routing too fragile for non-engineers → Option B (macOS launch W4, Windows fast-follow with committed date). Fields: machine tested, Windows build, spike results per validation item, failure notes, recommendation, OWNER DECISION + date.
- [ ] **Step 3: Execute the spike with the owner + one named Windows recruiter's machine** (owner supplies access, Task from spec §8 Q2 — "several recruiters use Windows", pick one). Fill the memo; present A/B to the owner with the evidence. **The decision is the owner's.**
- [ ] **Step 4: Commit**

```bash
git add docs/launch/windows-spike-runbook.md docs/launch/2026-08-0X-windows-gate-decision.md
git commit -m "docs: Windows VB-CABLE spike runbook and gate decision memo"
```

---

## Week-end close-out

- [ ] Push `launch-week-1` and open a PR to `staging` titled "Launch week 1: safe & lawful real interviews"; body lists the 9 tasks, soak/dogfood evidence, and the gate decision.
- [ ] Update `docs/superpowers/specs/2026-08-03-launch-vision-and-scope-design.md` §5/§8 with the Windows gate outcome.
- [ ] Carry every unfixed dogfood finding into the Week 2 plan as named defects.

## Self-review notes (spec coverage)

Spec §3 W1 line-items → tasks: rotation fix (T3), FLAC default-off (T2), torch removal (T1), disclosure+LIA (T6, T7), legacy deletion (T5, gated), deletion-cascade runbook (T4), ZDR/CDPA verification (T7), Friday 2-person interview (T8), Windows spike + gate (T9). W1 optional stretch (0002-shaped seam) deliberately excluded — it is a Should and the week is full. Types/signatures cross-checked: `Settings.buffer_max_chunks` exists (`backend/config.py:94-97`); `GoogleSTTStream.send_audio` gates on `_active` (`google_stt.py:124-127`) — which is why the pending-buffer lives in `StreamManager`, not `GoogleSTTStream`.
