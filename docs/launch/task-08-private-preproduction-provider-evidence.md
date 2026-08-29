# Task 08 private preproduction provider evidence

Status: `PRIVATE_PREPRODUCTION_CANARY_QUALIFIED`
Evidence date (UTC): `2026-08-28`

## Executable source and CI

- Executable commit/head: `8070cc16788a602fde553ad214b628499183b248` on branch `codex/task08-iap-source`; draft PR `#38`.
- Sol-reviewed incremental diff SHA-256: `6849c01f2ef4c6be0d9536f7f0e900c4eae8d3daedd9a90abef0b015b7e57306`; review `APPROVE`, no findings.
- Local focused backend tests: `155/155`. Full backend tests: `527/527`.
- Exact-head CI run [33215376174](https://github.com/delimatsuo/transcriptor/actions/runs/33215376174): `SUCCESS` for backend tests and frontend tests/typecheck/build.

## Immutable provider mappings

### Backend

- Exact HEAD `8070cc16788a602fde553ad214b628499183b248` produced a local deterministic protected/env-excluded source-projection archive with SHA-256 `8046440f8d606288038e3507482b3573c20c16bd6097a29185dec6d2018b7027`. This is a local projection artifact, not a byte-identity claim for the provider transport archive.
- Cloud Build `bcd80777-0cc0-4bdb-b8c2-8b612131bc40` resolved source object `gs://transcriptor-490222_cloudbuild/source/1787954830.394748-cf78dfb030a74170a9f666d56ed26713.tgz#1787954831312407`; provider readback SHA-256 is `f2fe133485c4f5115f9512b604930ebb73527e4bb914664c9b18ea3e79165ce6`. Its path-name audit found 393 nondirectory entries and zero protected or `.env*` path names.
- Selective provider-archive extraction of only `Dockerfile`, `requirements.txt`, and `backend/**` produced exact path-set and byte-content matches to HEAD `8070cc16788a602fde553ad214b628499183b248`. The matched Dockerfile copies only `requirements.txt` and `backend/` into the runtime image. The successful build produced immutable backend image `sha256:4181c9ad2de8631577c98056e349c272aeaa12bbe0540f23c7a342f86eb57f36`.
- Initial exact revision: `tars-backend-staging-task08-8070cc167`. Identical-image live logout recovery: `tars-backend-staging-task08-8070rec1`. Final identical-image kill recovery: `tars-backend-staging-task08-8070rec2`, `Ready`, `100%`, no traffic tag.
- Final controls: native IAP `true`; ingress `all`; runtime service account `tars-runtime@transcriptor-490222.iam.gserviceaccount.com`; timeout `3600`; max scale `1`; session affinity `true`. Service-level `roles/run.invoker` contains exactly the IAP service agent.
- The IAP service agent has no project-level IAM role; this is not by itself a defect because the [IAM service-agent reference](https://docs.cloud.google.com/iam/docs/service-agents) lists its default role as `None`, while [Cloud Run native IAP](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run) requires the service-level binding already present. No `roles/iap.serviceAgent` requirement is claimed.
- Historical backend archive provenance: Cloud Build `1d0f8b3c-f8ad-41bf-8c5f-472cc1d7782b` was `SUCCESS`; its provider source archive SHA-256 was `b7753be1aebf7359d0b1e2a223e12bc052c30559c6fb78d1b3c2f2476c0aff91`, and immutable image digest was `sha256:6275480b917f26325da1c513f93661966926ee6ce1f02ead48b5b6d2cb685a31`. The historical verifier selectively extracted and matched only `Dockerfile`, `requirements.txt`, and `backend/**` (99 files) against exact revision `f831f598157fcdc06066b83ed058259130c7adb2`; it did not read protected or `.env*` paths. This is not a claim that the full provider archive excluded those paths or that the current image was rebuilt from that revision.

### App Hosting

- Current backend `tars-frontend`: rollout/build `build-2026-08-28-006`, rollout `SUCCEEDED`, build `READY`, `reconciling=false`, `100%` traffic, current image tag `build-006`.
- Current custom domain `tars.ellaexecutivesearch.com`: `reconciling=false`, `hostState=HOST_ACTIVE`, `ownershipState=OWNERSHIP_ACTIVE`, `certState=CERT_ACTIVE`.
- The frontend tree object at commit `f1107a68c27afdf5374947765ece6ce04ec14d3a` and at executable head `8070cc16788a602fde553ad214b628499183b248` is identical: `4dbf3cfdaf9ac2d0d3a0c0a815915a61750b7284`.
- That Git tree identity does **not** bind provider build `build-006`; no `build-006` source-archive readback was performed.
- Historical App Hosting archive provenance (not proof of `build-006`): rollout/build `build-2026-08-28-001` was `SUCCEEDED`/`READY` with `reconciling=false`, image `us-central1-docker.pkg.dev/transcriptor-490222/firebaseapphosting-images/tars-frontend:build-2026-08-28-001`, and archive SHA-256 `cd1c657718124f021a67ffdb42741ee6811a61599dd2b146e3861a54c890e4f8`.
- Historical root verifier window `2026-08-28T02:34:53Z..02:34:56Z` used App Hosting REST v1beta source-archive readback, Cloud Storage, unzip, `git archive`, and `diff -qr`; all 84 provider frontend files matched the exact `e1eaa12633d4c62eaa5c71ff18379ce7abe0d1bb` `frontend/` projection, excluding `frontend/AGENTS.md`, `frontend/CLAUDE.md`, and every `.env*` path. The separate `firebase.json` Firebase Tools 14.17.0 schema check and dry run passed.

## Provider, identity, and edge observations

- Google IdP/OAuth is configured in Testing with the authorized operator; live provider sign-in succeeded. No client ID, secret, API key, state, cookie, or token is recorded.
- Current sanitized API-key readback: resource `projects/33726443105/locations/global/keys/e850a2b5-4711-4bfa-b8e9-69f4647db910`, etag `W/"6Opc7P1Znlo0aTrM4zfdzQ=="`, and canonical `{name,etag,restrictions}` SHA-256 `8091d45513ab11b3eb178ce951d010200a77be67fe297c266d6b40f303dc5257`. Key material was not read.
- Exactly four browser referrers are allowed: `https://tars.ellaexecutivesearch.com/*`, `https://tars-frontend--transcriptor-490222.us-central1.hosted.app/*`, `https://transcriptor-490222.firebaseapp.com/*`, and `https://transcriptor-490222.web.app/*`.
- The retained target set contains exactly these 27 APIs: `cloudconfig.googleapis.com`, `datastore.googleapis.com`, `fcmregistrations.googleapis.com`, `firebase.googleapis.com`, `firebaseappcheck.googleapis.com`, `firebaseappdistribution.googleapis.com`, `firebaseapphosting.googleapis.com`, `firebaseapptesters.googleapis.com`, `firebasedatabase.googleapis.com`, `firebasedataconnect.googleapis.com`, `firebasehosting.googleapis.com`, `firebaseinappmessaging.googleapis.com`, `firebaseinstallations.googleapis.com`, `firebaseml.googleapis.com`, `firebaseremoteconfig.googleapis.com`, `firebaseremoteconfigrealtime.googleapis.com`, `firebaserules.googleapis.com`, `firebasestorage.googleapis.com`, `firebasevertexai.googleapis.com`, `firestore.googleapis.com`, `fpnv.googleapis.com`, `identitytoolkit.googleapis.com`, `logging.googleapis.com`, `mlkit.googleapis.com`, `play.googleapis.com`, `securetoken.googleapis.com`, and `sqladmin.googleapis.com`. This broad retained set is not least-privilege proof; narrowing remains a compatibility/security decision.
- Historical pre-enablement IAP-settings evidence, not a current settings readback: tenant IDs `[_33726443105]`; login page origin/path `https://tars.ellaexecutivesearch.com/iap-login`; `allowHttpOptions=true`; API-key parameter present with its value omitted; sanitized canonical hash `306d0ca285531743b9735ca72c8826ed9ff5e0f2fb7955b3028e9ecb8b8c11e1`.
- Authenticated `run.app` `/api/me` returned HTTP 200 at `2026-08-28T22:11:05Z`. Final `https://api.tars.ellaexecutivesearch.com/api/me` authenticated live request returned HTTP 200 at `2026-08-28T22:30:05Z`. Anonymous health on the final host returned HTTP 302 after TLS.
- API edge resources: premium global external managed address `tars-api-ip` = `8.232.19.219`; forwarding rule `tars-api-https`; target proxy `tars-api-https-proxy`; legacy Google-managed certificate `tars-api-cert`; URL map `tars-api-url-map`; backend service `tars-api-backend` with LB IAP unset intentionally because Cloud Run native IAP is authoritative; serverless NEG `tars-api-neg` -> `tars-backend-staging`.
- DNS A for `api.tars` is authoritative and public at `8.232.19.219`. Certificate/domain are `ACTIVE`; expiry `2026-11-26T14:20:34.000-08:00`.
- Direct negative matrix on `run.app` and the final hostname: anonymous requests returned 302; operator developer ID token, spoofed unsigned email header, malformed assertion, and Google ID token used as an IAP assertion each returned 401. Native IAP evaluated before IAM and these requests produced no revision/app log; these are live ingress denials. Local app-level rejection remains supported by exact offline tests, not a live bypass-to-app claim.
- Final CORS: approved-origin preflight returned 200 with exact ACAO and credentials `true`; wrong-origin preflight returned 400 with no ACAO. A credentials header without ACAO does not authorize the origin.

## Authenticated canary and recovery evidence

- Final-host browser WebSocket: create 200, ws-ticket 200, open, ping/pong `true`, clean close 1000, stop 200, delete 200.
- Stream WSS: create 200, ticket 200, open, clean close 1000, stop 200, delete 200.
- Reconnect: two fresh tickets, two opens, pongs, 1000 closes, and cleanup 200. No audio was sent.
- Logout drill: open socket, logout 204, socket 4003, same signed session `/api/me` 401. The terminal synthetic record was deleted after identical-image recovery `...-task08-8070rec1`; readiness then showed all zero and kill `false`.
- Kill drill: open socket, kill 200, socket 4003, `/api/me` 401. Response counts for active sessions, sockets, tickets, stream keys, and provider operations were all zero; kill `true`, ready `false`. Identical-image recovery `...-task08-8070rec2` restored `/api/me` 200, deleted the terminal synthetic record, and readiness returned all zero/kill `false`.
- The 3300-second maximum is schema-capped and exact 3299/3300 behavior is offline-tested and CI-tested. No 55-minute wall-clock soak is claimed.
- Current `rec2` error-level logs: zero observed. No production business/user records, audio, or transcript data was accessed; only the authorized operator identity needed for the authentication canary was observed. Synthetic sessions were cleaned.

## Budget and monitoring

- Owner-authorized alert-only monthly amount: `USD 100`. Budget resource: `billingAccounts/01CA38-F1E90C-E9FD07/budgets/cebe8616-5fa7-4b12-87a9-8cd496017985`; display name `TARS Task 08 private preproduction USD 100`.
- Exact readback: `USD 100`, calendar period `MONTH`, `INCLUDE_ALL_CREDITS`, and the sole project filter `projects/33726443105` (`transcriptor-490222`). Current-spend thresholds are exactly `0.5`, `0.9`, and `1.0`.
- Notifications use exactly `projects/transcriptor-490222/notificationChannels/8720244377476471633`; the enabled channel is type `email` and resolves to `deli@ellaexecutivesearch.com`. Default billing-IAM recipients are disabled. The full budget-resource readback contains no `notificationsRule.pubsubTopic` field.
- Canonical readback projection `{name,displayName,amount,budgetFilter,thresholdRules,notificationsRule}` SHA-256: `1a16278697650e9298cdc0efa06075a94476078af63209db6ba008386eb27c37`. A billing-account list readback found exactly one budget with this display name.
- This is an alerts-only budget, not a spending cap; it does not automatically stop usage or billing. Actual monitoring email delivery remains unproven until a threshold-triggered message is received.

## Evidence ceiling

This ledger is preproduction evidence, not production or merge authorization. Remaining gates are:

- no 55-minute wall-clock soak;
- no provider monitoring email-delivery proof;
- no current App Hosting `build-006` source-archive readback or provider-to-Git source binding;
- no privacy/legal or production authorization/evidence;
- no merge evidence;
- API-key raw values, secrets, and environment values remain uninspected;
- exact provider runtime five-address cardinality and nonempty approved-operator subset remain uninspected where prohibited;
- Next.js compatibility remains empirical where applicable;
- IAM-first revocation and native-IAP-disable rollback have not been tested.

## Rollback fence

Live app kill/drain and same-image fresh-revision recovery were tested. Do not claim IAM-first revocation or native-IAP-disable rollback is tested; keep native IAP enabled.

For a future rollback, require a complete manifest, a reachable tested stop/kill path, and zero-count checks. Fence new entry, invoke the reachable app kill/stop path, drain and verify all five counts are zero, and revoke the IAP-agent invoker only after that drain. Keep native IAP enabled as the independent ingress interceptor until a separately authorized durable fence eliminates or conditions every effective invoker grant, disables service/ingress, or provides another verified equivalent. Verify both unauthenticated and non-IAP authenticated denial before IAP can be disabled. Emergency IAM-first revocation remains unproven safe for existing sockets without an independently tested out-of-band stop.

No production business/user-record content, secret material, token/cookie/state, environment value, audio/transcript, or payload log is included. `deli@ellaexecutivesearch.com` is the sole personal identifier and is recorded only as the approved operator and notification address.
