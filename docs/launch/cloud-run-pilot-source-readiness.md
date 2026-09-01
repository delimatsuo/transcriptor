# Cloud Run pilot source readiness

## Status

**SOURCE PASS / OWNER REAL-AUTH NOT RUN**

Executable-source digest: `1fd7c82b7164d7fb1d626df321fe5d9af6f426f93d616a7400ab873dbe4aa5f4`.

Task 07 has source, unit, syntax, compile, build, constrained Swift, controlled local/synthetic browser evidence, and separate owner-supplied PASS evidence for the two `.env*` example nodes. No Task 07 source gate remains unrun at the recorded evidence state.

## Current verification

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

**SOURCE PASS / OWNER REAL-AUTH NOT RUN**
