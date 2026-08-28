# Task 08 private preproduction provider evidence

Status: `PROVIDER_PARTIAL_NON_AUTHORIZING`
Evidence date (UTC): `2026-08-28`

## Source and CI

- Exact frontend source head: `e1eaa12633d4c62eaa5c71ff18379ce7abe0d1bb`.
- Draft PR: `#38`.
- Exact-head CI run `33134512468` succeeded, including frontend tests, typecheck, and build, plus backend tests.

## App Hosting

- Backend: `tars-frontend`.
- Immutable mapping: rollout `projects/transcriptor-490222/locations/us-central1/backends/tars-frontend/rollouts/build-2026-08-28-001` is `SUCCEEDED` with `reconciling=false` and references build `projects/transcriptor-490222/locations/us-central1/backends/tars-frontend/builds/build-2026-08-28-001`; the build is `READY` with `reconciling=false` and image `us-central1-docker.pkg.dev/transcriptor-490222/firebaseapphosting-images/tars-frontend:build-2026-08-28-001`.
- Root verifier (Codex), `2026-08-28T02:34:53Z..02:34:56Z`, used App Hosting REST v1beta source-archive readback, Cloud Storage, unzip, `git archive`, and `diff -qr`; all 84 provider frontend source files matched byte-for-byte against the exact `e1eaa12633d4c62eaa5c71ff18379ce7abe0d1bb` `frontend/` projection: `PASS`. The comparison explicitly excluded `frontend/AGENTS.md`, `frontend/CLAUDE.md`, and every `.env*` path.
- Provider archive SHA-256: `cd1c657718124f021a67ffdb42741ee6811a61599dd2b146e3861a54c890e4f8`.
- `firebase.json` was deployment configuration, not part of the provider frontend source archive; its Firebase Tools 14.17.0 schema check and dry run passed separately.
- Hosted visible-text probe at `https://tars-frontend--transcriptor-490222.us-central1.hosted.app/iap-login`, `2026-08-28T02:39:21Z`: HTTP 200, visible title/checking `true`, visible not-found `false`. This proves no hydration or authentication claim.
- App Hosting aggregate logs filtered to `cloud_run_revision` / `service_name=tars-frontend`, window `2026-08-28T02:03:59Z..02:38:22Z`, revision `tars-frontend-build-2026-08-28-001`: 29 metadata entries, severities `DEFAULT5 INFO6 NOTICE18`, HTTP 200 count 2, `ERROR`-or-higher count 0.

## Backend immutable mapping

- Cloud Build ID `1d0f8b3c-f8ad-41bf-8c5f-472cc1d7782b`: `SUCCESS`; create/start/finish `2026-08-28T00:32:47.011681674Z` / `2026-08-28T00:32:47.621368766Z` / `2026-08-28T00:34:31.517892Z`.
- Resolved source object: bucket `run-sources-transcriptor-490222-us-central1`, object `services/tars-backend-staging/1787877165.740049-cf0cc03d89dc4d2cb02d2d2f55a8a445.zip`, generation `1787877166409226`; provider source archive SHA-256 `b7753be1aebf7359d0b1e2a223e12bc052c30559c6fb78d1b3c2f2476c0aff91`.
- Root verifier, `2026-08-28T02:34:28Z..02:34:30Z`, selectively extracted/read only `Dockerfile`, `requirements.txt`, and `backend/**` (99 files), never protected or `.env*` contents; `diff -qr` matched exact `f831f598157fcdc06066b83ed058259130c7adb2`.
- Cloud Build results bind immutable image digest `sha256:6275480b917f26325da1c513f93661966926ee6ce1f02ead48b5b6d2cb685a31`; revision `tars-backend-staging-task08-f831f598b` resolves to that digest. Backend, `Dockerfile`, and `requirements.txt` Git objects are identical at final head `e1eaa12633d4c62eaa5c71ff18379ce7abe0d1bb`, proving final-head input equivalence rather than a rebuild at `e1`.
- Revision `tars-backend-staging-task08-f831f598b`: Ready, 100%; timeout 3600; max instances 1; affinity true; no tags.
- Backend aggregate log metadata, exact revision filter, window `2026-08-28T00:36:47Z..02:39:50Z`: 13 entries, severities `DEFAULT7 INFO3 WARNING3`, HTTP 401/403/404 count 1 each, `ERROR`-or-higher count 0. Warning statuses are probe metadata only; no payloads were inspected.

## IAM, IAP, and API-key controls

- Explicit service-level `roles/run.invoker` binding has exactly `serviceAccount:service-33726443105@gcp-sa-iap.iam.gserviceaccount.com`. The IAP service agent has no project-level IAM role; this is not by itself a defect because the [IAM service-agent reference](https://docs.cloud.google.com/iam/docs/service-agents) lists its default role as `None`, while [Cloud Run native IAP](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run) requires the service-level `roles/run.invoker` binding already present.
- No project- or organization-level `roles/run.invoker` binding was found; the project parent is organization `558436851610` with no folder. However, effective `run.routes.invoke` is not proven service-agent-only before native IAP: sanitized role-definition audit found project bindings `roles/aiplatform.serviceAgent`, `roles/editor`, `roles/firebaseapphosting.serviceAgent`, `roles/owner`, `roles/run.admin`, and `roles/run.serviceAgent`, each granting `run.routes.invoke`; no `allUsers`/`allAuthenticatedUsers` and no organization-level granting role were found. Other principals are omitted.
- Timestamped direct probe `2026-08-28T02:40:09Z..02:40:11Z`: unauthenticated nonexistent path 403; approved-operator ID token nonexistent path 404 (request reached the app); approved-operator ID token `/api/me` 401 (app fail-closed without IAP assertion). Direct authenticated IAM-bypass denial is **NOT PROVEN** until native IAP intercept is enabled and retested. Invoker IAM check is true; service IAP remains disabled.
- IAP settings: `tenantIds: [_33726443105]`; login page origin/path `https://tars.ellaexecutivesearch.com/iap-login`; API-key parameter present but its value omitted; `allowHttpOptions: true`. Canonical settings SHA-256 `306d0ca285531743b9735ca72c8826ed9ff5e0f2fb7955b3028e9ecb8b8c11e1`; service IAM hash `0c877eea66a348383260d66eb8beabcf36375a8b199c5fb3a5a70566a4b2e2d0`; service safety projection hash `6ddb76877d4318ed6b8f912816b840d79c177666fcf8192c9540acf9e5dfb844`.
- API-key audit resource `projects/33726443105/locations/global/keys/e850a2b5-4711-4bfa-b8e9-69f4647db910`, etag `W/"6Opc7P1Znlo0aTrM4zfdzQ=="`, Secret Manager reference `tars-firebase-web-api-key@1`, restrictions hash `ff9509546ed7304243c1588466759c691e058a5dc8de4c2ea1e8defef9cbaf04`. The raw key is omitted.
- Exactly four approved referrers are configured, each as an exact HTTPS origin with path wildcard `/*`: `tars.ellaexecutivesearch.com`, `tars-frontend--transcriptor-490222.us-central1.hosted.app`, `transcriptor-490222.firebaseapp.com`, and `transcriptor-490222.web.app`. There are no host, scheme, or port wildcards.
- The retained Firebase auto-key target set contains exactly these 27 Google APIs: `cloudconfig.googleapis.com`, `datastore.googleapis.com`, `fcmregistrations.googleapis.com`, `firebase.googleapis.com`, `firebaseappcheck.googleapis.com`, `firebaseappdistribution.googleapis.com`, `firebaseapphosting.googleapis.com`, `firebaseapptesters.googleapis.com`, `firebasedatabase.googleapis.com`, `firebasedataconnect.googleapis.com`, `firebasehosting.googleapis.com`, `firebaseinappmessaging.googleapis.com`, `firebaseinstallations.googleapis.com`, `firebaseml.googleapis.com`, `firebaseremoteconfig.googleapis.com`, `firebaseremoteconfigrealtime.googleapis.com`, `firebaserules.googleapis.com`, `firebasestorage.googleapis.com`, `firebasevertexai.googleapis.com`, `firestore.googleapis.com`, `fpnv.googleapis.com`, `identitytoolkit.googleapis.com`, `logging.googleapis.com`, `mlkit.googleapis.com`, `play.googleapis.com`, `securetoken.googleapis.com`, and `sqladmin.googleapis.com`. This broad retained set is not least-privilege proof; narrowing remains a compatibility/security decision.

## Remaining provider gates

- **NOT PROVEN / NOT RUN:** Google IdP, OAuth, and redirect behavior; DNS/TLS domains; native IAP enablement and IAP-through-protected-request behavior; direct authenticated bypass after IAP; exact five-address provider runtime cardinality/domain and nonempty approved-operator subset (values intentionally uninspected); server organization binding; live signed IAP issuer/audience/nested Google provider claim; all five readiness counts and kill-inactive behavior; CORS `OPTIONS` exact/wrong-origin behavior; live affinity/restart behavior; browser/WSS/reconnect/3300-second/logout/kill behavior; quota state; monitoring-channel delivery (only resource existence and address match are recorded); budget; complete rollback manifest and tested stop path; privacy/legal, production, and merge.
- DNS records are absent. Frontend domain ownership is missing; the certificate is validating and the host is unhosted. API certificate provisioning is `FAILED_NOT_VISIBLE`. Google IdP GET remains HTTP 404 and is not configured. No authenticated browser or WebSocket canary has run.
- Budget was not created because billing currency is USD while the approved amount is BRL 250.
- Monitoring email channel exists at `projects/transcriptor-490222/notificationChannels/8720244377476471633`; approved email is `deli@ellaexecutivesearch.com`. Delivery is not proven.

## Evidence ceiling and rollback fence

This ledger is non-authorizing. Next.js 16.3 provider-build evidence is empirical compatibility only because the App Hosting official support matrix ends at Next 15.2.x. No Google provider/login, DNS/TLS/custom-domain, native-IAP enablement, authenticated browser/WSS, kill-switch drill, budget, privacy/legal, production, or merge evidence is claimed.

Current partial-state rollback: IAP is disabled and no authenticated canary is active. Removing the new IAP-agent `roles/run.invoker` binding removes only that principal; provider deny-all and non-IAP authenticated denial remain **NOT PROVEN** because other project roles grant `run.routes.invoke`. Anonymous HTTP 403 and direct approved-operator `/api/me` HTTP 401 are separate anonymous/application fail-closed observations, not provider authenticated-denial evidence. No active-session zero-count claim exists.

Before any future IAP-enabled canary, require a complete manifest, a reachable tested stop/kill path, and zero-count checks. Normal rollback is to fence new entry, invoke the reachable app kill/stop path, drain and verify all five counts are zero, revoke the IAP-agent invoker, then **keep native IAP enabled** as the independent ingress interceptor until a separately authorized durable fence eliminates or conditions every effective invoker grant, disables the service/ingress, or provides another verified equivalent. Verify both unauthenticated denial and non-IAP authenticated denial before IAP can be disabled. Emergency IAM-first revocation remains **NOT PROVEN** safe for existing sockets without an independently tested out-of-band stop.

No API key string, OAuth secret or token, unapproved allowlist email, environment value, audio/transcript/user data, or payload log is included.
