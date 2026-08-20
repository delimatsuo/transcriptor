# Systems Architect — T.A.R.S. Launch Scoping Report

## 1. Reality-checking the gated plan

The gate system (ADR 0001 "Approval boundary"; `docs/plans/2026-07-15-native-companion-and-note-first-interviews.md` §10) was sized for public-cloud exposure risk: anonymous endpoints, external pilots, real-candidate data leaking through an unhardened gateway. An Ella-internal launch on a handful of hand-configured machines removes the exposure vector but not the data-sensitivity vector — real candidates will be recorded from day one.

**Keep (protects candidates/data):** Guardrail 1 (no raw-audio files by default — currently violated: `backend/audio/buffer.py:30-43` unconditionally writes FLAC to `recordings/`), Guardrail 3 (no unauthenticated endpoints reachable off-machine — `backend/main.py` has zero auth on REST and `/ws/{session_id}`), Guardrail 5 (verify STT data-logging / Vertex retention before privacy claims — already done for the isolated project per §10 Phase 0B, must be re-verified for the production GCP project), Guardrails 6 and 8 (capture indicator; human approval of assessments).

**Relax by explicit owner decision:** the 1B→1C→1D sequencing as a launch precondition; hosted attestation and fixture-manifest-only traffic; the "separate authorization per phase" ceremony; the deployment de-authorization. These were designed to prevent accidental public exposure during R&D, not to govern an internal tool the owner is deliberately shipping.

**Artifacts to update (else docs and reality diverge permanently):** (1) amend ADR 0001 status + approval boundary with an "Ella-internal launch waiver" naming exactly which guardrails remain binding; (2) a new ADR 0003 recording the launch architecture chosen below and its migration path; (3) status headers of both plan docs (`2026-07-15-*.md`) marking 1B–1D as post-launch tracks, not blockers; (4) note that the Phase-1C worktree (`Transcriptor-worktrees/offline-companion-qualification`) continues as the companion replacement track, unaffected.

## 2. Launch architecture recommendation

Reading the code changes the picture: **option (a) is not actually "local-first."** Firestore, GCS, and Vertex are already the cloud data/intelligence plane (`backend/storage/`, `backend/llm/`); only capture+orchestration run locally. The split the ADR wants already half-exists at the data layer.

- **(a) Python monolith locally on both platforms.** Feasible: `backend/audio/capture.py` is sounddevice/PortAudio and device-name-agnostic; the Windows delta is a loopback device path plus packaging. Cost: weeks 1–2 for capture+packaging, leaving time for defects and product surfaces. Violations: no-persistent-audio (fixable, see §4), no auth/ownership (add minimal bearer auth + `owner` on sessions), and GCP service credentials on every machine (Guardrail 2 — tolerable for a hand-configured handful, disqualifying commercially).
- **(b) Split now per ADR.** `gateway/phase1b/` is an **empty directory**; `companion/protocol/` is a conformance kit (schema, vectors, simulator, guard) with no transport server. Building auth, enrollment, session ownership, a streaming gateway, and server-side STT lifecycle is realistically 3–4 of the 4–6 weeks, on two platforms' capture besides. Nothing left for defects or product. Reject.
- **(c) Hybrid — Python capture speaking 0002 to a hosted plane.** Requires the same nonexistent gateway as (b) plus retrofitting 0002 framing onto the Python pipeline. Worst of both. Reject.

**Recommend (a) with corner-proofing:** ship the monolith locally, but (i) make the capture→STT boundary inside the process emit protocol-0002-shaped frames, reusing `companion/protocol/python/tars_phase1a/model.py` — the future split then becomes transport-only, and the Phase-1C companion replaces the capture half per-machine without touching the plane; (ii) FLAC off by default; (iii) minimal shared-secret auth on REST/WS and an owner field in Firestore, since the data plane is already cloud-shared. Migration cost to the ADR target stays honest: the seam is pre-cut, and ADR 0001's rejection of "Python as local app" remains correct *long-term* — record it as an explicit interim in ADR 0003.

## 3. Lab integration cost

`build/worktrees/integrated-recruiter-workspace-lab` is +49.5k insertions / 156 files, but roughly half is duplicated companion Swift experiments; the product core is ~4k lines of contracts/reducers/projectors in `frontend/src/{interview-coverage,note-sync,assessment-provenance,session-lifecycle,recruiter-workspace}` plus ~4.2k lines of tests. The contracts are genuinely good. The costs hide in three seams:

1. **Evidence binding is fixture-authored, not computed.** The coverage projector consumes a `SyntheticJDCoverageOracle` whose `bindings` (`reviewedExplorationEvidence`, `reviewedSufficientEvidenceSets`, `reviewedContradictions`) and `questions` are hand-written (`frontend/src/interview-coverage/contract.ts`). The live system needs the stage that *produces* bindings from real `TranscriptSegment`s + a JD-derived competency model — an LLM classification pipeline that does not exist anywhere. That is the single largest unbuilt component (1.5–2+ weeks alone, with quality risk in pt-BR).
2. **Note-sync's port is simulated.** `frontend/src/note-sync/simulated-port.ts` implements the idempotent mutation ledger in-memory; the backend has zero note endpoints (`backend/main.py`). Wiring = REST endpoints + Firestore persistence honoring `clientMutationId`/fingerprint semantics + auth context. ~1 week; contracts make this the cheapest, highest-value lab to land.
3. **Evidence references need stable transcript identity.** `AssessmentEvidenceReference{sourceType, sourceId, sourceVersion}` (`assessment-provenance/contract.ts:33-37`) presumes versioned, stable segment IDs; the live pipeline re-emits and relabels segments (speaker overrides in `main.py:242-247,815-837`) with no versioning. Plus the lab UIs are standalone pages (`RecruiterWorkspaceLab.tsx`), not the just-redesigned live screens (PR #3) — integration is a redesign task, not a copy.

Honest estimate: notes+report into the live app ≈ 1.5–2 weeks; coverage ≈ 2–3 weeks *because of the missing intelligence layer*, not the UI.

## 4. Defects

| Defect | Verdict | Cost |
|---|---|---|
| STT rotation drops audio: `_rotate_stream` hard-stops the stream (`backend/stt/stream_manager.py:121-125`) and `send_audio` silently no-ops while inactive (`:97-100`). The class docstring promises A/B overlap and `stt_stream_overlap_seconds` exists in config, but no overlap is implemented — a 60–90-min interview hits ~13–20 loss windows. | **Launch-blocking** | 2–3 days: implement the documented overlap (dedup via `_last_emitted_end_time` already exists) or buffer-and-flush during rotation; soak-test 90 min |
| `session_state` never sent on large replay gap: `ws/handler.py:76-84` returns with a comment "caller should send" — no caller does (`main.py:915-943`). Any laptop sleep/network blip >1000 messages silently loses the UI state mid-interview. | **Launch-blocking** | ~1 day; snapshot exists via `session_mgr.get_transcript` |
| Sync FLAC encode in async generator (`buffer.py:76-81`) | Moot — remove FLAC (no-persistent-audio); keep behind an opt-in dev flag until the rotation fix soaks | 0.5 day |
| 10 stale "active" Firestore sessions | Not blocking; orphan detection exists (`main.py:102-105`) but never finalizes | 0.5 day cleanup pass |
| No auth/ownership | Blocking for anything beyond localhost; minimal bearer + owner field | 1–2 days |

## 5. Scope table, risk, disagreements, owner questions

| Must | Should | Cut (this launch) |
|---|---|---|
| Rotation fix; session_state fix; FLAC default-off; Windows loopback capture; packaging both OS; minimal auth+ownership; ADR amendments; stale-session cleanup | 0002-shaped internal seam; note-sync backend port + notes UI; surface the existing interview report (`_generate_final_summary`, `INTERVIEW_REPORT_PROMPT` already produce it — it's a UI gap); session-lifecycle reducer | Hosted gateway/cloud plane; native companions; 3-axis coverage + provenance (blocked on nonexistent evidence-binding intelligence); Chrome-extension speaker correlation (live-tested broken 2026-08-01; dual-stream source labels are the working baseline) |

**Biggest risk:** the Windows capture path is unproven while `capture.py` assumes name-substring device discovery and mono channel-0 extraction (`capture.py:95`) — if WASAPI loopback behaves differently, the second platform slips the whole schedule. Prototype it in week 1, not week 4.

**Disagreements with the current plan:** (1) treating 1B–1D as launch prerequisites is wrong under the new constraints — they protect against exposure this launch doesn't have; (2) ADR 0001's rejection of Python-as-local-app should be formally suspended, not silently ignored; (3) the labs are being implicitly priced as "done" — 49.5k insertions overstate readiness because the coverage intelligence layer is fixtures.

**Owner-only questions:** (1) Does no-persistent-audio bind from day one internally, or may FLAC crash-insurance stay opt-in until the rotation fix soaks? (2) Are GCP service credentials on each Ella machine acceptable for the internal period? (3) Notes+report or coverage — which is the launch differentiator? Both don't fit in 4–6 weeks. (4) Is there a committed date for a signed companion (determines whether Phase-1C continues in parallel or pauses)?
