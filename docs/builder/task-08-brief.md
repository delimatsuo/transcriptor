# Task 08 source-only IAP preproduction candidate

## Authority and evidence ceiling

Implement and test source only on branch `codex/task08-iap-source`, based on
`dc262e55f62771615102cef22667f21c3a52a219`.  The original dirty Task 07
checkout is read-only input and must remain untouched.

The approved candidate is a private, browser-only preproduction canary:

- Firebase App Hosting serves the Next.js frontend at
  `https://tars.ellaexecutivesearch.com`.
- Browser HTTPS and WSS go directly to
  `https://api.tars.ellaexecutivesearch.com` / the equivalent `wss` origin.
- Cloud Run-native IAP uses Identity Platform external identities.
- Only the IAP service agent may hold `roles/run.invoker`.
- The application verifies every signed IAP assertion and independently applies
  the existing server-side organization and exact-address admission policy.
- Browser WebSockets have a 3,300-second absolute lifetime. Reconnect requires
  a new HTTP ticket and a freshly verified IAP assertion.
- Client logout and the application kill switch close browser sockets and stop
  browser audio immediately.
- Planned transitions require a zero-active-session result and a complete
  rollback manifest.
- BRL 250/month with 50/90/100 percent notifications is an alert-only proposal,
  not a hard spending limit and not provider state.

Source and offline tests cannot prove Identity Platform, IAP, IAM, DNS, TLS,
cookies, custom domains, deployed WebSockets, budgets, quotas, or rollback.
Do not claim otherwise.

Cloud Run IAP protects the complete service before application routing. The
current native desktop companions have no approved programmatic Identity
Platform/IAP session flow. In IAP mode, their direct socket is therefore
intentionally unqualified and must not gain an application-level bypass. The
browser audio socket can be admitted because its handshake carries the browser
IAP session.

## Non-negotiable boundaries

- Do not read or edit `AGENTS.md`, `frontend/AGENTS.md`, or
  `frontend/CLAUDE.md`.
- Do not inspect, copy, or edit any `.env*` file.
- Do not use Git.
- Do not call Firebase, GCP, IAP, Identity Platform, App Hosting, DNS, a live
  browser, or any other provider.
- Do not deploy, mutate IAM/configuration/budgets/quotas, inspect secrets or
  environment values, read logs/production data, or spend money.
- Use only synthetic identities, audiences, projects, and keys in tests.
- One writer owns this worktree for this packet.
- Edit only the allowlisted files below.

## Official protocol facts

- The application must validate `x-goog-iap-jwt-assertion`; unsigned
  `x-goog-authenticated-user-*` headers are not authority.
- The IAP issuer is exactly `https://cloud.google.com/iap`.
- A Cloud Run IAP audience has the form
  `/projects/PROJECT_NUMBER/locations/REGION/services/SERVICE_NAME`.
- External-identity user data is a JSON string in the signed `gcip` claim.
- IAP JWT lifetime is at most ten minutes plus clock skew.
- `gcp-iap-mode=GCIP_SIGNOUT` is the resource-level external-identity sign-out
  path.
- Cloud Run WebSockets are request-timeout bounded; this candidate closes at
  3,300 seconds and requires reconnect authentication.

References:

- https://docs.cloud.google.com/iap/docs/signed-headers-howto
- https://docs.cloud.google.com/iap/docs/external-identity-sessions
- https://docs.cloud.google.com/iap/docs/identity-howto
- https://docs.cloud.google.com/run/docs/triggering/websockets

## Exact implementation contract

### Backend configuration

Extend `Settings` without changing the default local/Firebase behavior:

- `auth_mode`: exact `firebase` or `iap`, default `firebase`.
- `auth_iap_audience`: required and strictly parsed only in IAP mode.
- `auth_iap_frontend_origin`: required exact HTTPS origin in IAP mode and fixed
  by the source contract to `https://tars.ellaexecutivesearch.com` for the
  approved candidate.
- `auth_iap_ws_max_lifetime_seconds`: default and maximum 3300.
- `auth_task08_operator_emails`: exact comma-separated subset of the existing
  admitted-address configuration; required in IAP mode.
- `auth_kill_switch`: boolean, default false.

IAP mode must reject startup/configuration when `auth_bypass` is true, audience
or frontend origin is absent/malformed, the WebSocket lifetime exceeds 3300,
or the operator set is empty/outside the admitted-address set. Existing
Firebase mode remains backward-compatible.

### Signed IAP assertion

Create a focused verifier that:

1. Requires exactly one nonblank signed assertion value.
2. Calls an injectable verifier seam whose production default validates the
   signature against the official IAP public-key endpoint and the configured
   audience. Offline tests patch/inject it and make zero network calls.
3. Independently requires exact issuer and audience, integer `iat`/`exp`, the
   official temporal bounds with 30 seconds skew, and no bool-as-int values.
4. Requires `gcip` to be a bounded JSON string, rejects duplicate JSON keys,
   and requires a mapping with exact string `sub`, exact string `email`,
   `email_verified is True`, integer `auth_time`, and Google sign-in provider.
5. Canonicalizes the validated email, checks the existing server-side admitted
   set, and derives the configured org server-side.
6. Returns content-free `AuthenticationError` failures with no raw token,
   email, claim, or provider payload in errors.
7. Never uses unsigned forwarded identity headers or a Firebase bearer as a
   fallback in IAP mode. Conflicting unsigned headers cannot change the signed
   principal.

### Runtime authorization gate

Create a process-local, testable gate with:

- a monotonic terminal kill-switch latch;
- principal logout/revocation cutoffs based on validated `auth_time`;
- per-connection leases that receive an event on principal logout or global
  kill;
- counts for live registered browser connections;
- idempotent revocation/kill behavior; and
- no raw identity/token logging.

This gate is only an application safety layer for the single-instance canary.
It is not durable provider revocation and must be documented as such.

### HTTP boundary and control routes

In IAP mode, every `/api/` request except actual CORS preflight must verify the
signed IAP assertion before route code. Firebase bearer-only and unsigned
identity-header-only requests fail closed. Preserve Firebase mode behavior.

Add:

- `GET /api/auth/bootstrap`: after successful middleware admission, redirect
  only to the configured frontend origin. Ignore any attacker-selected return
  destination.
- `POST /api/auth/logout`: synchronously revoke the current principal, revoke
  outstanding browser tickets for that principal, and signal its active
  browser leases before returning a content-free result.
- `GET /api/admin/task08/transition-readiness`: operator-only, read-only,
  returning counts only (never IDs) for active business sessions, registered
  browser sockets, outstanding browser tickets, and active stream keys, plus
  `ready` true only when every count is zero and the kill switch is not active.
- `POST /api/admin/task08/kill-switch`: operator-only emergency application
  mutation. Latch closed first, revoke outstanding tickets/stream keys, signal
  registered browser leases, and then return counts. This is source behavior;
  do not execute it against any running service.

Health/readiness remain unauthenticated for Cloud Run probes. In IAP mode,
stop-capability fallback must not substitute for a signed IAP assertion.

### WebSocket boundary

For `/ws/{session_id}` in IAP mode:

- verify the signed assertion before popping the one-time ticket, reading the
  session, accepting the socket, replaying messages, or registering it;
- require the verified principal to equal the ticket principal and the session
  owner/org;
- register a runtime lease;
- close at the earlier of 3300 seconds, principal logout/revocation, or global
  kill; ping/message activity never extends the absolute deadline;
- remove the lease and socket deterministically; and
- require both a newly minted ticket and freshly verified assertion on every
  reconnect.

For `/api/stream/native/{session_id}` in IAP mode, verify and bind the signed
IAP browser principal before `accept` or stream-manager work, then apply the
same 3300-second/logout/kill lease. This admits the browser audio socket. It
does not create a native desktop bypass; a native client without an IAP session
is denied at the edge/application and remains unqualified.

### Frontend

Add one pure runtime-config parser as the authority for authentication/API/WSS
settings. Local Firebase defaults remain available outside production. IAP
mode must require the exact approved HTTPS/WSS origins, same-site hosts, direct
`/ws` and `/api/stream/native` paths, credentialed requests, and bypass false.
Reject loopback, HTTP/WS, credentials, queries/fragments, proxy-relative paths,
wrong hosts, or overbroad values in IAP mode.

Add pure IAP URL/lifecycle helpers. In IAP mode:

- initial admission uses credentialed `GET /api/me` and returns only the
  admitted profile;
- sign-in is a top-level navigation to the API bootstrap route so IAP can
  redirect through its configured sign-in page;
- API requests use `credentials: include` and no Firebase bearer authority;
- logout first emits a terminal local auth event, stops browser capture,
  cancels retries, closes sockets, and hides user/data state; then it calls the
  application logout route and navigates to the IAP resource with
  `gcp-iap-mode=GCIP_SIGNOUT`;
- logout/kill policy closes are terminal and cannot trigger automatic retry;
- ordinary network or 3300-second policy expiry may reconnect, but the
  reconnect always obtains a new HTTP ticket.

Do not implement a custom Identity Platform sign-in page or provider setup.

### Source contract and documentation

Add a machine-readable JSON contract and semantic parser tests. It must bind:

- direct App Hosting frontend plus IAP Cloud Run API/WSS topology;
- exact approved origins;
- no public Cloud Run principal and exactly one IAP service-agent invoker
  placeholder;
- exact IAP issuer and Cloud Run audience template;
- `gcip` JSON-string semantics;
- 3300-second socket maximum and fresh reconnect authentication;
- browser-only evidence ceiling and native desktop incompatibility;
- alert-only BRL 250 proposal with 50/90/100 thresholds and recipient;
- zero-active categories; and
- complete rollback categories for frontend release, Cloud Run artifact/
  revision/traffic, IAM, IAP, Identity Platform, origins/DNS/TLS, API-key
  restrictions, runtime configuration, budgets, quotas, kill switch, and
  evidence.

Add an ADR and a non-authorizing canary/rollback runbook. No executable cloud
commands and no claim that provider state is ready.

## Required causal tests

Use synthetic fixtures and injected clocks/verifiers. At minimum prove:

- positive signed IAP admission;
- missing/malformed/bad-signature verifier failure;
- wrong issuer, audience, expired, future-issued, excessive lifetime, bool time;
- missing/malformed/duplicate-key `gcip`, unverified email, sixth address,
  wrong provider, malformed subject/email;
- unsigned identity headers alone fail and cannot override a signed principal;
- Firebase bearer alone fails in IAP mode;
- IAP and bypass are mutually exclusive;
- HTTP route code has no side effects before IAP admission;
- WebSocket verification happens before ticket pop/session read/accept;
- ticket/principal/session mismatch denial;
- 3299 seconds stays open and 3300 closes; ping does not extend;
- reconnect re-verifies and remints;
- logout signals active leases and makes the old `auth_time` inadmissible;
- kill latch closes first, is idempotent, blocks new admission, signals leases,
  revokes tickets/stream keys, and suppresses frontend retry;
- zero-active readiness counts every declared category;
- runtime config hostile URL matrix;
- frontend logout ordering and stale async completion;
- every rollback category is required by semantic contract validation; and
- no provider/network calls occur in any test.

The tests must be mutation-effective for removal of issuer, audience, gcip,
allowlist, signed-header authority, 3300-second lease, terminal reconnect
suppression, and rollback-category checks.

## File allowlist

You may create or edit only:

- `backend/iap_auth.py`
- `backend/auth_runtime.py`
- `backend/auth.py`
- `backend/config.py`
- `backend/main.py`
- `backend/stt/stream_manager.py`
- `backend/stt/google_stt.py`
- `backend/sessions/manager.py`
- `backend/tests/test_iap_auth.py`
- `backend/tests/test_iap_runtime.py`
- `backend/tests/test_task08_iap_source_contract.py`
- `backend/tests/test_stream_manager_drain.py`
- `frontend/src/lib/runtimeConfig.ts`
- `frontend/src/lib/runtimeConfig.test.ts`
- `frontend/src/lib/iapSession.ts`
- `frontend/src/lib/iapSession.test.ts`
- `frontend/src/lib/auth.ts`
- `frontend/src/hooks/useWebSocket.ts`
- `frontend/src/hooks/useBrowserAudioCapture.ts`
- `frontend/src/app/page.tsx`
- `docs/architecture/0004-iap-private-preproduction.md`
- `docs/launch/task-08-iap-source-contract.json`
- `docs/launch/task-08-iap-preproduction-canary.md`
- `docs/builder/task-08-report.md`

Do not edit this brief.

## Verification and report

Run only local/offline commands. Use the existing project Python environment
from the primary checkout if the clean worktree has no local virtualenv. Use
the existing frontend dependencies without installing or downloading anything.

Required checks:

1. focused new backend tests;
2. existing auth/auth-matrix/WS/native-stream/readiness regressions;
3. focused new frontend unit tests and the existing frontend unit suite;
4. TypeScript no-emit and Next production build if existing dependencies are
   available locally;
5. `git diff --check` may be run only by Codex, not the builder.

The report must list exact files changed, tests actually run and counts,
failures/limitations, and an explicit statement that no provider, browser,
credentials, environment-value, deployment, Git, or spending action occurred.

## Causal repair scope amendment

During the Luna continuation, the Codex designer authorized the addition of
`frontend/src/lib/iapLifecycle.ts` and `frontend/src/lib/iapLifecycle.test.ts`
to the Task 08 source-only allowlist. These files provide deterministic,
dependency-free lifecycle primitives and their executable production-wiring
tests for stale-attempt fencing, terminal decisions, bounded retry, and late
media/resource disposal. This amendment supplements the original brief; it
does not rewrite the original design history.

## Causal repair scope amendment (Luna continuation)

For the final causal repair packet, the Codex designer additionally authorized
the existing STT lifecycle sources `backend/stt/stream_manager.py` and
`backend/stt/google_stt.py`, together with
`backend/tests/test_stream_manager_drain.py`, for bounded offline repair and
drain-race evidence. The amendment also authorizes updates to the existing
Task 08 backend/frontend contract tests and source-only documentation. This
does not authorize provider, browser, deployment, credential, or production
actions, and it does not rewrite the original design history.
