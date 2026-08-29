# Task 08 IAP preproduction canary and rollback runbook

Non-authorizing source runbook. No step below executes a cloud command or
approves a deployment.

## Entry gate

Before a separately authorized canary, the owner records the exact source
revision, complete rollback manifest, operator, UTC time, and evidence hash.
The transition-readiness route must report zero for every declared category:
active business sessions, registered browser sockets, outstanding browser
tickets, active stream keys, and active provider operations. The process kill switch must be inactive at
the start, and the owner must retain a tested stop path.

Application CORS is exact-single-origin: only
`https://tars.ellaexecutivesearch.com` is allowed in IAP mode. Before any
separately authorized canary, the owner must verify that the provider-side IAP
setting `access_settings.cors_settings.allow_http_options=true` is configured
so the separate-origin browser preflight can reach the API. This source
candidate does not configure, authorize, or claim readiness for that provider
setting.

The candidate is browser-only. Native desktop companions have no approved IAP
session flow and must remain denied/unqualified. The source tests do not prove
provider state, cookies, DNS/TLS, custom domains, deployed WebSockets, billing,
budgets, quotas, privacy/legal compliance, or physical/audible behavior.

Before any canary, the owner must independently verify the single-instance
provider gate: Cloud Run `max_instances=1`, exactly one serving revision, and
100% unsplit traffic to it, plus the session-affinity/routing assumption needed
by process-local tickets, sessions, kill, and readiness. The source contract
records all four gates as `configured=false`, `proven=false`, and
`source-authorized=false`; this task does not configure or prove them.

## Observation checklist

Observe only the authorized browser canary and record counts and PASS/FAIL
labels, never tokens or raw identity payloads. Confirm `/api/me` admission is
server-side, bootstrap ignores attacker-selected destinations, logout closes
capture/sockets before provider signout, and each reconnect obtains a fresh
HTTP ticket and signed assertion. Confirm the 3,300-second absolute socket
bound is not extended by ping or message activity.

The five-address admission set is exact and unique, uses only the
`ellaexecutivesearch.com` domain, and is bound to the server-derived
`AUTH_ORG_ID=ella-internal`; the operator set must be nonempty and a subset.
The BRL 250/month proposal is alert-only with notifications at 50, 90, and 100
percent to `deli@ellaexecutivesearch.com`. It is not a hard spending limit or
evidence of configured provider state.

## Rollback manifest

The manifest must include a concrete owner and reversible state for every
category, even when a category is unchanged:

1. frontend release;
2. Cloud Run artifact, revision, and traffic;
3. IAM and IAP;
4. Identity Platform;
5. origins, DNS, and TLS;
6. API-key restrictions;
7. runtime configuration;
8. budgets and quotas;
9. application kill switch; and
10. evidence, including the exact revision and timestamps.

If any category is missing, active-session counts are nonzero, or a provider
observation is unavailable, stop and classify the canary as not ready. Use the
application kill switch as an emergency source-level fence only; it is not a
substitute for provider rollback or durable revocation.
