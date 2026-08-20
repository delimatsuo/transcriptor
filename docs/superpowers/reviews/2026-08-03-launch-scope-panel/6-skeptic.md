# Staff Engineer / Scope Skeptic — T.A.R.S. Launch Scoping

Architecture fact that frames everything: **the entire product runs on the recruiter's Mac.** Capture is backend-side `sounddevice` reading mic + BlackHole (`backend/audio/capture.py`), so the FastAPI backend, Next.js frontend, and Google credentials all live on the local machine; cloud deploy is de-authorized anyway (README). "Launch" therefore means "other Ella recruiters can run this stack," which is a packaging/ops problem as much as a feature problem.

## 1. Delivery math

Capacity assumption: one part-time owner reviewing AI-agent output. Session history shows heavy per-commit ceremony; realistically **~1–1.5 landed agent-weeks per calendar week**, so a 6-week window buys **~6–9 agent-weeks total**. That is the entire budget.

| Item | Estimate | Basis / assumptions |
|---|---|---|
| STT rotation audio gap | 2–3 days | `backend/stt/stream_manager.py`: docstring promises open-B-before-closing-A overlap; implementation is stop-then-recreate. `send_audio()` (line 97) silently drops while `is_active` is false. Fix: queue audio during handoff, flush into the new stream; add a rotation test + one real >5-min run. |
| Reconnect session_state | 1–2 days | `backend/ws/handler.py:76–85`: on gap > 1000 msgs, `_replay_messages` returns with a comment saying "caller should send session_state" — no caller does. Return a flag from `connect()`, build snapshot from session manager, send it. |
| FLAC off-by-default + bounded buffer | 2–3 days | `backend/audio/buffer.py:79`: synchronous `sf.SoundFile.write` per chunk on the event loop, two streams. Making backup **opt-in default-off** with a bounded deque fixes the blocking risk *and* delivers the no-persistent-audio privacy property in one move. |
| Stale Firestore sessions | 1–2 days | TTL/status sweep in `backend/storage/firestore.py` + `sessions/manager.py`. |
| Minimum auth | ~1 week | Frontend already scaffolds Firebase Auth (`frontend/src/lib/firebase.ts`); Firestore rules are deny-all, all data via Admin SDK. Google sign-in → ID token → backend verifies → stamps `user_id` on sessions, filters `list_sessions`/`get_session` (`backend/main.py:656–674`). No orgs, no cross-tenant hardening — internal trust. Only needed once a second person runs the stack. |
| Lab integration (each) | 1–2 weeks **each** | Critical finding: the labs are **frontend-only simulations**. E.g., integrated-recruiter-workspace-lab is ~14.5k frontend insertions: contract + reducer + fixtures + a standalone Lab component on a `synthetic-*` route (`frontend/src/note-sync/reducer.ts` 680 lines, `simulated-port.ts`, etc.). Zero backend. "Integration" means: new backend APIs + persistence, adapters from live WS messages to contract events, and re-skinning into the just-redesigned live screen (the Lab UIs predate PR #3). The reducers/contracts are harvestable; the UIs mostly aren't. |
| Post-session report + export | 1–1.5 weeks | Backend already has `_generate_final_summary`, `analyze_candidate`, transcript download (`backend/main.py:388, 577, 682`). V1 = summary + transcript + suggestions log rendered as a report page with markdown/PDF export. The assessment-provenance lab's evidence-linking is the commercial version — not needed to be useful. |
| Windows capture path | 1 wk (VB-CABLE parity) to 3+ wks (packaged) | See §3. |
| Live 2-person verification loop | ~0 code; 0.5 wk/week fix reserve | Calendar-bound, not effort-bound. Budget standing capacity for what it finds. |

Sum of the plausible "must" set (defects + FLAC-off + auth + notes + report + fix reserve) ≈ **6–7 agent-weeks** — the whole budget, before Windows or any lab lands wholesale.

## 2. The cut list and landing order

The forcing function is the owner interviewing with it **in week 1**, so sequence by "what makes a real interview trustworthy," not by feature value.

- **Week 1 — make real interviews safe to run:** rotation gap, reconnect snapshot, FLAC default-off, stale-session sweep. All four are small and they're precisely what makes a live interview not lie to you. **Owner runs a real interview by Friday of week 1.**
- **Week 2 — dumbest possible notes:** a timestamped textarea persisted per session (~2 days), *not* the 680-line note-sync reducer. Dogfood cadence: every real interview files defects; reserve half the week for them.
- **Week 3 — report v1 + export.** This is the artifact recruiters actually keep; it converts dogfooding into visible value. Ship it from existing backend endpoints.
- **Week 4 — auth + second user.** Google sign-in, per-user scoping, and a setup script so one colleague can run the stack. First non-owner interview.
- **Week 5 — coverage (Should):** harvest the interview-coverage projector behind the existing context-upload endpoint if dogfooding demands it; otherwise more hardening.
- **Week 6 — buffer.** It will be consumed by weeks 1–5 findings. Plan on it.

**Cut outright:** integrated-recruiter-workspace wholesale (a 49.5k-line parallel UI competing with the redesign that just landed — pick one; the live screen wins), session-lifecycle lab (fights the existing session manager), assessment-provenance machinery, note-sync reducer/offline sync, Chrome Meet extension (already dead, 7/8 selectors), native companion track, Windows-at-launch (below).

## 3. Windows, honestly

Not compatible with this window at this capacity. The capture code itself is nearly free — `capture.py` opens any named input device, so a VB-CABLE virtual device is the direct BlackHole analog (config + setup doc). But capture was never the cost. The cost is **running a local Python 3.12 + Node stack on a non-engineer's Windows machine**: dependency install, credential distribution, audio-device setup UX, and a test loop on hardware the owner may not own. That's 2–3+ agent-weeks of packaging and debugging — a third of the total budget — for zero learning about the product.

Least-bad path: **macOS launch week 3–4; Windows fast-follow with a committed date ~3 weeks post-launch**, via VB-CABLE (not WASAPI-loopback rework — sounddevice/PortAudio loopback support needs verification; defer to the Windows specialist), gated on identifying the actual Windows recruiter and a physical test machine first. If no recruiter in the first cohort runs Windows, this decision costs nothing.

## 4. Process right-sizing

**Keep (real harm protection):** no real candidate data until FLAC-default-off + a working delete endpoint land, then a written internal consent/retention note (LGPD matters — pt-BR candidates); deny-all client Firestore rules (`firestore.rules`) with all access via backend; disabled STT data logging; no public Cloud Run invoker (if anything is ever hosted, authenticated invoker/IAP only); the unresolved legacy 16-sessions/4-PDFs deletion question.

**Drop for the internal launch:** phase gates 1B–1D and their attestation/kill-switch/threat-model apparatus — they gate the *native companion + hosted* track, which is out of launch scope entirely; companion protocol conformance suites (not shipping the companion); digest-pinned per-commit staff-review ceremony (the session logs show multi-hour review stalls per small commit — that cadence alone can eat half the throughput budget).

**Minimum process:** green tests + owner PR review + a one-page privacy launch checklist (FLAC off, deletion works, retention job runs, consent script exists) checked once before the first non-owner user — a checklist, not a gate series.

## 5. Launch scope table

| Item | Verdict |
|---|---|
| 4 defect fixes + FLAC-off/bounded buffer | **Must** (week 1) |
| Real-interview dogfood loop | **Must** (week 1 onward) |
| Simple persisted notes | **Must** (week 2) |
| Report v1 + export | **Must** (week 3) |
| Google sign-in + per-user scoping | **Must** (week 4, gated on second user) |
| Coverage projector integration | **Should** (week 5, evidence-driven) |
| Note-sync reducer, session-lifecycle, provenance, workspace labs | **Cut** (harvest parts later) |
| Windows | **Cut from launch**; committed fast-follow |
| Meet extension, native companion, hosted deploy | **Cut** |

**Biggest timeline-blower warning:** treating "integrate the labs" as flipping switches. Every lab is a fixture-driven frontend simulation with **no backend and no live-pipeline wiring**; each real integration is 1–2 weeks of new construction. Two labs plus Windows would consume the entire 6-week budget by themselves.

**Explicit disagreements with the accepted plan:** (1) Windows-at-launch — no, fast-follow with a date; (2) lab work should be framed as a parts bin, not pending features; (3) the governance apparatus should be explicitly re-scoped to the commercial track, not partially complied with.

**Owner-only questions:** Who besides you runs it in week 4 — and on what OS? Is real candidate data authorized once FLAC-off + deletion land, and with what consent wording? Is 90-day retention (`backend/config.py:82`) acceptable if actually enforced? Is the Meet extension officially dead? What are the deletion obligations for the 16 legacy sessions and 4 PDFs?
