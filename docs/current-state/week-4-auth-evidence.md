# Week 4 authenticated-tenancy evidence

**Reviewed:** 2026-08-06

**Status:** Source and test qualified on the Week 4 draft branch. This record is
not hosted, deployment, provider, physical-device, or real-interview evidence.

## Exact artifact

| Item | Value |
| --- | --- |
| Branch | `codex/week-4-auth` |
| Source/test qualification commit | `f21b24cbbc37aac2a8d3bf9ea9f16d7c01cf4a75` |
| PR head at last evidence capture | Verify against the live PR head before any hosted or device work; source qualification is bound to the commit above. |
| Pull request | [#8](https://github.com/delimatsuo/transcriptor/pull/8), draft, stacked on `codex/week-3-evidence-report` |
| Remote head at last evidence capture | Matched the PR head above |

## Source and test evidence

- Startup performs `google.auth.default()` plus credential refresh before
  readiness, with a 10-second deadline and the exact loud remediation message;
  mocked refresh failure, stuck-refresh, and lifespan-order tests pass.
- Backend: 195 tests passed locally, including 43 focused authorization-matrix
  tests. The matrix covers every `/api` route pattern, token admission, CORS
  rejection, cross-owner and child-scope failures, stop capabilities, WebSocket
  replay/expiry, raw review-record scope, and disabled extension behavior. The
  inventory adds deterministic content-free ownership-scope tests.
- Frontend: 45 unit tests passed; TypeScript and production build passed.
- Browser rehearsal: 19 Playwright tests passed with the fixed synthetic
  principal. The bypass is test-only and does not bypass backend authentication.
- Dependency audit: `npm audit --audit-level=moderate` reports zero
  vulnerabilities.
- GitHub Actions: current-head checks are pending after repeated runner-service
  stalls. Local exact-source qualification is complete. Historical run
  [31119758788](https://github.com/delimatsuo/transcriptor/actions/runs/31119758788)
  passed both jobs on an earlier tree; it is not used as green evidence for
  the current source.

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
- Unchanged transcript/suggestion/summary rendering is memoized and report
  polling backs off to a five-second maximum interval.

These are deterministic source-level guardrails. They do not prove hosted Vertex
quota enforcement or live-provider cost savings.

## Remaining release gates

- Deploy and verify the declared Firestore owner/org/startedAt composite index in
  the authorized hosted project using the non-authorizing
  [hosted-gate checklist](../launch/week-4-hosted-gate-checklist.md).
- Complete the same-SHA macOS audio soak and physical Windows routing/owner gate.
- Resolve the distinction between the historical containment inventory and the
  owner-authorized purge report with a fresh authorized cloud readback before
  any migration; quarantine or owner-approved-backfill any records missing
  ownership and never auto-claim them. The read-only inventory command is
  `backend/scripts/inventory_legacy_scope.py` (version
  `week4-auth-legacy-scope-v1`).
- Keep Firebase/Firestore/provider access, deployment, first-user interviews,
  and real candidate data outside this source/test evidence record.

The PR remains draft until those gates have their own owner, environment, and
approval evidence.
