# Week 4 authenticated-tenancy evidence

**Reviewed:** 2026-08-06

**Status:** Source and test qualified on the Week 4 draft branch. This record is
not hosted, deployment, provider, physical-device, or real-interview evidence.

## Exact artifact

| Item | Value |
| --- | --- |
| Branch | `codex/week-4-auth` |
| Source/test qualification commit | `79638f7874b866639194c02d2ec2fc54fba5d157` |
| PR head at last evidence capture | `79638f7874b866639194c02d2ec2fc54fba5d157` |
| Pull request | [#8](https://github.com/delimatsuo/transcriptor/pull/8), draft, stacked on `codex/week-3-evidence-report` |
| Remote head at last evidence capture | Matched the PR head above |

## Source and test evidence

- Backend: 170 tests passed locally, including 43 focused authorization-matrix
  tests. The matrix covers every `/api` route pattern, token admission, CORS
  rejection, cross-owner and child-scope failures, stop capabilities, WebSocket
  replay/expiry, raw review-record scope, and disabled extension behavior. The
  inventory adds deterministic content-free ownership-scope tests.
- Frontend: 45 unit tests passed; TypeScript and production build passed.
- Browser rehearsal: 19 Playwright tests passed with the fixed synthetic
  principal. The bypass is test-only and does not bypass backend authentication.
- Dependency audit: `npm audit --audit-level=moderate` reports zero
  vulnerabilities.
- GitHub Actions: [run 31109609040](https://github.com/delimatsuo/transcriptor/actions/runs/31109609040)
  passed both backend and frontend jobs on the exact head above, with no
  action-runtime deprecation annotation.

## Efficiency and cost controls

- Gemini models are cached per static system prompt and all model requests share
  a bounded process-local semaphore.
- Repeated suggestion context is capped at 24,000 characters and suggestion
  output is capped at 1,024 tokens.
- Queue and generation latency are logged without prompt or transcript content.
- At most one in-flight suggestion generation is allowed per session; stale
  suggestion work is dropped on duplicate scheduling and canceled on cleanup.
- Unchanged transcript/suggestion/summary rendering is memoized and report
  polling backs off to a five-second maximum interval.

These are deterministic source-level guardrails. They do not prove hosted Vertex
quota enforcement or live-provider cost savings.

## Remaining release gates

- Deploy and verify the declared Firestore owner/org/startedAt composite index in
  the authorized hosted project.
- Complete the same-SHA macOS audio soak and physical Windows routing/owner gate.
- Resolve or explicitly accept dependency/runtime hardening findings before
  ready/merge.
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
