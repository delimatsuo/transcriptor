# Task 08 source-only causal hardening report

status: SOURCE_ONLY_NON_AUTHORIZING

This candidate contains the bounded R1-R10 and F1-F23 source and
offline-test repairs. It is not a provider, browser, device, deployment, or
production-readiness qualification. No P1 claim is promoted to live/provider
evidence here.

## Files changed

- `backend/iap_auth.py`
- `backend/auth.py`
- `backend/auth_runtime.py`
- `backend/config.py`
- `backend/main.py`
- `backend/ws/handler.py`
- `backend/sessions/manager.py`
- `backend/tests/test_iap_auth.py`
- `backend/tests/test_iap_runtime.py`
- `backend/tests/test_task08_iap_source_contract.py`
- `backend/stt/stream_manager.py`
- `backend/stt/google_stt.py`
- `backend/tests/test_stream_manager_drain.py`
- `frontend/src/lib/runtimeConfig.ts`
- `frontend/src/lib/runtimeConfig.test.ts`
- `frontend/src/lib/iapSession.ts`
- `frontend/src/lib/iapSession.test.ts`
- `frontend/src/lib/iapLifecycle.ts`
- `frontend/src/lib/iapLifecycle.test.ts`
- `frontend/src/lib/auth.ts`
- `frontend/src/hooks/useWebSocket.ts`
- `frontend/src/hooks/useBrowserAudioCapture.ts`
- `frontend/src/app/page.tsx`
- `docs/architecture/0004-iap-private-preproduction.md`
- `docs/launch/task-08-iap-source-contract.json`
- `docs/launch/task-08-iap-preproduction-canary.md`
- `docs/builder/task-08-brief.md`
- `docs/builder/task-08-report.md`

## Implementation summary

Added strict IAP-mode settings requiring exactly five unique
`ellaexecutivesearch.com` addresses and server-derived `AUTH_ORG_ID=ella-internal`,
signed assertion verification with injected offline seams, bounded
duplicate-safe nested `gcip.firebase.sign_in_provider` parsing, exact
issuer/audience and temporal validation, and content-free errors. Added the
process-local monotonic revocation/kill gate with browser leases, ticket/key
accounting, and operator-only readiness/kill routes. IAP HTTP, browser
WebSocket, and browser audio boundaries verify before route/session/socket side
effects; native desktop companions without an IAP session remain denied and
unqualified. Added exact-single-origin application CORS plus the explicit
future provider-side `access_settings.cors_settings.allow_http_options=true`
precondition (not configured or proven here), fixed-origin browser runtime
configuration, provider-managed bootstrap/signout helpers, synchronous
terminal logout cleanup, and stale-attempt/reconnect suppression. Added the
source contract, ADR, and non-authorizing canary/rollback runbook.
The Luna continuation added a designer-authorized scope amendment for the
dependency-free `frontend/src/lib/iapLifecycle.ts` controller and its
executable test; the amended allowlist also includes content-free provider
exception handling in `backend/auth.py`.
The final Luna continuation added a second Codex-authorized scope amendment
for the existing STT lifecycle sources and drain tests above, covering
provider-operation quiescence and emergency audio abort. This is an amended
source-only inventory, not a claim of an original-allowlist-only edit.

## Tests run

- Focused Task 08 suite (`backend/tests/test_iap_auth.py backend/tests/test_iap_runtime.py backend/tests/test_task08_iap_source_contract.py`): **92 passed**.
- STT drain suite (`backend/tests/test_stream_manager_drain.py`): **25 passed**.
- Full backend suite excluding `backend/tests/test_cloud_run_readiness.py`: **404 passed**.
- Frontend unit suite (`npm --prefix frontend test`): **91 passed**.
- TypeScript check (`frontend/node_modules/.bin/tsc -p frontend/tsconfig.json --noEmit`): **passed**.
- Default production frontend build (`NEXT_TELEMETRY_DISABLED=1 npm --prefix frontend run build`): **passed**.
- Exact documented IAP-value production frontend build (inline synthetic public values only): **passed**.
- Backend source AST parse for changed backend modules and contract JSON parse: **passed**.
- Explicit changed-file trailing-whitespace scan: **passed**.

## Tests not run / limitations

- No browser, device, provider, deployment, CI, live WebSocket, Identity
  Platform, IAP, IAM, DNS, TLS, cookie, budget, quota, production, privacy/legal,
  or audible-quality evidence was obtained.
- `backend/tests/test_cloud_run_readiness.py` was intentionally excluded because
  it reads prohibited `.env*` examples.

## R1-R10 repair map

- R1: raw non-ASCII is rejected before case folding in IAP claims and strict
  settings email parsing; adversarial long-s and Kelvin aliases are covered.
- R2: the production `verify_iap_signature` path passes the exact audience,
  certificate URL, and 30-second skew when the installed verifier supports it.
  The offline seam uses a locally stubbed verifier/transport and tamper case;
  it does not prove Google's live ES256 implementation or network behavior.
- R3: the monotonic kill latch keeps ordinary API requests at 401 while the
  two exact operator control paths remain reachable only for a freshly
  verified operator principal; Firebase-bearer, unsigned, revoked, and
  non-operator cases are denied.
- R4: an operation/admission lease spans awaited session creation. Losing the
  lease terminalizes the session as INCOMPLETE, cancels heartbeat/pipeline,
  clears capabilities/context, persists best effort, returns content-free 401,
  and releases the lease.
- R5: browser WebSocket expiry starts at connection acceptance, with its
  watcher active during replay. The outer cleanup fence covers read, owner,
  lease, accept/replay, cancellation, receive, and deadline failures. The
  3300-second bound is retained and 3301 is rejected by settings.
- R6: native stream accept, health setup/emission, watchdog, cancellation, and
  failure paths clean leases, tasks, expiry, and health counters.
- R7: frontend IAP ticket 401/403 and policy/revocation closes are idempotent
  terminal transitions. Generations fence stale fetch/json/socket/device
  completions; ordinary failures and expiry mint fresh tickets with bounded
  retry; audio setup is transactional.
- R8: logout synchronously tears down local capture/socket state, revokes the
  principal, terminalizes owned active sessions and pipeline/heartbeat state,
  persists best effort without claiming durability, and navigates after a
  bounded frontend wait.
- R9: the ADR, canary runbook, JSON contract, and semantic test require the
  exact unproven/false gates for Cloud Run max instances 1, one serving
  revision, 100% unsplit traffic, and the process-local routing assumption.
- R10: offline source-wiring, race, and mutation-effective contract tests are
  included. The JSON contract is ignored by the repository's `*.json` rule;
  the Git owner must force-stage the exact contract path. No `.gitignore`
  change was made.

## F1-F11 continuation repair map

- F1: kill latches before terminalizing active business sessions and stopping
  native managers; native StreamManager creation rechecks lease, principal,
  session, and stream-key admission after awaited start and stops an
  unpublishable manager.
- F2: native connection start is bound before accept; expiry/revocation watches
  run during stalled accept, admission is rechecked before health setup, and
  accept/setup cleanup settles leases, tasks, and health counters.
- F3: operator control exceptions after global kill require a current principal
  cutoff; a revoked operator cannot reuse old auth time while a fresh operator
  can repeat/read the controls.
- F4: every IAP `apiFetch` response at 401/403 invokes the idempotent terminal
  transition, including non-ticket REST calls; 5xx/network failures remain
  retryable.
- F5: stream and audio-graph generations advance together; stale media setup
  disposes tracks/nodes/new contexts and cannot commit a later device attempt.
- F6: terminal cleanup collects, cancels, and awaits rolling-summary,
  suggestion, and single-source tasks in logout, kill, and session-race paths.
- F7: executable lifecycle controllers provide generation fencing, terminal
  disposition, bounded retry, and stale-resource disposal; strict source tests
  bind production hook/auth call sites and reject hardcoded guard mutants.
- F8: the brief records the Luna designer scope amendment and this inventory
  includes `backend/auth.py`, the brief, and lifecycle controller files.
- F9: provider/verifier exceptions are discarded before content-free errors are
  raised; tests assert no secret/cause/context survives the outer error.
- F10: expired WebSocket/stream tickets are pruned at mint, lookup, and
  readiness, with runtime ticket counts consumed in lockstep.
- F11: the machine contract and semantic tests require terminal statuses
  `[401,403]`, authenticated-data unmount, 5000 ms logout bound, stale-attempt
  fencing, late-media disposal, and 5xx/network retryability.

## F12-F17 final causal repair map

- F12: provider work is registered before Gemini awaits with owner/session
  scope; logout and kill cancel and await registered and not-yet-started
  workers, readiness includes count-only `active_provider_operations`, and
  post-terminal report persistence/broadcast is fenced.
- F13: STT has an emergency abort path that drops pending audio and settles
  response/rotation tasks; kill/logout mark sessions incomplete and use this
  path, while ordinary stop retains graceful final-response draining.
- F14: IAP create-session responses set `stream_key` to null and the browser
  page neither stores nor passes a native key in IAP mode; Firebase/local
  compatibility remains unchanged.
- F15: dependency-free lifecycle tests exercise deferred terminal fencing,
  timer/retry cancellation, and late-resource disposal; production hooks and
  auth remain bound to those helpers by source-wiring assertions.
- F16: ticket minting prunes expiry and replaces prior same-principal,
  same-session tickets while consuming runtime accounting in lockstep.
- F17: concurrent native StreamManager starts publish exactly one manager,
  emergency-abort the loser, and route both callers to the published manager;
  provider start never awaits while `native_sm_lock` is held.

## F18-F23 closure map

- F18: IAP browser WebSocket admission starts lease/deadline coverage before
  session read; terminal events during the read fence the socket before any
  accept, replay, or registration side effect. The connection manager also
  rechecks admission after accept and across replay, unregistering and closing
  on a terminal event during an awaited send.
- F19: logout/kill emergency-abort affected STT pipelines before unrelated
  provider cancellation; late responses and callbacks recheck emergency,
  admission, and session status before mutation, broadcast, persistence, or
  later scheduling.
- F20: emergency cancellation is bounded and idempotent, so terminal routes
  return without waiting indefinitely. Response, rotation,
  current-stream-abort, and injected pipeline tasks remain exposed and counted
  while cancellation-resistant; done callbacks clear accounting only after
  actual completion. Invalid IAP STT tasks are cancelled before registration
  or provider/STT side effects. Force-clear/background cleanup uses the same
  fail-closed bound, without weakening graceful user-stop draining.
- F21: rolling, suggestion, and final-summary operations revalidate their
  registered operation after every awaited read, state write, provider,
  broadcast, and persistence boundary and before subsequent side effects.
  IAP final-summary exception handling intentionally performs no `failed`
  marker write, leaving the durable `generating` marker for reconciliation by
  a fresh authorized run; non-IAP failure persistence remains compatible.
- F22: IAP provider and STT work carries the exact session `auth_time`
  generation; missing or unbound principals, empty uids, and malformed
  (including boolean) auth_time values are rejected before admission, with the
  newly created invalid STT task cancelled before it can run. A newer
  validated generation can schedule fresh work. Local/non-IAP compatibility
  remains available.
- F23: production-owned frontend lifecycle helpers and deterministic deferred
  tests fence terminal ticket/socket/media completions, dispose late resources,
  and make scheduled retries inert after terminal invalidation.

## Constraints confirmed

All tests use synthetic identities, audiences, and injected verifiers/clocks.
No provider calls or successful network access occurred. No browser,
credentials, environment-value inspection,
deployment, Git command, production-data/log access, IAM/configuration/budget/
quota mutation, or spending action occurred. The native desktop companion is
intentionally fail-closed/unqualified in IAP mode. The original brief was
amended during the Luna continuation by the Codex designer to authorize
`frontend/src/lib/iapLifecycle.ts` and `frontend/src/lib/iapLifecycle.test.ts`;
the scope was later amended to authorize the existing STT lifecycle sources
and drain tests. All edits are within that amended scope. No
out-of-amended-allowlist edit is claimed.

## Evidence ceiling and remaining uncertainty

Evidence is limited to source inspection, synthetic fixtures, locally stubbed
offline seams, offline unit tests, and the local frontend build. It does not
prove Identity Platform/IAP/IAM configuration, provider traffic or revision
state, Cloud Run instance/session-affinity behavior, browser/device capture,
live WebSockets, live cryptographic verification, DNS/TLS/cookies, privacy or
legal compliance, budgets/quotas/billing, production data, or audible quality.
The native desktop companion remains fail-closed and unqualified in IAP mode.
The documented provider gates remain `configured=false`, `proven=false`, and
`source-authorized=false`; a separately authorized owner/provider canary is
required before any readiness claim.
