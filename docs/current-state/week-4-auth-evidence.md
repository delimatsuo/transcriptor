# Week 4 authenticated-tenancy evidence

**Reviewed:** 2026-08-07

**Status:** Source, CI, hosted cleanup/index readback, synthetic provider soak,
and physical macOS source-isolation are qualified on the exact current head.
Windows, hosted tenant isolation (no active runtime), deployment, and
real-interview evidence remain open.

## Exact artifact

| Item | Value |
| --- | --- |
| Branch | `codex/week-4-auth` |
| Source/test qualification commit | `785cdffa6649f5dda39fbf46cc2b0319c92ec34c` |
| Latest lifecycle hardening commit | `66591bf` (deletion clears in-process interview context, detached warning work, capabilities, and WebSocket replay state) |
| Latest generation-fence commit | `15f561d` (late callbacks and provider generations cannot publish or persist after deletion fencing) |
| CI trigger hardening commit | `f4e19d2` (checked-in workflow also declares Week 4 branch pushes) |
| WebSocket delivery hardening commit | `254a22e` (bounded concurrent sends evict slow peers without stalling STT callbacks) |
| Terminal replay cleanup commit | `39fb0b6` (durable terminal cleanup releases the WebSocket replay ring) |
| Latest locally and CI-qualified head | `785cdffa6649f5dda39fbf46cc2b0319c92ec34c` |
| PR head at last evidence capture | `785cdffa6649f5dda39fbf46cc2b0319c92ec34c` |
| Pull request | [#8](https://github.com/delimatsuo/transcriptor/pull/8), draft, stacked on `codex/week-3-evidence-report` |
| Remote head at last evidence capture | Matched the PR head above |
| Exact-head CI run | [31182899947](https://github.com/delimatsuo/transcriptor/actions/runs/31182899947), passed backend and frontend jobs on `785cdff` |

## Source and test evidence

- Startup performs `google.auth.default()` plus credential refresh before
  readiness, with a 10-second deadline and the exact loud remediation message;
  mocked refresh failure, stuck-refresh, and lifespan-order tests pass.
- Backend: 224 tests passed locally, including 46 focused authorization-matrix
  tests. The matrix covers every `/api` route pattern, token admission, CORS
  rejection, cross-owner and child-scope failures, stop capabilities, WebSocket
  replay/expiry, raw review-record scope, and disabled extension behavior. The
  inventory adds deterministic content-free ownership-scope tests.
- Frontend: 45 unit tests passed; TypeScript and production build passed. Exact-head CI reran frontend tests, typecheck, and build on `785cdff`.
- Browser rehearsal: 19 Playwright tests passed with the fixed synthetic
  principal. The bypass is test-only and does not bypass backend authentication.
- Dependency audit: `npm audit --audit-level=moderate` reports zero
  vulnerabilities.
- GitHub Actions: exact-head run
  [31182899947](https://github.com/delimatsuo/transcriptor/actions/runs/31182899947)
  passed both backend and frontend jobs on `785cdff`. The checked-in workflow
  declares manual dispatch, Week 4 branch pushes, and the stacked-PR base.

## Efficiency and cost controls

- Gemini models are cached per static system prompt and all model requests share
  a bounded process-local semaphore.
- Repeated suggestion context is capped at 24,000 characters and suggestion
  output is capped at 1,024 tokens.
- Queue and generation latency are logged without prompt or transcript content.
- At most one in-flight suggestion generation is allowed per session; stale
  suggestion work is dropped on duplicate scheduling and canceled on cleanup.
- Suggestion cadence counts only finalized candidate responses; interviewer
  speech no longer triggers paid suggestion generations. It uses the original
  source label even when the optional extension overrides the display name.
- At most one in-flight rolling-summary generation is allowed per session;
  duplicate work is coalesced and canceled on cleanup.
- Rolling-summary transcript input is capped at 16,000 characters per update;
  the newest tail is retained because the prior summary carries earlier context.
- Rolling-summary updates send only segments appended since the previous summary;
  transcript indices are used because per-source STT sequence numbers reset.
- Rolling-summary state is allocated per authenticated session and removed during
  terminal cleanup; summary text, counters, and indices are not shared across
  sessions or organizations.
- Provider failures use a bounded exponential rolling-summary cooldown and do not
  broadcast or persist stale/blank summaries as successful coverage.
- Live transcript segments receive a session-global durable ordinal while the
  source-local STT counter is retained for provenance; legacy source-scoped
  records remain reconstructable without accepting same-source duplicates.
- Pre-interview analysis bounds the combined provider input at 30,000 characters,
  including the job description, and oversized uploads are read only one byte
  beyond the parser limit before rejection.
- Final report generation fails closed before the provider call when durable
  context plus transcript exceeds the configurable 120,000-character budget;
  it records `report_input_too_large` rather than silently truncating evidence.
- Vertex AI location is explicit through `LLM_LOCATION` (default
  `us-central1`); changing it requires the provider/privacy region gate.
- Every Gemini request has a configurable 60-second client deadline;
  timeout cleanup releases the shared request queue, including the reusable
  streaming path.
- A centralized `LLM_MAX_INPUT_CHARS` ceiling rejects oversized provider input
  before model construction or network invocation; the final report cap is
  aligned to the same 120,000-character default.
- A centralized `LLM_MAX_OUTPUT_TOKENS` ceiling rejects oversized generation
  budgets before provider invocation.
- Compatibility meeting summaries combine the per-session rolling summary with
  only a bounded 50-segment tail instead of rebuilding an unbounded transcript;
  coverage metadata reports only the tail when the rolling watermark is stale.
- Per-session cumulative final-word prefixes make rolling-summary cadence checks
  constant-time instead of rescanning the entire transcript suffix per segment.
- After terminal durability is recorded, the process releases transcript and
  word-prefix payloads while retaining session metadata; incomplete stops defer
  release until their terminal session write succeeds.
- A failed durable transcript-child write marks the session as unsafe to evict;
  later parent-session persistence cannot silently discard the in-memory copy.
- Incomplete-stop cleanup keeps the short-lived stop recovery capability until
  the terminal session write succeeds; a transient Firestore failure therefore
  remains retryable after bearer-token loss, and successful durability revokes
  the capability.
- Failed child writes set durable parent metadata to `transcriptDurability=pending`
  on a best-effort path, replay all final children in a bounded batch before
  terminal persistence, and keep incomplete reviews out of the ready state.
- Active deletion is rejected; terminal deletion shares the stop lock, fences
  late transcript callbacks, and cancels/awaits detached report work before the
  cascade and tombstone.
- Successful deletion also clears in-process interview documents, context
  windows, capability maps, detached single-source checks, and WebSocket replay
  state.
- A transcript callback durability failure stops STT recovery instead of
  reconnecting every 0.5 seconds during a Firestore outage; the session remains
  visibly incomplete and retryable.
- Late transcript callbacks and in-flight rolling-summary or suggestion
  generations re-check the deletion fence before child writes, broadcasts, or
  summary persistence.
- Rolling summaries advance only through contiguous bounded transcript batches;
  a backlog schedules follow-up batches without silently skipping older speech.
- Vertex initialization and static-prompt model construction are serialized,
  and configured output ceilings are honored by every feature callsite.
- Unchanged transcript/suggestion/summary rendering is memoized and report
  polling backs off to a five-second maximum interval.
- WebSocket replay and broadcast sends have a bounded deadline; broadcasts send
  to peers concurrently and evict slow or dead sockets so capture callbacks do
  not wait on browser delivery.
- Terminal cleanup releases the replay ring after durable completion while an
  incomplete stop retains it for visible retry/recovery.

These are deterministic source-level guardrails. They do not prove hosted Vertex
quota enforcement or live-provider cost savings.

## Remaining release gates

- Hosted cleanup and index readback passed under the owner-approved corporate
  ADC in `transcriptor-490222`: zero remaining sessions/objects, five exact
  deletion tombstones (161 child documents and one GCS blob), and the checked-in
  `sessions(ownerId ASC, orgId ASC, startedAt DESC)` index uniquely `READY`.
- The privacy-safe synthetic Chirp 3 rotation soak passed for 600 seconds with
  three streams, clean drain, and zero client delivery gaps at both rotations.
- The physical macOS source-isolation gate passed on `785cdff` after explicit
  Vocaster One Host Microphone channel-4 configuration: system-only speech
  produced 170 BlackHole final characters and zero Vocaster wrong-channel text;
  microphone-only speech produced 51 Vocaster final characters and zero
  BlackHole wrong-channel text. No raw audio or transcript content was retained
  in evidence.
- Complete the physical Windows routing/owner gate, or record the owner's
  macOS-first launch plus a committed Windows fast-follow date. No local Windows
  device/VM was found, and no hosted Week 4 runtime exists for tenant-isolation
  testing.
- The exact hosted readback is live evidence for deletion/index state only; it
  does not prove hosted tenant isolation, deployment, provider quota, or
  real-interview behavior. The read-only inventory command remains
  `backend/scripts/inventory_legacy_scope.py` (version
  `week4-auth-legacy-scope-v1`).
- Keep Firebase/Firestore/provider access, deployment, first-user interviews,
  and real candidate data outside this source/test evidence record.

The PR remains draft until those gates have their own owner, environment, and
approval evidence.
