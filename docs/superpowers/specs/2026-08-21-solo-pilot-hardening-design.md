# Solo Pilot Hardening — Native Capture Spine (Design)

- **Date:** 2026-08-21
- **Owner:** Deli Matsuo
- **Authority basis:** Direct owner instruction in the 2026-08-21 architecture-brainstorm session: scope (a) — "you first, on this Mac, for real interviews ASAP" — Approach 1 approved verbatim, including the evidence-doc corrections. This spec grants **local solo-pilot work only**: no recruiter pilot, no packaging/distribution, no hosted deployment, no Windows capture claims.
- **Supersedes/relates:** Implements the hardening prerequisites for ADR 0003 (`docs/architecture/0003-native-capture-launch-boundary.md`) within its boundary. Does not reopen the capture-architecture decision (Option A stands; Chrome-extension capture remains enrichment-only; meeting bots remain a non-goal).

## 1. Problem

The 2026-08-20/21 PR wave (#16–#36) landed a real macOS native-capture path (ScreenCaptureKit system audio + AVAudioEngine mic → WebSocket gateway → Google STT), but a verified audit (2026-08-21) found the spine is not yet trustworthy for a real interview:

1. **The candidate channel has never produced a frame.** The only recorded run (`docs/launch/2026-08-21-macos-pilot-verification-evidence.md`) shows 0 system-audio frames. Since SCStream delivers audio buffers continuously once running, 0 frames most likely means capture never started (TCC/permission failure), not silence — unconfirmed either way. The CLI warns and keeps running when system-audio capture fails.
2. **The interviewer channel is captured twice.** The cockpit auto-streams the browser mic (`source="microphone"`, resampled correctly) on every session, and the companion CLI also captures/sends the mic (with a real defect: hardware-rate buffers labeled 16 kHz, no resampling). Two connections create two "Entrevistador" StreamManagers → duplicate transcripts, the same defect class PR #36 fixed for legacy host capture.
3. **The audio gateway is unauthenticated.** `/api/stream/native/{session_id}` (`backend/main.py:2333`) calls `websocket.accept()` unconditionally; the CLI's `--token` is ignored; any local process can open billable STT streams under any session-id string, and no session-existence check runs. The UI WebSocket, by contrast, requires a single-use ticket.
4. **A dropped WebSocket silently ends capture.** After one send error the companion marks the sink closed and discards every subsequent frame while the CLI keeps running — a mid-interview blip silently loses the rest of the session.
5. **The health UI is dead.** The cockpit's companion badges and coverage-gap timeline (PR #23) listen for `companion_health` / `coverage_gap` WS messages that no backend code emits, so the interviewer cannot see whether the candidate channel is alive.
6. **Launch/evidence documents on `main` are false.** The sign-off memo claims "G0–G8 satisfied / LAUNCH APPROVED" from mock-gateway harness runs; the Windows "pilot evidence" claims validation of WASAPI code that does not exist (verified: zero WASAPI interop in `companion/native-windows`; non-simulate `StartAsync` captures nothing); the packaging guide claims the exe bundles "native WASAPI interop".

## 2. Goal and success criteria

**Goal:** One real interview, conducted by the owner on this Mac, transcribed end-to-end through the native path, with honest documentation.

**Success criteria (all must hold):**

- S1. A scripted live proof exists and passes on this Mac: pt-BR system audio (macOS `say`) played while a session runs is transcribed and labeled **Candidato**; browser-mic (or injected mic-channel) speech is labeled **Entrevistador**; no duplicate segments.
- S2. Running cockpit + companion together cannot double-capture the interviewer: companion defaults to system-audio-only.
- S3. `/api/stream/native` rejects connections without a valid per-session stream key, and rejects unknown/inactive sessions. The companion authenticates with that key.
- S4. Killing the companion's WebSocket mid-stream (or restarting the companion) results in automatic reconnection and continued transcription; frames lost beyond the buffer window surface as a coverage gap, not silence.
- S5. The cockpit shows the system-audio channel as healthy while frames flow, degraded on stall/disconnect, and renders coverage gaps in the transcript timeline.
- S6. If system-audio capture cannot start (permission missing), the companion exits non-zero with a clear pt-BR message pointing at the exact System Settings pane — it never runs in a silently-dead state.
- S7. The false launch/evidence docs are corrected in place (retraction headers + accurate claims), the Windows companion is labeled scaffold-only everywhere user-facing, and the Windows CLI refuses to pretend: non-simulate mode exits non-zero stating capture is not implemented.

## 3. Design decisions

### D1. Channel split: browser owns self, companion owns candidate
The browser mic path (PR #35) is the canonical **Entrevistador** channel: it resamples correctly, has the device selector + VU meter, and echo-cancels. The companion becomes **system-audio-only by default** (`--sources system_audio`; `both`/`microphone` remain available behind the flag for future headless use). The AVAudioEngine mic source stays in the codebase but is off the critical path; its missing-resampling defect is documented in code and out of scope to fix here.

### D2. Gateway auth: per-session stream key (not single-use tickets)
Single-use tickets (the UI WS mechanism) break automatic reconnection — every retry would need a fresh authenticated mint. Instead: session creation generates a random `stream_key` (secrets.token_urlsafe, stored on the session record, returned to the authenticated cockpit client only). Audio WS connections must present it (`?stream_key=` query param on the WS URL, replacing the ignored `--token`); the endpoint validates key + session existence + session ACTIVE state before `accept()` completes, closing with policy-violation code otherwise. The cockpit displays a copy-ready companion launch command including `--session-id` and `--stream-key`. The browser mic capture hook sends the same key. Key dies with the session. Rationale: reconnect-friendly, session-scoped, revocable, and strictly better than today's nothing; full protocol-0002 enrollment stays out of scope.

### D3. Reconnect: bounded buffer + backoff + honest gaps
Companion sink keeps a bounded FIFO of encoded frames (target ~30 s). On send failure/close: keep capturing into the buffer, reconnect with exponential backoff (1 s → 30 s cap, jittered, indefinitely until stopped), replay buffered frames on resume (existing `sequence`/`first_sample` header fields make this safe server-side), and emit a `gap` message for any frames dropped on overflow. Reuse `CustodyRing` if it fits with minor adaptation; otherwise a minimal ring — builder's call, decided by code fit, not by preserving unwired machinery.

Server side: StreamManagers for a session must survive a companion reconnect. Get-or-create per (session, source) against the module-level registry instead of per-connection instances; a disconnect stops feeding but does not stop/pop the managers immediately — cleanup happens on session stop (existing `_stop_pipeline` path) with an idle-timeout safety net. The `finally` block must not destroy another connection's managers (fixes the latent registry-clobber the audit found).

### D4. Health loop: emit what the frontend already expects
Backend emits `companion_health` (shape: existing `SourceHealthReport` in `frontend/src/types/ws.ts`) over the session UI WebSocket on: audio-WS connect/disconnect, first frame per source, and stall (no frames for >10 s while connected → `device_unavailable`; permission-style gap reasons map to `permission_denied`). Backend rebroadcasts companion `gap` messages as `coverage_gap` (add to `WSMessageType`). Frontend changes limited to whatever's needed to make the existing badges/timeline actually render with real data. The stale BlackHole wording in the single-source watchdog (`backend/main.py:778-802`) and the stale BlackHole comment in `frontend/src/lib/transcript.ts` get updated as drive-by copy fixes.

### D5. Permission preflight: fail loud, in Portuguese
Companion startup: preflight screen-capture permission (`CGPreflightScreenCaptureAccess()`; if unavailable to the SwiftPM CLI, probe by attempting `SCShareableContent` fetch and treating failure as denial). On missing permission: print pt-BR instructions naming the exact pane (Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema, granting the **hosting terminal app**) and exit non-zero. Additionally, if the stream starts but zero system-audio frames arrive within 15 s, print a loud warning and emit a gap/health signal (distinguishes "running but dead" from "healthy but silent" as best SCK allows).

### D6. Evidence honesty rules
Every evidence document this work produces or touches states: what ran, on which machine, against which commit, and an explicit claim ceiling. The two existing "pilot verification" docs and the sign-off memo get retraction/correction headers, not deletion (history preserved). `scripts/verify_e2e_pilot.py` / `verify_windows_e2e_pilot.py` docstrings are re-labeled wire-format harnesses. The new live proof (S1) is `scripts/verify_live_system_audio.py` and its output doc claims exactly: "native capture spine verified live on the owner's machine — solo-pilot scope only."

## 4. Scope

**In:** the seven workstreams above (split-channel default, live candidate-channel proof, stream-key auth, reconnect+gaps, health loop, permission preflight, docs corrections + Windows CLI honesty), plus tests for each behavior change (backend pytest; Swift tests for flag parsing and sink reconnect state machine with a mock transport; .NET test for the non-simulate refusal).

**Out (explicit):** menu-bar app; Developer ID signing/notarization; CoreAudio process-tap migration (macOS 14.4+ permission UX — next phase, when recruiters install); real Windows WASAPI implementation; mounting `backend/g3a_gateway`/protocol-0002 envelope; hosted deployment (remains blocked by its own gate); G3C consent-UX reconciliation; Chrome-extension work; fixing companion-mic resampling (documented, dormant).

## 5. Risks & dependencies

- **TCC grant needs a human click.** The terminal app hosting the builder/CLI must hold Screen Recording permission. If not yet granted, the live proof pauses until the owner clicks — surfaced with a one-line instruction, not silently skipped. (S6 makes this state loud forever after.)
- **Google ADC expires daily** (`invalid_rapt`). Verified valid at spec time; the live-proof script checks ADC by exit code first (never printing tokens) and reports expiry as its own failure mode.
- **`say` voice availability:** pt-BR voices Eddy/Flo confirmed present on this Mac; script falls back to any available voice and loosens the content assertion accordingly (frame-count + any-transcript-text minimum).
- **Reconnect semantics vs. STT stream rotation:** replayed frames interact with StreamManager's rotation/dedup logic; the existing `sequence`/`first_sample` anchoring is designed for this, but S4's test must cover a reconnect across a rotation boundary.
- **Protected files:** `AGENTS.md`, `frontend/AGENTS.md`, `frontend/CLAUDE.md` (untracked) must not be staged, modified, or deleted. All commits use explicit pathspecs; never `git add -A`.

## 6. Verification protocol

1. Full backend suite (`.venv`) green; frontend unit tests green; Swift package tests green; .NET tests green.
2. `scripts/verify_live_system_audio.py` passes on this Mac end-to-end (real backend, real companion, real `say` audio, real Google STT), producing an evidence doc per D6.
3. Manual-equivalent checks scripted where possible: kill -9 the companion mid-stream and confirm reconnect + gap rendering; wrong/missing stream key rejected; unknown session rejected.
4. The reviewer (session supervisor) re-runs the live proof independently before merge.
