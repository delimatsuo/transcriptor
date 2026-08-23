# Cloud Run Pilot Source Readiness & Contract

This document specifies the source-ready configuration contract and gate separation for the single-instance hosted T.A.R.S. pilot candidate. It serves as a non-authorizing architecture contract and gate checklist for Task 07 and Task 08.

---

## 1. Proven by this task (Task 06)

The following components and contracts have been verified offline with automated test suites:

- **Frame/Path Binding Gate**: Every binary audio frame received at `/api/stream/native/{session_id}` requires a JSON header containing `session_id` matching the route parameter exactly. Missing, non-string, or mismatched IDs trigger a 1008 WebSocket policy close without side effects on source health, dedup state, or audio processing.
- **Fail-Closed CORS Parsing**: `CORS_ALLOWED_ORIGINS` is parsed strictly as ASCII. If absent, exact local defaults are preserved. If provided, wildcards, non-root paths, query parameters, fragments, credentials, internal whitespace, backslashes, percent encoding, control characters, empty port delimiters, invalid ports, non-IPv6 bracketed hosts, and non-canonical extension IDs are rejected at config time without echoing sensitive input.
- **Liveness (`/healthz`) & Readiness (`/readyz`) Semantics**: `/healthz` provides unauthenticated, zero-dependency liveness. `/readyz` returns HTTP 503 (`{"status":"not_ready"}`) until lifespan initialization (ADC, Firebase Admin, Firestore, GCS, Gemini) completes, returning HTTP 200 (`{"status":"ready"}`) during normal serving, and resetting to 503 upon shutdown. Verified via direct ASGI route requests without running lifespan.
- **Single-Process Container Contract**: `Dockerfile` is configured for non-root execution (`appuser` UID 1001), installs required audio libraries (`libsndfile1`, `libportaudio2`), defines safe non-capture environment defaults, contains no overriding `ENTRYPOINT`, and runs a single Uvicorn process via `exec` without `--reload` or `--workers`.
- **Least-Privilege Storage Binding**: `GCSStorage` binds directly to `GCS_BUCKET_NAME` or the `<GOOGLE_CLOUD_PROJECT>-tars` default without inspecting bucket existence or attempting bucket creation during request handling.
- **Build Context Containment**: `.dockerignore` establishes a checked-in source-rule contract isolating the build context to application sources only, excluding credentials, environment files, tests, documentation, caches, frontend, companion, and local scratch files. (Note: Docker image build was skipped because the daemon was unavailable; static source-rule assertions are verified in test suites rather than representing a resolved container build proof).
- **Clean Frontend & Backend Environment Contracts**: `.env.example` and `frontend/.env.example` explicitly define the required pilot configuration parameters (`AUTH_BYPASS=false`, `NEXT_PUBLIC_AUTH_BYPASS=0`, root email placeholder, test-only bypass warning, zero credential paths). Visible `--stream-key` exposure and terminal copy commands have been removed from `CompanionCommand.tsx`.

---

## 2. Still required in Task 07

- **Account Allowlist Verification**: Verify the exact five real Firebase/Google accounts for the pilot cohort before placing the named email allowlist into runtime configuration.
- **Authentication Bypass Verification**: Prove local authentication flows and recruiter sign-in UX under `AUTH_BYPASS=false` with real Firebase token verification.

---

## 3. Owner/Designer-Only Task 08 Gates

All real cloud, credential, and deployment mutations are gated to Task 08 and must be executed by the repository owner/designer. (None of these gates were executed in Task 06. This readiness document does not itself authorize or infer any cloud, infrastructure, or environment mutation):

- **Identity & Commit Binding**: Bind deployment strictly to the committed Git SHA, designated GCP/Firebase project, owner identity, runtime service account, and verified rollback plan.
- **Owner-Approved Allowed-Mutation Manifest**: Before any Task 08 mutation occurs, require a separately owner-approved exhaustive allowed-mutation manifest. The manifest must explicitly identify each exact target resource, operation, before/after value, responsible owner, rollback procedure, and evidence destination. Every unlisted mutation is strictly forbidden.
- **Pre-Created Infrastructure & Storage IAM**: Pre-create and verify the exact dedicated GCS bucket with least-privilege object-level access (granting only the required object read/write access without bucket-creation or administration permissions); never use downloaded service-account JSON keys.
- **Cloud Run Deployment Specification**:
  - Exactly one Uvicorn process (`min=1`, `max=1`).
  - Request timeout set to `3600` seconds.
  - Startup probe configured to HTTP `GET /readyz`.
  - Liveness probe configured to HTTP `GET /healthz`.
  - HTTP/1.1 WebSocket support (no end-to-end HTTP/2 WebSocket configuration).
- **Runtime Environment Binding**: Set `AUTH_BYPASS=false`, verified email allowlist, explicit project/org/region/bucket/CORS variables, host capture disabled, and audio backup disabled. Explicitly prove that no local `.env` or `.env.local` files leak into the runtime environment.
- **Firebase Hosting Production Build**: Build frontend in a clean environment with explicit production URLs (`https://<cloud-run-host>`, `wss://<cloud-run-host>/ws`, `wss://<cloud-run-host>/api/stream/native`), all required Firebase public web configuration, and `NEXT_PUBLIC_AUTH_BYPASS=0`.
- **Gate Execution & Proof**: Execute and record `docs/launch/week-4-hosted-gate-checklist.md` with complete evidence for:
  - Allowed and denied Google accounts;
  - Cross-owner access denial;
  - Path and header mismatch frame rejections;
  - Liveness and startup health probe behavior under cold starts;
  - WebSocket ticket renewal, expiration, and reconnection handling;
  - TLS certificates, ingress controls, and IAM service permissions;
  - Exact deployed revision verification and 100% traffic allocation.

---

## 4. Known Pilot Limitations & Operational Invariants

- **WebSocket Request Timeout**: Cloud Run WebSockets operate as persistent HTTP requests subject to the configured request timeout (`3600`s maximum). When the timeout is reached, connections close and clients must reconnect.
  - Reference: [Cloud Run WebSockets](https://cloud.google.com/run/docs/triggering/websockets)
  - Reference: [Cloud Run Request Timeout](https://cloud.google.com/run/docs/configuring/request-timeout)
- **Session Duration & Reconnection Boundaries**: `3600` seconds is the platform timeout ceiling, not a guarantee of continuous audio across network blips. The native macOS companion automatically reconnects with backoff; browser microphone streaming has **no automatic reconnect**. Task 08 must validate the operator recovery path and must not claim uninterrupted browser audio across a Cloud Run timeout. User guidance: "Se o status reconectando persistir por mais de 2 minutos, reinicie a sessão."
- **Process-Local Ephemeral State**: Single-instance deployment (`min=max=1`) prevents multi-instance routing conflicts but leaves in-memory state (stream keys, active WebSocket connections, ephemeral session locks) non-durable across process restarts.
- **Session Affinity**: Session affinity does not provide state durability across container restarts. Multi-tenant distributed clustering remains out of scope for the pilot.
- **Retention Policy**: `DATA_RETENTION_DAYS=90` is an inert configuration placeholder and does not constitute an automated deletion guarantee.

---

## 5. Authoritative References

- [Google Cloud Run — WebSockets](https://cloud.google.com/run/docs/triggering/websockets)
- [Google Cloud Run — Configuring Request Timeout](https://cloud.google.com/run/docs/configuring/request-timeout)
- [Google Cloud Run — Health Checks (Startup and Liveness Probes)](https://cloud.google.com/run/docs/configuring/healthchecks)
- [Google Cloud Run — Container Runtime Contract](https://cloud.google.com/run/docs/container-contract)
