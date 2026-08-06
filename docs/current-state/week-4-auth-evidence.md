# Week 4 authenticated-tenancy evidence

**Reviewed:** 2026-08-06

**Status:** Source and test qualified on the Week 4 draft branch. This record is
not hosted, deployment, provider, physical-device, or real-interview evidence.

## Exact artifact

| Item | Value |
| --- | --- |
| Branch | `codex/week-4-auth` |
| Commit | `91a65f3dd9aa74ef54fa777916df5eb4b54421c2` |
| Pull request | [#8](https://github.com/delimatsuo/transcriptor/pull/8), draft, stacked on `codex/week-3-evidence-report` |
| Remote head | Matches the local commit above |

## Source and test evidence

- Backend: 166 tests passed locally, including 43 focused authorization-matrix
  tests. The matrix covers every `/api` route pattern, token admission, CORS
  rejection, cross-owner and child-scope failures, stop capabilities, WebSocket
  replay/expiry, raw review-record scope, and disabled extension behavior.
- Frontend: 45 unit tests passed; TypeScript and production build passed.
- Browser rehearsal: 19 Playwright tests passed with the fixed synthetic
  principal. The bypass is test-only and does not bypass backend authentication.
- Dependency audit: `npm audit --audit-level=moderate` reports zero
  vulnerabilities.
- GitHub Actions: [run 31108211107](https://github.com/delimatsuo/transcriptor/actions/runs/31108211107)
  passed both backend and frontend jobs on the exact head. The only annotation
  is an upstream Node 20 action-runtime deprecation warning.

## Efficiency and cost controls

- Gemini models are cached per static system prompt and all model requests share
  a bounded process-local semaphore.
- Repeated suggestion context is capped at 24,000 characters and suggestion
  output is capped at 1,024 tokens.
- Queue and generation latency are logged without prompt or transcript content.
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
- Inventory and quarantine or owner-approved-backfill legacy records missing
  ownership; do not auto-claim them.
- Keep Firebase/Firestore/provider access, deployment, first-user interviews,
  and real candidate data outside this source/test evidence record.

The PR remains draft until those gates have their own owner, environment, and
approval evidence.
