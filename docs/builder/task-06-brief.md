# Task 06 — Bind native frames to their route and make the hosted pilot source-ready

Read `docs/builder/README.md` first. Repo: `"/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"` (quote the path). The exact prerequisite commit is `7133634`: browser microphone and macOS companion connections now use keyless native-stream URLs, `tars-stream` subprotocol authentication, and hello-first application traffic; the backend query-key fallback is gone.

This task has two tightly related deliverables:

1. reject every binary audio frame whose JSON header does not carry the exact `session_id` from the authenticated WebSocket route, before frame-derived state or audio delivery can occur;
2. make the checked-in container/configuration a truthful **source-ready** Cloud Run pilot candidate while leaving every real cloud, Firebase, credential, provider, signing, audio, and deployment action to the owner/designer gates in later tasks.

This is a source/test/documentation task only. Do not use Git. Do not run the live audio harness. Do not contact GCP, Firebase, STT, Gemini, Apple, or any deployed service.

## Exact file plan

Modify only:

- `Dockerfile`
- `.env.example`
- `frontend/.env.example`
- `backend/config.py`
- `backend/main.py`
- `backend/storage/gcs.py`
- `backend/tests/test_native_stream_endpoint.py`
- `frontend/src/components/CompanionCommand.tsx`
- `scripts/verify_live_system_audio.py`

Create only:

- `.dockerignore`
- `backend/tests/test_cloud_run_readiness.py`
- `docs/launch/cloud-run-pilot-source-readiness.md`
- `docs/builder/task-06-report.md`

Do not touch workflows, Firebase/Cloud Run manifests, deployment sentinels, `DEPLOY-SETUP.md`, the old hosted-gate checklist, Windows, Swift, any generated evidence, or the three protected untracked instruction files (`AGENTS.md`, `frontend/AGENTS.md`, `frontend/CLAUDE.md`). Do not run any Git command, including read-only Git commands.

## Current source facts and boundaries

- `backend/main.py::native_stream_endpoint` authenticates the path session and stream key before `accept()`, but after JSON header decode it immediately reads `source`, mutates source ownership/health, advances dedup state, creates a `StreamManager`, and forwards PCM. It never validates `header["session_id"]`.
- The target protocol in `docs/architecture/0002-companion-stream-protocol.md` requires session identity on every event, but it uses the target-style name `sessionId`. This task hardens the **current legacy gateway frame format**, whose production encoders use the exact JSON key `session_id`; do not rename the current wire field.
- Browser and Swift production encoders already write the active route session ID. Windows frame serialization does too, but Windows connection auth/hello remains incompatible and is out of scope.
- The canonical live-proof harness is stale: `encode_frame()` hard-codes `"session_id": ""`. Strict server validation will break its microphone stream unless the harness is migrated offline in this task.
- `Dockerfile` honors `PORT` and runs as non-root, but its top comment still declares a prototype, it lacks the PortAudio runtime needed by the import-time `sounddevice` dependency, and its shell does not `exec` Uvicorn.
- No `.dockerignore` exists.
- `/healthz` is an unauthenticated liveness response. Uvicorn lifespan performs ADC and service initialization before normal serving, but the source has no explicit readiness state/endpoint.
- CORS is hard-coded to localhost plus one extension origin, so a Firebase Hosting origin cannot be admitted through configuration.
- `GCSStorage` derives a bucket name and attempts to create the bucket during a request. The hosted runtime identity must not need bucket-creation authority; Task 08 will pre-create and bind the bucket.
- `.env.example` and `frontend/.env.example` omit hosted safety/configuration fields. Frontend production modules have localhost fallbacks, so Task 08 must use an explicit clean hosted build environment.
- `CompanionCommand.tsx` still renders and copies a terminal command containing `--stream-key <key>`. The pilot-approved deep link may carry its session-scoped key, but a visible banner/terminal command may not. Remove this residual Task-05b exposure here.
- Cloud Run service timeout, instance count, ingress, IAM, probes, and Firebase Hosting are service configuration. Do **not** pretend the Dockerfile configures them, and do not create a deployment manifest in this task.

## 1. Mandatory native-frame session binding

In `native_stream_endpoint`, immediately after a binary frame's length checks and successful UTF-8/JSON decode:

1. Require the decoded header to be a JSON object/dict.
2. Require `header.get("session_id")` to be a string.
3. Require that string to equal the route parameter `session_id` exactly.

This gate must run before all frame-derived behavior, including:

- reading/defaulting `source`;
- `_mark_owned` or `_set_source_health`;
- `last_frame_at` or `intended_since` mutation;
- clearing an alert or emitting frame-triggered health;
- `_is_duplicate_frame` or `native_frame_last_seq` mutation;
- `StreamManager` lookup/construction/start;
- PCM forwarding.

The already-authenticated connection's connect-time `connections` count/initial health emission and the existing `finally` disconnect cleanup remain intact. “No side effects” below means no **frame-derived** ownership, source-health, dedup, manager, or audio effect; normal accepted-connection cleanup is expected.

On a missing, non-string, or non-object header identity:

```python
logger.warning(
    "native_stream_frame_rejected",
    session_id=session_id,
    reason="missing_session_id",
)
```

On a string that differs from the path:

```python
logger.warning(
    "native_stream_frame_rejected",
    session_id=session_id,
    reason="mismatched_session_id",
)
```

Then call `await websocket.close(code=1008)` with no reason text and return from the connection. Never log the supplied header ID, full header, source, sequence, payload, or audio-derived data. Do not echo any untrusted value in the close.

Preserve existing handling for too-short packets, incomplete declared headers, and malformed JSON: they continue to be ignored. Do not add general framing/source validation or change hello, gap, ping, watchdog, dedup, STT, stop, reconnect, or health semantics.

### Endpoint tests — write first and capture RED

Add these focused tests to `backend/tests/test_native_stream_endpoint.py`:

1. `test_native_stream_rejects_frame_missing_session_id_before_side_effects`
   - Cover an omitted field and at least one non-string/non-object form without weakening the exact contract.
   - Authenticate and accept the socket, send the bad binary frame, then assert close `1008`.
   - Assert one exact `native_stream_frame_rejected` warning with only trusted route ID plus fixed reason.
   - Assert no `StreamManager` construction/start/send, no dedup entry for that session, and neither source becomes owned/healthy. Account for normal connect/disconnect health emissions.
2. `test_native_stream_rejects_frame_session_id_mismatch_before_side_effects`
   - Header carries a different string. Assert the same close/no-frame-effects guarantees with `mismatched_session_id`.
   - Include a distinctive attacker-controlled value and assert it appears in neither logger call arguments/kwargs nor close data.
3. `test_native_stream_accepts_frame_session_id_matching_path`
   - Exact matching ID, complete valid frame. Assert normal source health, dedup baseline, one manager start, and one PCM forward; no policy close.

Update every existing intended-valid binary-frame fixture to carry its exact route ID. In particular, the current dual-source routing test has two headers with no `session_id`. Do not “fix” intentionally short or malformed-JSON fixtures.

## 2. Canonical live-proof harness compatibility, without live execution

In `scripts/verify_live_system_audio.py`:

- Change `encode_frame` to take the session ID explicitly and serialize it, e.g. `encode_frame(session_id, source, sequence, first_sample, pcm)`.
- `MicChannel` must retain its route session ID and pass it for every speech and silence frame.
- `_probe_invalid_key` must also build its fallback probe frame with the same path session ID. Authentication should reject before frames, but the fallback probe must still be protocol-valid if a proxy/server accepts the handshake before issuing `1008`.
- Update every call site. There must be no canonical-harness `"session_id": ""` residue.
- Preserve phase names, outputs, timing, ADC/TCC isolation, real-audio behavior, evidence paths, subprotocol auth, and hello-first ordering.
- Do not run the script. The only permitted harness gate is `py_compile` plus source inspection.

## 3. Explicit and fail-closed CORS configuration

Move CORS-origin parsing into small pure functions in `backend/config.py` and consume the result when adding `CORSMiddleware` in `backend/main.py`.

Required contract:

- If `CORS_ALLOWED_ORIGINS` is **absent**, preserve exactly today's five local origins (localhost/127.0.0.1 on ports 3000/3003 plus the existing extension origin).
- If the variable is present, split its comma-separated exact origins, trim surrounding whitespace, normalize only a single trailing `/`, deduplicate while preserving order, and use that list instead of merging in local defaults.
- An explicitly blank value fails startup/config parsing. `*` or any wildcard fails. Reject credentials/userinfo, query, fragment, and non-root paths.
- Accept only valid `http://`, `https://`, and `chrome-extension://` origins with a host/extension ID. Do not log or silently discard invalid entries.
- Keep `allow_credentials=True`; that is why wildcard origins are prohibited.
- Do not instantiate the full `Settings` object at module import merely to configure middleware; `GOOGLE_CLOUD_PROJECT` remains a lifespan/runtime requirement. Define a narrow `.env`-aware `BaseSettings` reader containing only `cors_allowed_origins`, plus a separately testable `parse_cors_allowed_origins(raw: str | None)` pure function. Instantiate only that narrow reader when supplying `allow_origins` to `app.add_middleware`, so a copied local `.env` works without triggering the full application settings contract. Direct process environment must retain normal precedence over `.env`.

Tests in `backend/tests/test_cloud_run_readiness.py` must prove exact local defaults, hosted-origin replacement/normalization/deduplication, and fail-closed blank/wildcard/path/query/credential cases. Preserve the existing auth-401 CORS test.

## 4. Liveness and readiness truth

Keep `/healthz` as an unauthenticated, dependency-free liveness endpoint returning the existing `{"status":"ok"}`.

Add explicit application readiness state and `GET /readyz`:

- set `app.state.ready = False` immediately after the `FastAPI` app is created;
- set the lifespan argument's `application.state.ready` false at lifespan entry;
- set it true only after settings, ADC, Firebase Admin, session manager, Firestore/GCS adapters, Gemini client, context state, and orphan detection have initialized successfully, immediately before yielding;
- set it false before shutdown cleanup starts;
- have `/readyz` accept/read `request.app.state.ready`; do not add a module-level readiness boolean;
- `/readyz` returns HTTP 503 with a fixed content-free `{"status":"not_ready"}` while false, and HTTP 200 with `{"status":"ready"}` while true;
- neither health endpoint is under `/api/`, bearer auth, provider access, or data access.

Tests must prove false-before-start, true only inside a fully initialized mocked lifespan, false-after-shutdown, the two response codes/bodies, and no provider/network call from either route. For the pre-start/no-provider route checks, use direct ASGI/no-lifespan invocation (or direct route-function calls with a synthetic request); do not accidentally start the real lifespan through `TestClient(main.app)`. Test the ready transition separately under a fully mocked lifespan. Extend the existing lifespan test only if necessary; do not weaken its ADC-before-readiness assertion.

Task 08 will use `/readyz` for the Cloud Run HTTP startup probe and `/healthz` for liveness. Do not configure or call those service probes here.

## 5. Least-privilege bucket binding

Add optional `GCS_BUCKET_NAME` support to `Settings`:

- blank/absent preserves the compatibility default `<GOOGLE_CLOUD_PROJECT>-tars`;
- a supplied value is trimmed, must be a bare bucket name (not `gs://...`, no slash/path, no whitespace), and is retained exactly after validation.

`GCSStorage` must use the configured value or compatibility default, but `_get_bucket()` must only obtain the bucket handle. Delete the `bucket.exists()` / `client.create_bucket()` mutation and the `gcs_bucket_created` log. A missing/misbound bucket must fail naturally at the attempted object operation; the hosted runtime must never create infrastructure during an interview request.

Offline tests must prove the override/default selection and prove `create_bucket`/`exists` are never called. No GCS client may reach the network.

## 6. Container build-context and process contract

Update `Dockerfile` so its comments truthfully say this is the backend image source for the single-instance hosted pilot, while deployment remains blocked until Task 07/08 gates. Preserve Python 3.12, `/app`, non-root UID 1001, `EXPOSE 8080`, and `${PORT:-8080}`.

Also:

- install both `libsndfile1` and `libportaudio2`; `backend.main` imports legacy `sounddevice` code at process startup even though hosted capture is disabled;
- set safe image defaults `HOST_AUDIO_CAPTURE_ENABLED=false` and `AUDIO_BACKUP_ENABLED=false`, plus normal unbuffered/no-bytecode Python runtime settings;
- run one Uvicorn process with no reload and no extra workers because all pilot session/key/WS state is process-local. This requirement applies to the Docker `CMD` only; leave the intentional local-development `backend.main.main()` entry point and its `reload=True` unchanged;
- use the shell only for `${PORT:-8080}` expansion and `exec python -m uvicorn ...` so Uvicorn receives termination signals directly;
- do not encode Cloud Run timeout, min/max instances, IAM, ingress, credentials, or project IDs in the image.

Create a whitelist-style `.dockerignore` whose transferred application inputs contain only `Dockerfile`, `requirements.txt`, runtime `backend` package directories, and runtime Python source (`.dockerignore` itself is unavoidable Docker metadata). Use this ordered shape or a byte-for-byte equivalent with the same semantics:

```dockerignore
**
!Dockerfile
!requirements.txt
!backend/
!backend/**/
!backend/**/*.py
backend/tests/
backend/tests/**
```

The ignore-all rule also excludes caches, bytecode, `.git`, every `.env*`, `.venv`, frontend, companion, docs, root scripts, recordings, artifacts, protected instruction files, and local credentials. Keep the final test exclusions after the Python re-inclusions so `backend/tests/**` cannot re-enter the context.

Add offline static tests for the required system libraries, safe defaults, exec-form process contract, Docker-CMD-only no-reload/no-workers, non-root user, ordered whitelist behavior, and intended exclusion of tests/env/local material. Without a successful offline Docker build, describe these as source-rule checks rather than proof of Docker's resolved build context.

## 7. Environment examples and source-readiness document

Update `.env.example` without placing real credentials or the final Task-07 cohort allowlist in it. It must explicitly document safe values/meaning for at least:

- `GOOGLE_CLOUD_PROJECT`
- `FIREBASE_PROJECT_ID`
- placeholder `AUTH_ALLOWED_EMAILS`
- `AUTH_ORG_ID=ella-internal`
- `AUTH_BYPASS=false`
- `CORS_ALLOWED_ORIGINS` (local example here; hosted exact origins are in the readiness doc)
- `GCS_BUCKET_NAME`
- `EXTENSION_ENABLED=false`
- `HOST_AUDIO_CAPTURE_ENABLED=false`
- `AUDIO_BACKUP_ENABLED=false`
- `LLM_LOCATION`, `LLM_REQUEST_TIMEOUT_SECONDS`
- `STT_LOCATION`, `STT_GRACEFUL_DRAIN_TIMEOUT_SECONDS`
- local `FASTAPI_HOST` / `FASTAPI_PORT`
- the existing inert retention warning
- local frontend `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`, and `NEXT_PUBLIC_WS_STREAM_URL`
- `NEXT_PUBLIC_AUTH_BYPASS=0`

Correct the stale claim that REST is hard-coded. Do not instruct a hosted runtime to use `gcloud auth application-default login` or a downloaded service-account JSON key.

Update `frontend/.env.example` to include all Firebase public fields plus explicit local values for:

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
NEXT_PUBLIC_WS_STREAM_URL=ws://localhost:8000/api/stream/native
NEXT_PUBLIC_AUTH_BYPASS=0
```

Create `docs/launch/cloud-run-pilot-source-readiness.md`. It is a non-authorizing source contract and Task-08 gate, not a deployment run record. It must clearly separate:

### Proven by this task

- frame/path binding source tests;
- explicit CORS parsing;
- `/healthz` liveness and `/readyz` readiness semantics;
- non-root, signal-correct, one-process image source with required import-time audio libraries;
- least-privilege pre-existing bucket behavior;
- build-context containment;
- explicit backend/frontend environment contract.

### Still required in Task 07

- verify the exact five real Firebase/Google accounts before placing the named allowlist into the runtime;
- prove `AUTH_BYPASS=false` admission and sign-in UX locally.

### Owner/designer-only Task 08 gates

- bind the exact committed SHA, GCP/Firebase project, owner account, runtime service identity, allowed mutation set, and rollback;
- pre-create/verify the exact GCS bucket and grant only required object access; never use a JSON service-account key;
- deploy Cloud Run with exactly one Uvicorn process, `min=1`, `max=1`, request timeout `3600` seconds, HTTP startup probe `/readyz`, liveness probe `/healthz`, and no end-to-end HTTP/2 WebSocket configuration;
- set `AUTH_BYPASS=false`, exact verified allowlist, project/org/region/bucket/CORS values, host capture false, audio backup false, and no local `.env` leakage;
- build Firebase Hosting in a clean environment with explicit `https://<cloud-run-host>`, `wss://<cloud-run-host>/ws`, `wss://<cloud-run-host>/api/stream/native`, Firebase public config, and `NEXT_PUBLIC_AUTH_BYPASS=0`;
- execute and record `docs/launch/week-4-hosted-gate-checklist.md`, including allowed/denied-account smoke, cross-owner behavior, path/header mismatch, health probes, ticket renewal/reconnect, TLS/ingress/IAM, and exact revision/traffic evidence.

### Known pilot limitations

- Cloud Run WebSockets are requests and are subject to the configured request timeout; clients must reconnect. Link the official Cloud Run WebSocket and request-timeout docs.
- `3600` seconds is Cloud Run's maximum request timeout, not evidence of uninterrupted interviews beyond that boundary. The macOS sink reconnects; browser microphone capture currently does not auto-reconnect. Do not claim more than the source proves.
- `min=max=1` avoids cross-instance routing but does not make process-local state durable. A restart loses in-memory sessions, keys, and socket state; the approved user guidance remains “if reconectando lasts more than 2 minutes, restart the session.”
- session affinity is not a durability guarantee; broad tenancy/multi-instance state is out of scope.
- `DATA_RETENTION_DAYS=90` remains inert and is not a deletion guarantee.

Use these authoritative references in the document:

- `https://cloud.google.com/run/docs/triggering/websockets`
- `https://cloud.google.com/run/docs/configuring/request-timeout`
- `https://cloud.google.com/run/docs/configuring/healthchecks`
- `https://cloud.google.com/run/docs/container-contract`

Do not copy stale project IDs, service accounts, WIF providers, or commands from deployment/rollback files. Do not modify those files.

## 8. Remove residual visible stream-key command

In `frontend/src/components/CompanionCommand.tsx`, remove the expandable terminal command, clipboard state/handler, and visible `--stream-key` string entirely. Preserve the primary `tars-companion://join?...` anchor and pt-BR onboarding pointer; the session-scoped deep-link key exposure remains explicitly pilot-accepted by the recruiter-phase design. Do not log or display the deep link itself. Simplify unused React imports after the removal.

Add an offline source-contract assertion in `test_cloud_run_readiness.py` proving the component no longer contains `--stream-key`, a clipboard write of the command, or “Método alternativo (terminal)”. Do not add a frontend test framework.

## TDD and verification

Write the new/changed tests first. Run and record genuine RED failures before implementation. Then implement and run all of these offline gates:

```bash
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py backend/tests/test_cloud_run_readiness.py -q
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests -q
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend" && npm test
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend" && NEXT_PUBLIC_API_URL=https://backend.invalid NEXT_PUBLIC_WS_URL=wss://backend.invalid/ws NEXT_PUBLIC_WS_STREAM_URL=wss://backend.invalid/api/stream/native NEXT_PUBLIC_AUTH_BYPASS=0 npm run build
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift test
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift build
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m py_compile backend/config.py backend/main.py backend/storage/gcs.py scripts/verify_live_system_audio.py
```

Baselines before this task: native endpoint `36`, full backend `301`, frontend `64`, Swift `79`. The three mandatory frame tests must increase the endpoint count; readiness tests must increase the full backend count; all suites must have zero failures. Next production build and Swift build must succeed.

Docker verification is optional and strictly offline. First check whether a daemon is available. Only if it is available **and** dependencies/base layers are already cached, run:

```bash
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && docker build --pull=false --network=none -t transcriptor-backend:task06 .
```

If the daemon/cache is unavailable, skip the build and report that exact limitation; do not fetch images or dependencies and do not treat static/source checks as a built-container proof.

Also run non-Git source scans proving:

- no production native-frame encoder writes an empty session ID;
- the server gate precedes source health, dedup, manager, and audio behavior;
- no `bucket.exists`, `create_bucket`, or `gcs_bucket_created` remains in production GCS storage;
- no visible terminal `--stream-key` remains in `CompanionCommand.tsx`;
- no wildcard CORS or hosted local-bypass example was introduced;
- the Docker context allowlist excludes env files, tests, caches, docs, frontend, companion, recordings, credentials, and protected instructions.

Do not run `git status`, `git diff`, `git diff --check`, or any other Git command. The designer performs all Git gates.

## Out of scope and prohibited actions

- No `gcloud`, Firebase CLI, cloud/project/IAM/ingress/traffic/timeout/probe mutation, deployment, hosting, or rollback.
- No live HTTP/WebSocket/provider/storage/STT/Gemini call.
- No execution of `scripts/verify_live_system_audio.py`, audio capture, TCC prompt, device test, signing, notarization, packaging, or Apple action.
- No real pilot-email allowlist yet (Task 07 validates the exact accounts).
- No Firebase Hosting files or Cloud Run service manifest.
- No frontend localhost-fallback refactor, browser audio auto-reconnect, durable session state, multi-instance support, or broad tenancy.
- No change to deep-link key transport, Windows, Swift, protocol framing beyond path/header identity, malformed-packet behavior, hello/gap semantics, STT, dedup, session cleanup, or data-retention behavior.
- No claim that source tests prove a container build, deployed revision, TLS, IAM, runtime identity, provider behavior, live audio, physical device behavior, hour-long continuity, production security, or deletion.

## Report

Write `docs/builder/task-06-report.md` with:

- every file changed/created and confirmation that it matches the allowlist;
- exact RED commands and failure excerpts before implementation;
- exact GREEN commands, counts, and build outputs;
- frame rejection ordering/log/close/no-side-effect evidence;
- live-harness offline migration evidence and explicit confirmation it was not run;
- CORS, readiness, bucket, Dockerfile, `.dockerignore`, env-example, and banner-key evidence;
- Docker build result or exact daemon/cache skip;
- explicit confirmation that no Git, cloud, Firebase, provider, credential, signing, device, live-network, live-audio, or deployment action ran;
- anything skipped, uncertain, or weaker than requested.

Do not commit. Stop with the working tree ready for designer verification.
