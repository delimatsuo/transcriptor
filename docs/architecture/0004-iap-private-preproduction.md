# ADR 0004: Private IAP preproduction candidate

Status: source-only, non-authorizing proposal

## Decision

The hosted candidate is a browser-only topology: Firebase App Hosting serves
`https://tars.ellaexecutivesearch.com`, while browser HTTPS and WSS connect
directly to `https://api.tars.ellaexecutivesearch.com`. Cloud Run IAP is the
edge admission layer and the application independently verifies each signed
`x-goog-iap-jwt-assertion` and applies the existing exact-address and
server-derived organization policy. IAP admission is exactly five unique
addresses, all on the `ellaexecutivesearch.com` domain, with the exact
server-derived `AUTH_ORG_ID=ella-internal`; the operator set is nonempty and a
subset of those five addresses.

Application CORS in IAP mode is exactly
`https://tars.ellaexecutivesearch.com`. A future separately authorized IAP
provider configuration must set
`access_settings.cors_settings.allow_http_options=true` so cross-origin
preflight can reach the API; this source task does not configure or prove that
provider setting.

The single-instance provider gate is explicit but unconfigured and unproven in
this source candidate: Cloud Run must use `max_instances=1`, exactly one
serving revision, and 100% unsplit traffic to that revision. Session affinity
or an equivalent routing guarantee is also required because tickets, sessions,
kill state, and readiness counts are process-local. Each of these gates is
`configured=false`, `proven=false`, and `source-authorized=false` here.

The application does not treat unsigned forwarded identity headers or a
Firebase bearer as IAP authority. External-identity data is parsed from the
bounded JSON-string `gcip` claim with duplicate-key rejection; provider
validation requires the nested mapping
`gcip.firebase.sign_in_provider == google.com`, and top-level provider fields
are not accepted. Browser socket
leases close at the absolute 3,300-second bound, on principal logout, or on the
process-local kill latch. Reconnection obtains a new HTTP ticket and a newly
verified assertion.

Terminal logout and the process-local kill latch fence provider and STT work
and begin bounded cancellation before returning; neither route waits
indefinitely. Cancellation-resistant tasks remain in the count-only
`active_provider_operations` readiness category until they actually finish.
Emergency audio abort drops queued chunks and cancels STT rotation/response
work; ordinary user stop keeps the bounded graceful final-response drain.

## Consequences and limits

Current native desktop companions do not have an approved programmatic
Identity Platform/IAP session flow. Their direct socket is therefore denied in
IAP mode and remains unqualified; the browser audio socket is admitted only
when its browser IAP session is present. The runtime gate is a single-instance
application safety layer, not durable provider revocation.

The BRL 250/month figure with 50/90/100 percent notifications is an alert-only
proposal addressed to `deli@ellaexecutivesearch.com`. It is not a hard
spending limit and does not describe provider state. This ADR contains no
deployment, IAM, DNS, budget, quota, or credential authorization.

## Evidence boundary

Source review, synthetic fixtures, and offline tests can validate control flow
and mutation-effective guards. They cannot prove Identity Platform, IAP, IAM,
DNS, TLS, cookies, custom domains, deployed WebSockets, budgets, quotas,
provider billing, privacy/legal requirements, physical-device behavior,
audible quality, or production readiness.
