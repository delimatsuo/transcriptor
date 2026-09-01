# Cloud Run pilot source readiness

> **Historical evidence snapshot — not current exact-head qualification.** The digest, counts, PASS verdicts, and browser evidence below belong only to the recorded pre-split Task 07 source snapshot. Later executable-source changes invalidate the applicable gates. Do not use those historical results as proof for draft PR #41 or the split PR stack; use the applicable PR description, exact-head CI, and review record. The prospective Task 08 safety contract in this document remains required and non-authorizing.

## Historical source status

**HISTORICAL SNAPSHOT: SOURCE PASS / OWNER REAL-AUTH NOT RUN**

Executable-source digest: `1fd7c82b7164d7fb1d626df321fe5d9af6f426f93d616a7400ab873dbe4aa5f4`.

Task 07 has source, unit, syntax, compile, build, constrained Swift, controlled local/synthetic browser evidence, and separate owner-supplied PASS evidence for the two `.env*` example nodes. No Task 07 source gate remains unrun at the recorded evidence state.

## Historical verification

| Area | Status | Result |
|---|---|---|
| Hostile environment and source isolation | PASS | Clean-room isolation passed; executable pre/post digest remained equal. |
| Backend focused/full | PASS | Earlier clean-room aggregates: `336 passed, 2 deselected`; `580 passed, 2 deselected`. The two deselected `.env*` example nodes subsequently passed separately in the owner-supplied current-workspace scrubbed-environment run (`2 passed in 3.00s`); these are not aggregate `338`/`582` totals. |
| Backend syntax | PASS | 4 production paths. |
| Frontend unit / TypeScript | PASS | 125 passed; zero diagnostics. |
| Production build | PASS | Build passed. |
| Readiness and causal controls | PASS | Negative 18, positive 3, adapter causal 1, incomplete-drain 1. |
| Authorized source-readiness | PASS | 175 passed, 1 deselected. |
| WebSocket lifecycle | PASS | Targeted test passed in five fresh mirrors. |
| Swift | CONSTRAINED PASS | 79 passed and build passed with nested SwiftPM sandbox disabled because nested `sandbox-exec` was denied; outer Codex sandbox remained enforced. |
| Auth-offline browser | PASS | Owner-run unsandboxed local clean-room replay: 4/4 passed in 20.3s with the exact executable digest before and after. Owner-supplied terminal evidence was not independently rerun by Codex outside its sandbox. |
| Core browser | PASS | Owner-run unsandboxed local clean-room replay: 19/19 passed in 19.8s with the exact executable digest before and after. Owner-supplied terminal evidence was not independently rerun by Codex outside its sandbox. |
| `.env*` example nodes | PASS | Owner-supplied current-workspace scrubbed-environment run: `backend/tests/test_cloud_run_readiness.py::test_environment_examples_static_contract` and `backend/tests/test_task07_auth_source_readiness.py::test_env_example_requires_tars_runtime_mode_local`; `2 passed in 3.00s`. Codex neither independently reran these nodes nor inspected, copied, or hashed any `.env*` file. |

An owner-run unsandboxed local clean-room replay at the exact executable digest qualified controlled local/synthetic browser behavior: auth-offline 4/4 passed in 20.3s and core 19/19 passed in 19.8s, with exact pre/post digest equality. This owner-supplied terminal evidence was not independently rerun by Codex outside its sandbox. It does not prove real identities, Firebase/provider/cloud access, deployment, production, live audio, or devices.

The canonical browser attempts that encountered Watchpack `EMFILE` before collection are discarded diagnostics. The earlier Codex-sandbox Chromium `bootstrap_check_in org.chromium.Chromium.MachPortRendezvousServer.<pid>: Permission denied (1100)` is retained only as discarded historical diagnostic evidence and is explicitly superseded for local browser qualification by the owner run. It was a pre-assertion launch diagnostic, never a functional failure.

The two `.env*` example results are point-in-time, owner-attributed evidence from the current workspace. The executable-source digest `1fd7c82b7164d7fb1d626df321fe5d9af6f426f93d616a7400ab873dbe4aa5f4` binds executable source only; it does not bind `.env*` byte identity. The earlier clean-room aggregate counts remain exactly `336 passed, 2 deselected` and `580 passed, 2 deselected`; the two example nodes passed separately afterward and are not folded into those aggregates.

## Truthful qualification ceiling

The evidence ceiling is source/unit/build plus constrained Swift, WebSocket lifecycle PASS, controlled local/synthetic browser qualification from the owner-supplied exact-digest replay, and the separate owner-attributed PASS for the two environment-example nodes. It does not extend to real identities/login/tokens, Firebase/GCP/cloud/provider access, deployment, Docker daemon/image, live audio/device, production, or owner real-auth; all remain NOT RUN.

Any known or suspected change to either relevant example after the owner run invalidates this two-node PASS and requires the exact owner command to be rerun. Any executable-source change continues to invalidate applicable gates.

## Task 08 non-authorizing boundary

Task 08 separately owns project/provider selection, Firebase configuration and restrictions, Hosting/Cloud Run binding, deployment, production configuration, and live operational verification. This source-readiness document authorizes none of those actions.

The following prospective gates remain mandatory. They do not grant authority to access a provider, accept terms, spend money, deploy, or use real identities.

### Deployment identity and mutation binding

- Bind any deployment to one exact committed Git SHA, the designated GCP/Firebase project and region, the owner/operator identity, the runtime service account, and a verified rollback target and procedure.
- Before mutation, require a separately owner-approved exhaustive manifest naming every target resource, operation, before/after value, responsible owner, rollback step, and evidence destination. Every unlisted mutation remains forbidden.

### Pre-created infrastructure and least privilege

- Pre-create the exact dedicated GCS bucket; the application must never create or administer buckets at runtime.
- Grant only the required object read/write permissions to the runtime service account. Do not use downloaded service-account JSON keys.
- Verify ingress, TLS, IAM, and cross-owner denial before traffic is admitted.

### Cloud Run single-process and WebSocket contract

- Run exactly one Uvicorn process with Cloud Run `min-instances=1` and `max-instances=1`; process-local session state is not safe under horizontal fan-out.
- Set the request timeout to `3600` seconds.
- Configure the startup probe as HTTP `GET /readyz` and the liveness probe as HTTP `GET /healthz`.
- Use HTTP/1.1 WebSocket support; do not enable an end-to-end HTTP/2 WebSocket path.
- Verify cold-start probe behavior, WebSocket ticket renewal and expiration, reconnection, exact revision routing, and 100% traffic allocation to the intended revision.

### Runtime configuration containment

- Set `AUTH_BYPASS=false`, the owner-verified allowlist, explicit project/org/region/bucket/CORS values, host capture disabled, and audio backup disabled.
- Prove that no local `.env`, `.env.local`, credential database, access token, or machine-local configuration enters the build context, image, runtime environment, logs, or evidence.
- Preserve content-free health endpoints and verify that failures do not echo secrets, identity material, raw URLs, or stream keys.

### Clean frontend production binding

- Build in a clean environment with explicit production `https://` API and `wss://` WebSocket/native-stream URLs bound to the selected Cloud Run host.
- Provide the required Firebase public web configuration, keep `NEXT_PUBLIC_AUTH_BYPASS=0`, and verify authorized-domain and API-key restrictions under the owner gate.
- Prove the deployed frontend revision and configuration correspond to the same exact commit as the backend.

### Hosted verification evidence

- Record the exact commit and deployed revision, project/region, operator, UTC timestamp, rollback target, and privacy-safe command results.
- Verify allowed and denied account behavior, cross-owner denial, path/header mismatch rejection, cold-start readiness/liveness behavior, WebSocket renewal/expiration/reconnect behavior, TLS/ingress/IAM, and rollback.
- Keep all evidence free of emails, tokens, claims, credentials, raw join links, and personal audio or transcript content.

### Pilot operational invariants

- A `3600`-second request timeout is a platform ceiling, not a continuity guarantee. Clients must reconnect; browser microphone streaming has no automatic reconnect and requires a verified operator recovery path.
- `min=max=1` avoids multi-instance routing conflicts but does not make process-local stream keys, WebSockets, session locks, or in-flight state durable across restart.
- Session affinity is not state durability, and `DATA_RETENTION_DAYS=90` is only an inert configuration value until a separately verified deletion mechanism exists.

**HISTORICAL SNAPSHOT: SOURCE PASS / OWNER REAL-AUTH NOT RUN**
