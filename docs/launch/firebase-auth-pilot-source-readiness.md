# Firebase auth pilot source readiness

## Source status

**SOURCE PASS / OWNER REAL-AUTH NOT RUN**

Executable-source digest: `1fd7c82b7164d7fb1d626df321fe5d9af6f426f93d616a7400ab873dbe4aa5f4`.

This document records source, unit, syntax, build, constrained Swift test/build, and controlled local/synthetic browser evidence. Controlled local/synthetic browser runtime is proven by an owner-run clean-room replay; this does not prove a real identity, Firebase/provider/cloud access, or a deployed tenant.

## Source contract

The source contract keeps authentication fail-closed: a local development bypass is explicit, readiness and configuration are checked before protected access, and missing or malformed Firebase configuration cannot silently become an authenticated state. Auth admission, account switching, logout, refresh/revocation handling, `/api/me`, and protected WebSocket admission are represented by source and test contracts. Safe static configuration names remain documented where needed; no local environment value, identity, token, or claim is recorded.

The source also separates public build configuration from owner-only real authentication. Source/unit/compile evidence and the controlled local/synthetic browser replay support those boundaries; real identity and deployed-tenant behavior remain outside this evidence.

## Current metrics

| Evidence | Status | Result |
|---|---|---|
| Hostile environment isolation | PASS | Clean-room isolation passed. |
| Backend focused/full | PASS | Earlier clean-room aggregates: `336 passed, 2 deselected`; `580 passed, 2 deselected`. The two deselected `.env*` example nodes subsequently passed separately in the owner-supplied current-workspace scrubbed-environment run (`2 passed in 3.00s`); these are not aggregate `338`/`582` totals. |
| Backend syntax | PASS | 4 production paths. |
| Frontend unit | PASS | 125 passed. |
| TypeScript/build | PASS | Zero diagnostics; production build passed. |
| Negative readiness / positive controls | PASS | 18 negative, 3 ready-path controls. |
| Authorized source-readiness | PASS | 175 passed, 1 deselected. |
| Adapter causal / incomplete-drain | PASS | 1 / 1. |
| WebSocket lifecycle | PASS | Targeted test passed in five fresh mirrors. |
| Playwright auth-offline | PASS | Owner-run unsandboxed local clean-room replay: 4/4 passed in 20.3s with the exact executable digest before and after. Owner-supplied terminal evidence was not independently rerun by Codex outside its sandbox. |
| Playwright core | PASS | Owner-run unsandboxed local clean-room replay: 19/19 passed in 19.8s with the exact executable digest before and after. Owner-supplied terminal evidence was not independently rerun by Codex outside its sandbox. |
| `.env*` example tests | PASS | Owner-supplied current-workspace scrubbed-environment run: `backend/tests/test_cloud_run_readiness.py::test_environment_examples_static_contract` and `backend/tests/test_task07_auth_source_readiness.py::test_env_example_requires_tars_runtime_mode_local`; `2 passed in 3.00s`. Codex neither independently reran these nodes nor inspected, copied, or hashed any `.env*` file. |
| Swift | CONSTRAINED PASS | 79 passed and build passed with nested SwiftPM sandbox disabled; outer Codex sandbox remained enforced. |

An owner-run unsandboxed local clean-room replay at the exact executable digest qualified controlled local/synthetic browser behavior: auth-offline 4/4 passed in 20.3s and core 19/19 passed in 19.8s, with exact pre/post digest equality. This owner-supplied terminal evidence was not independently rerun by Codex outside its sandbox. It does not prove real identities, Firebase/provider/cloud access, deployment, production, live audio, or devices.

Canonical Playwright attempts that hit Watchpack `EMFILE` before collection are discarded diagnostics. The earlier Codex-sandbox Chromium `bootstrap_check_in org.chromium.Chromium.MachPortRendezvousServer.<pid>: Permission denied (1100)` is retained only as discarded historical diagnostic evidence and is explicitly superseded for local browser qualification by the owner run. It was a pre-assertion launch diagnostic, never a functional failure.

The two `.env*` example results are point-in-time, owner-attributed evidence from the current workspace. The executable-source digest `1fd7c82b7164d7fb1d626df321fe5d9af6f426f93d616a7400ab873dbe4aa5f4` binds executable source only; it does not bind `.env*` byte identity. The earlier clean-room aggregate counts remain exactly `336 passed, 2 deselected` and `580 passed, 2 deselected`; the two example nodes passed separately afterward and are not folded into those aggregates.

## Task 07 completion and invalidation boundary

1. No Task 07 source gate remains unrun at the recorded evidence state. The two `.env*` example nodes are PASS separately from the owner-supplied run and are not folded into the earlier clean-room aggregate counts.

No browser source gate remains unrun at the recorded evidence state.

Any known or suspected change to either relevant example after the owner run invalidates this two-node PASS and requires the exact owner command to be rerun. Any executable-source change continues to invalidate applicable gates.

## Task 08 boundary

Real identity, login, tokens, Firebase/provider/GCP/cloud access, authorized domains, API-key restrictions, Hosting, Cloud Run, deployment, Docker daemon/image, device or live-audio proof, production verification, and owner real-auth are Task 08 requirements and remain NOT RUN. Nothing here authorizes cloud or provider action.

## Truthful outcome

**SOURCE PASS / OWNER REAL-AUTH NOT RUN**
