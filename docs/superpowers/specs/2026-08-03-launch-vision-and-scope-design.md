# T.A.R.S. Launch Vision and Scope — Design

**Date:** 2026-08-03

**Status:** Approved scope pending owner review of this document. Supersedes the delivery sequencing in `docs/plans/2026-07-15-native-companion-and-note-first-interviews.md` §"Phases 2–5" for the launch window only; the governing plan's product vision and target architecture remain authoritative for the commercial track.

**Inputs:** Six-expert independent panel (product strategy, Windows platform, systems architecture, privacy/compliance, UX, delivery skepticism) plus an adversarial moderator synthesis that re-verified load-bearing claims against the repo. Full reports: `docs/superpowers/reviews/2026-08-03-launch-scope-panel/`.

**Owner decisions incorporated (Deli Matsuo, 2026-08-03):**

| # | Decision | Choice |
|---|----------|--------|
| 1 | Launch audience | Ella-internal, commercial-ready architecture |
| 2 | Timeline | 4–6 weeks |
| 3 | Product modes | Interview copilot only (no meeting-notes mode) |
| 4 | Windows | Week-1 evidence gate (see §5), not decided blind |
| 5 | AI ratings in reports | Keep, hard-gated behind mandatory human review and explicit approval |
| 6 | Candidate disclosure | Transparent copilot: notice before recording, verbal confirmation, no-recording fallback |
| 7 | Legacy 16 sessions + 4 PDFs | Delete before the first week-1 dogfood interview |

---

## 1. Vision

T.A.R.S. is an executive-search interview companion for Ella. It captures any interview — Meet, Zoom, Teams, phone, in person — with no meeting bot, transcribes it live in Brazilian Portuguese with reliable self/remote speaker attribution, lets the recruiter capture judgment with one tap mid-conversation, and produces the artifact the business actually sells: **an assessment report whose every material conclusion traces to transcript evidence or recruiter judgment, reviewed and explicitly approved by a human before anyone sees it.**

The competitive scan (panel report 1) confirmed the wedge is real and unoccupied: no product combines botless capture + pt-BR-first + evidence-linked human-approved assessment for retained search. It also confirmed the window is narrowing (Metaview marketing to executive search; BrightHire acquired by Zoom, Dec 2025). Live suggestion generation is the *least* differentiated capability in the market; the report loop is the differentiator.

**Launch defined:** Ella recruiters run real interviews with T.A.R.S. and deliver client-ready reports from it, on their own machines, lawfully, within 6 weeks.

## 2. What launch delivers (the wedge loop)

Prepare from CV+JD (exists) → capture botlessly with source-labeled speakers (exists, macOS; Windows gated §5) → glanceable live screen with hero question (exists, PR #3 — frozen) → one-tap segment-anchored notes (build) → same-day draft report reviewed, edited, and approved by the recruiter, exported as a client-presentable PDF (build on existing backend report).

Explicitly **not** in the launch loop: live competency-coverage tracking (its evidence-binding intelligence layer does not exist anywhere in the repo — the lab consumes hand-authored fixtures), the four-panel integrated workspace, provider adapters, the native companion, and any hosted deployment.

## 3. Launch scope

Budget assumption: ~7 landed agent-weeks across 6 calendar weeks (one part-time owner reviewing AI-agent output). Sequencing is dogfood-first: a real interview happens in week 1 and every week thereafter; findings from real use outrank planned work.

### Must — week by week

**Week 1 — make real interviews safe and lawful:**
- Fix STT rotation audio loss: implement the documented A/B overlap (or buffer-and-flush) in `backend/stt/stream_manager.py` (docstring promises overlap at :19–28; `_rotate_stream` hard-stops at :121–125; `send_audio` silently drops at :97–100; `stt_stream_overlap_seconds` exists unused in `backend/config.py:66–68`). Soak-test 90 minutes.
- FLAC off by default: bounded in-memory buffer; opt-in dev flag only (`backend/audio/buffer.py:51–53` unconditional open, :76–81 synchronous per-chunk write on the event loop — this is a performance fix and a privacy fix in one).
- Remove `torch`/`torchaudio`/`silero-vad` (`requirements.txt:14–16`) — pinned for a VAD not wired into the live pipeline (only `backend/config.py` and orphaned `backend/audio/vad.py` reference it). Shrinks every install by gigabytes.
- Privacy launch bar, part 1: delete the 16 legacy sessions + 4 PDFs (Firestore + GCS); working deletion cascade for new sessions — one path keyed by session that removes Firestore records, GCS uploads, and any audio, manual runbook acceptable at launch; candidate disclosure script (pre-interview notice + verbal confirmation captured in transcript + no-recording fallback + notice-given flag on session); documented legítimo-interesse balancing test (LIA); verify prod GCP project has Vertex caching + abuse-logging opt-outs and STT data logging off; verify Ella's Google agreement incorporates the CDPA + Brazil SCCs.
- **Real 2-person interview on macOS by Friday** — first end-to-end verification of the dual-stream pipeline with a real second speaker, ever. Pre-flight: system output routed to the Multi-Output Device, BlackHole signal confirmed via level meter.
- Windows VB-CABLE spike on the named Windows recruiter's actual machine (§5) → **owner gate decision**.

**Week 2 — resilience + notes:**
- Reconnect fix: send `session_state` when replay gap exceeds the buffer (`backend/ws/handler.py:76–85` returns with only a comment; no caller sends it — verified repo-wide).
- Firestore-backed session read/regenerate path: `get_session`/`get_transcript`/report generation currently read only the in-memory manager (`backend/main.py:664–678, 392`), so any backend restart strands all past sessions despite transcripts being persisted per segment (`backend/main.py:256`). This one path is simultaneously crash recovery, app-update survival, and review-of-past-interviews. The report screen (W3) depends on it.
- Live note chips: `Marcar` / `Preocupação` / `Ponto forte` / `Retomar` — one tap, wordless, stamped with current transcript segment id and offset. Plumbing is a plain REST endpoint + Firestore persistence using the note-sync lab's `NoteKind` vocabulary (`note | bookmark | concern | strength | follow_up`) so data migrates cleanly to the full contract later. The lab's 680-line offline-sync reducer is explicitly NOT integrated at launch.
- Standing dogfood fix reserve (half-week, every week).

**Week 3 — the money artifact:**
- **Two artifacts, per the real Ella deliverable** (owner supplied a sample 2026-08-03; anonymized structure in `docs/product/2026-08-03-ella-client-report-template.md` — the original contains candidate PII and is not committed): (1) an **internal assessment view** in the review screen, where AI draft aids live; (2) a **client-facing export** that clones Ella's actual format — dense two-block pt-BR narrative (Trajetória + Avaliação in the consultant's first-person voice), with NO ratings, NO rubric, and a *process* recommendation naming next steps. The existing `INTERVIEW_REPORT_PROMPT` rubric output (`backend/llm/interview_prompts.py:74–139`) does not match Ella's deliverable and is restructured for the export path; recruiter note chips (concern/strength) feed the Avaliação's attention-points and limits movements directly.
- Report review screen, Firestore-backed: draft rendered as section cards with recruiter notes alongside; edit per section; explicit **Aprovar relatório** action pins the approved version.
- **AI-ratings hard gate (launch-blocking, owner decision #5):** the existing prompt emits 1–5 competency ratings and a Recomendado/Não Recomendado hire recommendation (`interview_prompts.py:101–135`) with no gate — verified. At launch: any ratings/draft aids render only inside the internal review view, clearly labeled "Rascunho gerado por IA", and never appear in the client-facing export. No auto-generated report leaves the app unapproved. This is the LGPD Art. 20 posture.
- Print-CSS PDF export of the client-facing narrative (replaces `.txt` download, `frontend/src/components/SummaryPanel.tsx:38`); separate transcript and notes exports retained.
- Stale-session sweep (10 stale "active" Firestore sessions; orphan detection at `backend/main.py:102–105` only logs).

**Week 4 — second user:**
- Google sign-in (scaffold exists, `frontend/src/lib/firebase.ts`) → backend ID-token verification → stamp `owner_id` **and `org_id`** on every session/note/report record and storage path (commercial-ready tenancy from day one; retrofitting is the classic disaster) → list/get filtering.
- Setup script + per-machine checklist (GCP credentials, audio routing, disclosure script).
- Documented shared-trust statement: every internal machine runs the Admin SDK with a service account that bypasses Firestore rules; app-level auth is user attribution, not a security boundary, until the hosted plane exists. Accepted for the internal period by owner decision; revisit at commercial.
- First non-owner interview.

**Week 5 — Windows landing (if GO at gate) or hardening:**
- GO: VB-CABLE capture path, packaging (no signing — SmartScreen "Run anyway" is acceptable internally), per-machine setup checklist, headset mandated, 2-hour real-call soak.
- NO-GO: the week goes to hardening and dogfood findings; optionally, harvest of the coverage projector behind the existing context-upload endpoint — only if dogfood evidence demands it, owner's call.

**Week 6 — buffer and formalization:**
- Fix-reserve (will be consumed; plan on it).
- One-page privacy launch checklist (FLAC off, deletion cascade works incl. GCS, retention TTL enforced, disclosure flow live, ZDR opt-outs verified) — checked before first non-owner user if not already done.
- ADR 0001 amendment: Ella-internal launch waiver naming which guardrails remain binding (1, 3, 5, 6, 8) and which are explicitly relaxed (1B–1D sequencing, hosted attestation, deployment de-authorization for local-only launch). New ADR 0003 recording the launch architecture and its migration path.

### Should (fast-follow, not launch)

- Document Picture-in-Picture "modo compacto" (hero + chips, always-on-top; Chrome/Edge 116+) with screen-share safety: tab-share-only policy, panic-hide, neutral window title.
- DOCX export on Ella's client template (owner supplies a sample report — open question #3).
- Protocol-0002-shaped frames at the internal capture→STT seam (pre-cuts the native-companion split; architect-sponsored; do early if cheap during W1 rotation work).
- PyAudioWPatch (virtual-device-free Windows capture) or thin C#/NAudio companion.
- Coverage projector integration behind the existing context-upload endpoint, once an evidence-binding layer exists and is quality-tested in pt-BR.
- Note-sync full contract (offline sync, multi-device); audit log v1 (append-only: actor, org, action, object, purpose, timestamp); breach-response runbook; named encarregado (DPO); full RIPD document (draft exists at launch).
- southamerica-east1 for Vertex (data residency). Note: chirp_3 STT runs only in `us`/`eu` (`backend/config.py:53–55`) — Brazil STT residency is impossible today; the transfer mechanism is required regardless.
- Cost measurement from first dogfood interview: two continuous STT streams ≈ 180 STT-minutes per 90-minute interview; suggestion pipeline re-sends full CV+JD+briefing every 5th final segment with caching off (ZDR requirement). Restructure prompts if ugly.
- Per-search telemetry (owner decision: commercial model is **per-search**): sessions carry a search/mandate identifier from creation; log session counts, durations, and reports produced per search. The identifier field lands with the W4 ownership stamping; dashboards are post-launch.

### Cut from launch (explicit)

Integrated-recruiter-workspace UI wholesale (49.5k-line parallel UI competing with PR #3 — the labs are a parts bin of contracts and reducers, not pending features); assessment-provenance machinery (blocked on nonexistent evidence binding); session-lifecycle lab (fights the existing session manager); Chrome Meet extension (live-tested broken 2026-08-01, 7/8 selectors dead — dual-stream source labels are the baseline; extension officially dormant pending owner decision); native companions at launch (Phase-1C Swift track continues in its worktree, unaffected); hosted gateway/cloud plane; per-process loopback; code signing; CMEK; consent-management platform; candidate self-service portal; ATS/CRM integrations; English UI; billing; voiceprint/diarization features (see §6 no-biometrics rule).

## 4. Architecture posture at launch

Ship the existing Python FastAPI application as a local app per machine. This is a **formal interim** (record in ADR 0003), not a reversal of ADR 0001: Firestore/GCS/Vertex already constitute the cloud data plane; only capture+orchestration are local. The future split is pre-cut by emitting 0002-shaped frames at the internal capture→STT boundary (Should). ADR 0001's rejection of Python-as-local-app remains correct long-term. `gateway/phase1b/` (empty) is not built at launch. GCP service credentials on each hand-configured Ella machine are accepted for the internal period (owner decision W4) and are disqualifying for commercial — the hosted plane is the commercial-track successor.

## 5. The Windows week-1 gate

Owner requirement: Windows at launch. Panel conflict: feasible in 1–2 weeks (Windows engineer) vs. cut — packaging on non-engineer machines is the real cost (skeptic). Moderator verification: both mechanisms check out; VB-CABLE works with **zero code changes** (`backend/audio/capture.py:17–42` opens any named PortAudio input device; misroute failure class already instrumented by startup silence detection at :139–163 and the single-source warning in `backend/main.py:320–340`); PyAudioWPatch is the code-change fallback; torch removal (W1) deletes the heaviest install step.

**Gate procedure (end of week 1):** (1) name the actual Windows recruiter in the first cohort and their physical machine — if none exists, the decision defaults to macOS-first at zero cost; (2) run the VB-CABLE spike on that machine: install Python stack, route meeting audio through VB-CABLE with "Listen to this device" for recruiter playback, capture a 30-minute real call, verify both channels label correctly; (3) decide:

- **Option A — Windows at launch:** spike clean → W5 lands packaging + checklist. Cost ≈ 1.5–2 of ~7 agent-weeks; squeeze absorbed by report/notes polish.
- **Option B — macOS launch W4 + Windows fast-follow with a committed date (~3 weeks post-launch).** Default if the spike is dirty or no real Windows user exists in the cohort.

## 6. Privacy launch bar (event-based, verified against panel research)

Blockers bind at the **first real-candidate interview** (week 1), not at an arbitrary date:
FLAC default-off · disclosure flow live (decision #6) · working deletion cascade across Firestore + GCS (uploaded CVs) + any audio, manual runbook acceptable · legacy 16+4 deleted (decision #7) · ZDR opt-outs verified in prod project · LIA documented · AI report hard gate (decision #5) before any report is exported or any colleague uses the product.

Standing design rules: **no voiceprint/biometric speaker identification, ever** — stream-routing attribution is what keeps T.A.R.S. outside LGPD sensitive-data processing (codify in ADR 0003); legítimo interesse is the legal basis for in-search processing, consent only for talent-pool retention/reuse; recruiter guidance not to elicit sensitive topics.

**Retention policy (adopted 2026-08-03 from cited research — `docs/superpowers/reviews/2026-08-03-launch-scope-panel/8-lgpd-retention-controller-research.md`):**

| Artifact | Retention | Legal anchor |
|---|---|---|
| Interview transcripts | Delete **90 days** after report delivery (TTL-enforced) | Art. 15, I — necessity ends at report production |
| Recruiter notes | Restricted archive at search close; delete at **2 years** | CNIL active-phase analogy; bienal prescription |
| Delivered assessment reports | Restricted archive (DPO-only access); delete at **5 years** from search close | Art. 7, VI defense of claims; CNIL 5-year intermediate archive |
| CVs | Delete at search close, unless candidate opts into talent pool by specific consent (**2 years, renewable on contact**) | Migalhas/CNIL practice; talent pool = separate processing |

At expiry: **delete, don't pseudo-anonymize** (Art. 16, IV permits only truly anonymized data; a de-named transcript does not qualify). Launch implements the transcript TTL; the archive tiers land with the audit log (Should).

**Controller allocation (adopted 2026-08-03, same research):** Ella = controller for search execution (interviews, transcripts, notes, reports, talent pool); client = separate independent controller of the report upon receipt — not joint (ANPD Guia ¶45; WEC guidelines p. 13). Never share candidate-pool systems with clients (that fact pattern creates controladoria conjunta and solidary liability). The pt-BR client-contract clause is drafted in the research doc. Encarregado (DPO): **Deli Matsuo**.

Compliance analysis is not legal advice; the LIA and controller-structure conclusions should be validated with Brazilian counsel before commercial launch (internal launch proceeds on documented good-faith posture per owner decision).

## 7. Verified defect register (all confirmed by direct code inspection)

| Defect | Location | Fix window |
|---|---|---|
| STT rotation silently drops audio every ~270s | `backend/stt/stream_manager.py:97–100,121–125`; `backend/config.py:62–68` | W1 |
| FLAC always written; sync write on event loop | `backend/audio/buffer.py:51–53,76–81` | W1 |
| torch/torchaudio/silero-vad pinned, unwired | `requirements.txt:14–16`; only `backend/config.py` + orphaned `backend/audio/vad.py` | W1 |
| `session_state` never sent on large replay gap | `backend/ws/handler.py:76–85`; no caller in `backend/main.py:915–943` | W2 |
| Sessions unreachable after backend restart | `backend/main.py:663–678,392` read memory only; segments persisted at `:256` | W2 |
| AI hire recommendation ungated | `backend/llm/interview_prompts.py:101–135`; generated on stop at `backend/main.py:512` | W3 (gate) |
| 10 stale "active" Firestore sessions | orphan detection logs only, `backend/main.py:102–105` | W3 |
| No auth/ownership | `backend/main.py` REST + `/ws/{session_id}` | W4 |

## 8. Open questions — status after owner answers (2026-08-03)

1. **Ella client report sample** — ✅ RESOLVED. Sample supplied; anonymized structure committed as `docs/product/2026-08-03-ella-client-report-template.md` (original withheld from repo — candidate PII). Drives the W3 two-artifact design (§3).
2. **Windows recruiter + machine named** — ✅ PARTIALLY RESOLVED (owner, 2026-08-03): **several recruiters in the cohort use Windows.** The "macOS-first wins by default" branch is off the table — Windows demand is real. The §5 gate still runs, but it now decides *how and when* Windows lands (spike-quality → Option A at launch vs Option B dated fast-follow), not whether it matters. Remaining W1 logistics: pick any one Windows recruiter's machine for the spike.
3. **Retention period** — ✅ RESOLVED by research (owner: "use regulation, best practices"): per-artifact policy adopted in §6 — transcripts 90d TTL, notes 2y archived, reports 5y DPO-only archive, CVs deleted at search close unless talent-pool consent. Sources cited in panel doc #8.
4. **Screen-share policy** — ⏳ STILL OPEN, explained: this is an operational rule, not a feature. The planned fast-follow "modo compacto" floating window (§Should) stays on top of the meeting; if a recruiter shares their **entire screen**, the candidate sees the copilot. Mitigation is procedural — recruiters share only a tab/window. The question is whether Ella mandates that in onboarding. *Default: yes.*
5. **Encarregado (DPO)** — ✅ RESOLVED: **Deli Matsuo, DPO** (named, not interim).
6. **Controller structure with clients** — ✅ RESOLVED by research (owner: "use market practice, research"): independent/singular controllers — Ella for the search, client for the received report; not joint, not operador. Allocation + pt-BR contract clause in §6 and panel doc #8.
7. **Meet extension** — ✅ RESOLVED: officially dormant; revisit only with evidence participant naming materially improves outcomes (governing plan §5.4).
8. **Commercial model** — ✅ RESOLVED: **per-search**. Telemetry consequence recorded in §Should (search/mandate identifier on sessions from W4).

## 9. Success criteria

Launch is successful when: (1) ≥2 Ella recruiters (owner + one colleague) have each run ≥1 real candidate interview end-to-end; (2) a client-ready approved PDF report was produced same-day from a real interview; (3) zero interviews lost audio to rotation, crash, or reconnect; (4) the privacy checklist passes; (5) every report that left the app was explicitly human-approved.
