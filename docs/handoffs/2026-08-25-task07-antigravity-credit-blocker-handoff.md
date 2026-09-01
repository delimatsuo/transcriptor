# Transcriptor Task 07 Antigravity Credit-Blocked Handoff

Created: 2026-08-25 00:16 EDT
Prepared by: Codex

## Objective

Complete the Recruiter-phase Task 07 Firebase-auth hardening work using Antigravity/Gemini as the code builder and Codex as designer, verifier, and Git owner. The work is source-only: harden and causally verify local/offline authentication controls without accessing credentials, providers, live audio, deployment, or production systems.

## Current State

- Repo/workspace: `/Volumes/Extreme Pro/MYPROJECTS/Transcriptor`
- Branch: `codex/recruiter-phase`
- Worktree: primary local recruiter-phase checkout at the workspace path above
- Latest commit: `dc262e5 docs(builder): add task 07 second repair packet`
- Dirty state at 2026-08-25 00:16 EDT: 31 modified tracked paths, 10 untracked non-protected Task 07 paths, 3 protected untracked instruction files, and no staged paths. Preserve the dirty tree.
- Open pull requests: none at handoff time (`gh pr list` returned no entries).

## Newest User Request

Prepare a handoff. The next session must preserve the builder/verifier split: Antigravity/Gemini writes only explicitly allowed code; Codex designs repair packets, independently verifies, owns Git, and commits only after qualification. Newest user request wins if later instructions conflict.

## Completed Work

- Task 07 prior builder work remains uncommitted and intentionally dirty. Its expected implementation inventory is held in `/tmp/task07-live-changed.txt`: 39 non-protected paths, including `backend/tests/test_native_stream_endpoint.py`.
- The Task 07 source manifest is `/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/docs/builder/task-07-source-manifest.txt`. It has 212 regular non-symlink paths and SHA-256 `e6c238bdc24c78825c80ac659e125183097a30ab3fc4c2a4e30ce706609df1a3`.
- Live invariants that must not change:
  - `frontend/src/lib/auth.ts`: `8694c23b7545a0e0773aa77f18eb3d81f2ef5a40836dd867954c1865aa31196e`
  - `frontend/next-env.d.ts`: `1862ac4bbbc5192d4bf562161df66ea547ed3e67173100656ab606ae9797db2b`
  - `frontend/src/components/AudioDeviceSelector.tsx`: `b7c068554df48611e9a659d16e2e5d6727ef538271297fd1ce80bbd36fea7ff0`
- The root-owned clean-room runner is `/tmp/task07_frontend_gate.sh` (mode plus command arguments). It builds a positive-manifest disposable mirror, uses `env -i`, excludes local env files, and currently expects executable digest `ae2f804ef7881d82331909228b485c6d0fa6f0ff945983eace979a801af95c2f`.

## In Progress

Antigravity/Gemini began approved frontend Repair 10A.11 but ran out of shared credits before finishing. Only partial test-harness changes exist:

- `frontend/src/lib/authAdmission.test.ts` SHA-256 `020fe9d63772616b23ee098aafb1f8ad5c5034ced450fbd6052ff3398706e781`
- `frontend/src/lib/authController.test.ts` SHA-256 `4e0c78f96b92f6bf4309d80057ad30fcd66214e7cfee4cd0e9f01847270c3ffc`
- `frontend/e2e/auth-source-readiness.spec.ts` remains SHA-256 `53ea4285a3047bd8e3b176d129931f04fd7341bc72aadb2c1dfb5f109ca5b089`.

The Antigravity UI showed “Insufficient AI Credits / balance too low.” A scoped reload did not clear it. Do not enable paid credit overages, switch builders, or send unrelated Antigravity prompts without explicit user authority. An unrelated NewCo prompt appeared in the Transcriptor UI and failed immediately; it did not mutate this repository.

## Important Decisions

- Use Gemini/Antigravity only as the low-cost builder. Codex supplies bounded packets, runs clean-room verification, evaluates review findings, and owns Git operations.
- Do not send backend Repair 10B until frontend 10A passes independent final verification at a fresh exact digest.
- Keep Task 07 source-only. No credentials, `.env*` files, providers, Firebase/GCP calls, cloud resources, physical devices, recordings, deployment, PR creation, or release claims are authorized.
- The two handoff files created by this request are user-authorized meta-documentation. They are outside the 39-path builder-owned implementation inventory and must not be folded into it.

## Files And Artifacts

- `/tmp/task07-repair10a11-draft.txt`: approved original frontend builder packet; 9,998 bytes; SHA-256 `f6db03dac034ae85a6c46690a696de467f77bbc3db63263d6dec162a7e7fa099`.
- `/tmp/task07-repair10a11-continuation.txt`: approved continuation packet for the current partial tree; 8,365 bytes; SHA-256 `3c70b1d7ad453ae0f948974423d4227305d7dc88eb6246bf8aa616b8d3d332a3`. It limits Gemini to exactly three files: `frontend/src/lib/authAdmission.test.ts`, `frontend/src/lib/authController.test.ts`, and `frontend/e2e/auth-source-readiness.spec.ts`.
- `/tmp/task07-repair10b-compact.txt`: backend packet, held until frontend qualification; SHA-256 `a46b07ef6fa977e50978bbc72f11e6a03f7dbec8bc27d08338d6ef635983a40f`.
- `/tmp/task07-repair10b-addendum.txt`: backend causal-proof addendum; substitute a final frontend digest before dispatch; SHA-256 `ca34602bec50d6f48bad9698f125a467847e6579522d0fb412ea7d9f8cd63d9e`.
- `/tmp/task07_frontend_gate.sh`: root-owned clean-room verification wrapper. Invoke as `"/tmp/task07_frontend_gate.sh" <gate-name> <command...>`.
- `docs/builder/task-07-report.md` and `docs/launch/firebase-auth-pilot-source-readiness.md`: existing dirty Task 07 evidence docs. Do not update them or claim qualification while frontend remains partial.

## Commands Run And Results

```bash
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"
git status --short --branch
git log --oneline -3
git diff --check
gh pr list
```

Result: on `codex/recruiter-phase` at `dc262e5`; dirty as described above; `git diff --check` produced no whitespace errors; no staged files; `gh pr list` was empty.

```bash
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"
"/tmp/task07_frontend_gate.sh" unit npm --prefix frontend test
```

Result: the disposable, `env -i` mirror preserved source and mirror digest `ae2f804ef7881d82331909228b485c6d0fa6f0ff945983eace979a801af95c2f` and preserved the live `next-env.d.ts` hash. The suite had 125 tests: 124 passed and 1 failed. The single failure was Section J.3 AST binding proof, with `ReferenceError: require is not defined` from `frontend/src/lib/authController.test.ts:3634` in the mirror.

## Verification

- Passed: `git diff --check`; no staged changes; current partial-tree/hash checks above.
- Failed: clean-room frontend unit gate, 124/125 due solely to the ESM-incompatible `require` in the partial AST test.
- Not run after partial builder work: clean-room TypeScript, offline auth E2E, causal mutation playbook, independent final staff review, backend Repair 10B, combined qualification, documentation refresh, commit, push, or PR.

## Risks And Watchouts

- **P1 — incomplete frontend harness:** The partial changes are not qualifying. Do not treat prior reviews, a partial digest, or 124/125 tests as approval.
- **P1 — builder blocked:** Antigravity cannot continue until the user makes shared credits available or explicitly authorizes another builder. Do not incur spend autonomously.
- **P1 — dirty worktree:** Preserve every change. Do not use `git reset`, `git clean`, `git checkout --`, broad formatting, or broad staging.
- **P1 — protected instructions:** Do not read, modify, or stage `AGENTS.md`, `frontend/AGENTS.md`, or `frontend/CLAUDE.md`.
- **P1 — no live boundary crossing:** Keep every command offline and clean-room; never inspect or copy `.env*`, use real identities, or contact Firebase/GCP/other providers.

## Do Not Do

- Do not commit, push, open a PR, merge, deploy, enable paid Antigravity credits, or switch the builder without new user authority.
- Do not dispatch Repair 10B until frontend 10A independently passes at a newly frozen final digest.
- Do not let Gemini use Git, edit outside its exact three-file continuation allowlist, write reports, or claim test success without Codex replay.
- Do not modify product source for a test-only causal-proof repair unless a fresh bounded RED probe reproduces a product defect and the user authorizes that scope.

## Next Recommended Steps

1. Resolve the authority gap: wait for Antigravity credits to be available, or ask the user to authorize a specific alternative builder. If credits become available, send the exact bytes of `/tmp/task07-repair10a11-continuation.txt` to the existing Transcriptor Antigravity conversation.
2. After Gemini returns, inspect the exact allowed three-file diff, run `git diff --check`, and replay the frontend unit gate in a fresh clean-room mirror. The first target is 125/125, including the ESM-safe replacement for `require`.
3. Run clean-room TypeScript and offline auth E2E in distinct mirrors; then run the root-owned causal mutation playbook and obtain fresh independent review at the final exact digest.
4. Only after frontend approval, substitute that final digest into the backend addendum, dispatch 10B to Gemini, and repeat the same builder/verifier loop.
5. Only after combined qualification update evidence docs, review exact scope, and ask the user before committing or opening a PR.

## Open Questions

- Can the user make Antigravity shared credits available for the existing Gemini builder, without enabling automatic paid overages?
- If credits remain unavailable, does the user authorize a named alternate builder/model for the bounded three-file frontend continuation?
