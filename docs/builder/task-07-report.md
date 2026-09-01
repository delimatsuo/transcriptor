# Task 07 source-readiness report

## Current identity and verdict

**Verdict: SOURCE PASS / OWNER REAL-AUTH NOT RUN**

The current executable-source digest is `1fd7c82b7164d7fb1d626df321fe5d9af6f426f93d616a7400ab873dbe4aa5f4`.

| Identity | Value |
|---|---|
| Branch | `codex/recruiter-phase` |
| HEAD | `dc262e55f62771615102cef22667f21c3a52a219` |
| Positive manifest | `e6c238bdc24c78825c80ac659e125183097a30ab3fc4c2a4e30ce706609df1a3` (212 paths) |
| `frontend/next-env.d.ts` prerequisite | `1862ac4bbbc5192d4bf562161df66ea547ed3e67173100656ab606ae9797db2b` |
| Executable source | `1fd7c82b7164d7fb1d626df321fe5d9af6f426f93d616a7400ab873dbe4aa5f4` |

The dirty Task 07 worktree is preserved. No paths are staged; no commit, push, or PR was made. `frontend/next-env.d.ts` and `AudioDeviceSelector.tsx` are prerequisite restorations, not builder-owned changed paths, subject to verifier checks.

## Changed-file inventory

The inventory remains exactly 38 paths: **frontend 23 paths** and 15 paths outside frontend. `frontend/src/components/AudioDeviceSelector.tsx` and `frontend/next-env.d.ts` are prerequisite restorations, not changed paths, subject to verifier checks. This count is an inventory statement, not a claim that every path is independently qualified.

```text
.env.example
Dockerfile
backend/auth.py
backend/config.py
backend/main.py
backend/scripts/check_auth_setup.py
backend/tests/test_auth.py
backend/tests/test_auth_matrix.py
backend/tests/test_cloud_run_readiness.py
backend/tests/test_startup_credentials.py
backend/tests/test_task07_auth_source_readiness.py
docs/builder/task-07-report.md
docs/launch/cloud-run-pilot-source-readiness.md
docs/launch/firebase-auth-pilot-source-readiness.md
frontend/e2e/auth-source-readiness.spec.ts
frontend/e2e/fixtures.ts
frontend/e2e/recent-interviews.spec.ts
frontend/package.json
frontend/playwright.auth.config.ts
frontend/playwright.config.ts
frontend/src/app/page.tsx
frontend/src/components/AuthControls.tsx
frontend/src/components/CompanionCommand.tsx
frontend/src/components/InterviewReportReview.tsx
frontend/src/components/NoteChips.tsx
frontend/src/components/RecentInterviews.tsx
frontend/src/components/SessionControls.tsx
frontend/src/hooks/useBrowserAudioCapture.ts
frontend/src/hooks/useWebSocket.ts
frontend/src/lib/auth.ts
frontend/src/lib/authAdmission.test.ts
frontend/src/lib/authAdmission.ts
frontend/src/lib/authController.test.ts
frontend/src/lib/authController.ts
frontend/src/lib/firebase.ts
frontend/src/lib/runtimeConfig.test.ts
frontend/src/lib/runtimeConfig.ts
scripts/run_staging_preflight.py
```

## Current evidence

Evidence categories are explicit: PASS, BLOCKED, and NOT RUN. DESELECTED/CONSTRAINED qualify the applicable category.

| Area | Status | Evidence and ceiling |
|---|---|---|
| Hostile-environment isolation | PASS | Clean-room isolation guard passed. |
| Backend focused | PASS | Earlier clean-room aggregate: `336 passed, 2 deselected`. The two deselected `.env*` example nodes subsequently passed separately in the owner-supplied current-workspace scrubbed-environment run (`2 passed in 3.00s`); this is not an aggregate `338` count. |
| Backend full | PASS | Earlier clean-room aggregate: `580 passed, 2 deselected`. The same two `.env*` example nodes subsequently passed separately in the owner-supplied current-workspace scrubbed-environment run (`2 passed in 3.00s`); this is not an aggregate `582` count. |
| Backend production syntax | PASS | 4 paths compiled successfully. |
| Frontend unit | PASS | 125 passed. |
| TypeScript | PASS | Zero diagnostics. |
| Production build | PASS | Build passed. |
| Swift tests/build | CONSTRAINED PASS | 79 tests passed and Swift build passed. SwiftPM's internal sandbox was disabled only because nested `sandbox-exec` was denied; the enclosing Codex filesystem sandbox remained enforced. The initial nested-sandbox failure is discarded diagnostic evidence. |
| WebSocket lifecycle | PASS | The targeted test passed in five separate fresh mirrors. |
| Auth-offline browser | PASS | Owner-run unsandboxed local clean-room replay: 4/4 passed in 20.3s with the exact executable digest before and after. This is owner-supplied terminal evidence and was not independently rerun by Codex outside its sandbox. |
| Core browser | PASS | Owner-run unsandboxed local clean-room replay: 19/19 passed in 19.8s with the exact executable digest before and after. This is owner-supplied terminal evidence and was not independently rerun by Codex outside its sandbox. |
| `.env*` example nodes | PASS | Owner-supplied current-workspace scrubbed-environment run: `backend/tests/test_cloud_run_readiness.py::test_environment_examples_static_contract` and `backend/tests/test_task07_auth_source_readiness.py::test_env_example_requires_tars_runtime_mode_local`; `2 passed in 3.00s`. Codex neither independently reran these nodes nor inspected, copied, or hashed any `.env*` file. |
| Receive-ready causal mutant | PASS | Removing the receive-ready wait failed as required. |
| Diff/worktree checks | PASS | `git diff --check` passed and no paths were staged; these were verifier read-only checks. |

Additional focused PASS evidence: negative readiness (18), ready-path positive controls (3), adapter causal test (1), authorized source-readiness (175 passed, 1 deselected), and incomplete-drain control (1).

The two `.env*` example nodes below passed separately in an owner-supplied current-workspace run under a scrubbed `env -i` environment:

- `backend/tests/test_cloud_run_readiness.py::test_environment_examples_static_contract`
- `backend/tests/test_task07_auth_source_readiness.py::test_env_example_requires_tars_runtime_mode_local`

The result was `2 passed in 3.00s`. Codex neither independently reran these nodes nor inspected, copied, or hashed any `.env*` file. This is point-in-time, owner-attributed evidence for the examples. The executable-source digest `1fd7c82b7164d7fb1d626df321fe5d9af6f426f93d616a7400ab873dbe4aa5f4` binds executable source only; it does not bind `.env*` byte identity.

### Browser boundary

An owner-run unsandboxed local clean-room replay at the exact executable digest qualified both controlled synthetic suites: auth-offline 4/4 passed in 20.3s and core 19/19 passed in 19.8s. Pre/post executable digests stayed equal. This owner-supplied terminal evidence was not independently rerun by Codex outside its sandbox. It establishes controlled local/synthetic browser qualification only; it does not prove real identities, Firebase/provider/cloud access, deployment, production, live audio, or devices.

Canonical Playwright attempts that hit Watchpack `EMFILE` before collection are discarded diagnostics. The earlier Codex-sandbox Chromium `bootstrap_check_in org.chromium.Chromium.MachPortRendezvousServer.<pid>: Permission denied (1100)` is retained only as discarded historical diagnostic evidence and is explicitly superseded for local browser qualification by the owner run. It was a pre-assertion launch diagnostic, never a functional failure.

## Historical and discarded evidence

Prerequisite and design history is retained here for traceability, but historical builder assertions are not current qualification. Antigravity P1-P4 were static builder work with zero tests. Later Luna repairs and verifier gates supersede them. Earlier builder counts (`231`, `475`, `85`, and pre-digest Playwright `1/1` or `19/19`) are superseded/non-qualifying after later source changes and must not be read as current evidence; the current `4/4` and `19/19` results are the owner-supplied exact-digest replay described above.

## Remaining qualification boundary

No Task 07 source gate remains unrun at the recorded evidence state. The two `.env*` example tests below are PASS separately from the owner-supplied run and are not folded into either earlier clean-room aggregate count:

- `backend/tests/test_cloud_run_readiness.py::test_environment_examples_static_contract`
- `backend/tests/test_task07_auth_source_readiness.py::test_env_example_requires_tars_runtime_mode_local`

Any known or suspected change to either relevant example after the owner run invalidates this two-node PASS and requires the exact owner command to be rerun. Any executable-source change continues to invalidate applicable gates.

No browser source gate remains unrun at the recorded evidence state. Real identities, login, tokens, Firebase/GCP/cloud/provider actions, deployment, Docker daemon/image, live audio/device proof, production proof, and owner real-auth remain NOT RUN. No identity, token, claim, local environment value, mirror path, or screenshot data is recorded here. Static contract variable names and required safe values remain only where architecturally necessary.

## Final verdict

**SOURCE PASS / OWNER REAL-AUTH NOT RUN**
