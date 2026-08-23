# Task 05a Implementation Report

## 1. Summary of Changes
- **Gateway Hello & Intended-Source Tracking (`backend/main.py`)**:
  - Added support for client `hello` text message: `{"type": "hello", "sources": ["system_audio"]}` and `{"type": "microphone"}`.
  - Implemented strict validation for `hello` messages (non-empty list containing only `"microphone"` and/or `"system_audio"`), deduplicating sources and supporting idempotent union without resetting initial first-frame deadlines.
  - Added semantic rejection for invalid hello messages logging `native_companion_hello_invalid` with `session_id` only.
  - Extended `native_session_health` to track per-source connection counts (`source_connections`) and active session alerts (`alerts`).
- **First-Frame Deadline Watchdog (`backend/main.py`)**:
  - Implemented `NATIVE_NEVER_PRODUCED_TIMEOUT_SECONDS = 15.0`.
  - Added never-produced check to `stall_watchdog` that flags an intended source as `device_unavailable`, adds the exact pt-BR message to `session_health["alerts"]`, logs `native_source_never_produced_frames`, and emits health exactly once.
  - Configured first received audio frame to clear first-frame deadline and alerts, recovering source to `healthy` with `message=None`.
- **Disconnect Truth & Reconnecting State (`backend/main.py`, `frontend/src/types/ws.ts`)**:
  - Added `"reconnecting"` to `SourceHealthState` in `frontend/src/types/ws.ts` and updated `CompanionHealthPayload.message?: string | null`.
  - On socket disconnect, decremented connection count and per-source ownership; when a source reaches zero owners during an active session (`session_id in stream_keys` and `session.status == SessionStatus.ACTIVE`), set source to `"reconnecting"` and derived `physical_capture` as `"unknown"`. When session is stopped, set source to `"unknown"` and `physical_capture` to `"stopped"`.
- **UI & Presentation (`frontend/`)**:
  - Created `frontend/src/lib/companionHealth.ts` exporting pure `formatSourceHealth` helper with exact pt-BR labels (`Microfone: Reconectando…`, `Áudio do Sistema: Reconectando…`), amber badge styling, and `↻` icon.
  - Updated `frontend/src/components/CaptureSourceStatus.tsx` to render an amber alert banner below source badges with `role="status"`, `aria-live="polite"`, and `⚠` icon when `message` is present.
  - Threaded `companionMessage` from `useWebSocket` hook through `page.tsx` and `InterviewLiveView.tsx` into `CaptureSourceStatus.tsx`.

## 2. Test Execution & Outputs

### RED Phase Test Output (Frontend)
```
# Subtest: formats reconnecting state for microphone with exact label, icon, and amber color
not ok 34 - formats reconnecting state for microphone with exact label, icon, and amber color
  ---
  duration_ms: 0.089
  location: '/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend/src/lib/companionHealth.test.ts:6:1'
  failureType: 'testCodeFailure'
  error: 'Cannot find module \'./companionHealth.ts\''
  code: 'ERR_MODULE_NOT_FOUND'
  ...
1..60
# tests 60
# suites 0
# pass 59
# fail 1
```

### RED Phase Test Output (Backend)
```
FAILED backend/tests/test_native_stream_endpoint.py::test_health_emitted_on_connect_first_frame_and_disconnect
FAILED backend/tests/test_native_stream_endpoint.py::test_stall_watchdog_flags_and_recovers_source_health
FAILED backend/tests/test_native_stream_endpoint.py::test_health_companion_connect_disconnect_does_not_clobber_open_mic_connection
FAILED backend/tests/test_native_stream_endpoint.py::test_health_companion_disconnect_only_resets_its_own_source
FAILED backend/tests/test_native_stream_endpoint.py::test_hello_never_produced_source_alarms_then_recovers
FAILED backend/tests/test_native_stream_endpoint.py::test_announced_source_disconnect_is_reconnecting_not_stopped
FAILED backend/tests/test_native_stream_endpoint.py::test_invalid_hello_does_not_claim_sources
7 failed, 28 passed in 3.99s
```

### GREEN Phase Test Output (Backend Endpoint Suite)
```
.venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q
...................................                                      [100%]
35 passed in 2.81s
```

### GREEN Phase Test Output (Full Backend Suite)
```
.venv/bin/python -m pytest backend/tests -q
........................................................................ [ 24%]
........................................................................ [ 48%]
........................................................................ [ 72%]
........................................................................ [ 96%]
............                                                             [100%]
300 passed in 4.72s
```

### GREEN Phase Test Output (Frontend Test Suite)
```
npm test (in frontend/)
1..62
# tests 62
# suites 0
# pass 62
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 221.90375
```

## 3. Build Output

### Frontend Build (`npm run build`)
```
> tars-frontend@0.1.0 build
> next build --webpack

▲ Next.js 16.3.0 (webpack)
- Environments: .env.local
✓ Running next.config.ts took 94ms

  Creating an optimized production build ...
✓ Compiled successfully in 2.1s
  Running TypeScript ...
  Finished TypeScript in 1552ms ...
  Collecting page data using 4 workers ...
  Generating static pages using 4 workers (0/3) ...
✓ Generating static pages using 4 workers (3/3) in 380ms
  Finalizing page optimization ...
  Collecting build traces ...

Route (app)
┌ ○ /
└ ○ /_not-found


○  (Static)  prerendered as static content
```

## 4. File List
- **Modified**:
  - `backend/main.py`
  - `backend/tests/test_native_stream_endpoint.py`
  - `frontend/src/types/ws.ts`
  - `frontend/src/hooks/useWebSocket.ts`
  - `frontend/src/components/CaptureSourceStatus.tsx`
  - `frontend/src/components/views/InterviewLiveView.tsx`
  - `frontend/src/app/page.tsx`
- **Created**:
  - `frontend/src/lib/companionHealth.ts`
  - `frontend/src/lib/companionHealth.test.ts`
  - `docs/builder/task-05a-report.md`

## 5. Invariants Preserved Checklist
- [x] Zero Git commands executed throughout the session.
- [x] No protected untracked files modified (`AGENTS.md`, `frontend/AGENTS.md`, `frontend/CLAUDE.md`).
- [x] Exact file plan adhered to without touching any extra file.
- [x] All UI strings in Brazilian Portuguese (pt-BR).
- [x] No trailing whitespace or empty lines at end of files.
- [x] Backend STT pipeline, sequence dedup, gap forwarding, and auth remain intact.

## 6. Fix Round 1 Verification (`docs/builder/task-05a-fixes.md`)
- **Finding 1 — Overlapping Owner State Preservation**:
  - Updated `stall_watchdog` in `backend/main.py` so a never-producing connection's expired deadline does not clobber merged source health or raise alerts when `source_connections[source] > 1` and the merged source state is currently `healthy`.
  - Added regression test `test_never_produced_overlap_does_not_clobber_healthy_owner` in `backend/tests/test_native_stream_endpoint.py` verifying that overlapping non-producing connections cannot alarm, degrade source health, or log warnings while a healthy owner is live.
- **Finding 2 — Warning Placement Below Badges**:
  - Restructured `frontend/src/components/CaptureSourceStatus.tsx` with an outer column container (`display: "inline-flex"`, `flexDirection: "column"`), an inner row wrapping the source badges, and the alert banner placed directly below the badge row.
- **Finding 3 — Whitespace Cleanliness**:
  - Removed trailing blank line at EOF in `backend/tests/test_native_stream_endpoint.py`.
  - Audited all 10 modified and created files with non-git verification: confirmed zero trailing whitespace and exactly one trailing newline at EOF across every file.

### Verification Commands & Results (Fix Round 1)
```
.venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q
....................................                                     [100%]
36 passed in 2.84s

.venv/bin/python -m pytest backend/tests -q
........................................................................ [ 23%]
........................................................................ [ 47%]
........................................................................ [ 71%]
........................................................................ [ 95%]
.............                                                            [100%]
301 passed in 4.42s

npm test (in frontend/)
1..62
# tests 62
# suites 0
# pass 62
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 191.076333

npm run build (in frontend/)
▲ Next.js 16.3.0 (webpack)
- Environments: .env.local
✓ Running next.config.ts took 15ms

  Creating an optimized production build ...
✓ Compiled successfully in 1910ms
  Running TypeScript ...
  Finished TypeScript in 971ms ...
  Collecting page data using 4 workers ...
  Generating static pages using 4 workers (0/3) ...
✓ Generating static pages using 4 workers (3/3) in 370ms
  Finalizing page optimization ...
  Collecting build traces ...

Route (app)
┌ ○ /
└ ○ /_not-found


○  (Static)  prerendered as static content
```

Task 05a implementation complete. Ready for designer review.
