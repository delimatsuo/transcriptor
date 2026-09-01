> **Archived historical prompt — do not execute.** This paste-ready prompt is preserved for provenance only and was superseded after Repair 10A.11 and the split PR stack completed. Do not resume the named repair, restore its old dirty-worktree state, or use its counts, digests, builder blocker, or authority as current. Use the newest durable handoff and exact-head PR/CI/review records.

You are continuing Transcriptor Recruiter-phase Task 07, a source-only Firebase-auth hardening and causal-verification effort.

Current objective:
Resume the interrupted frontend Repair 10A.11 only after the Antigravity/Gemini credit blocker is resolved or the user authorizes another builder. Codex remains designer, verifier, and Git owner; the builder writes only the exact authorized files.

Workspace:
- Repo/worktree: `/Volumes/Extreme Pro/MYPROJECTS/Transcriptor`
- Branch: `codex/recruiter-phase`
- First read: `/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/docs/handoffs/2026-08-25-task07-antigravity-credit-blocker-handoff.md`
- Packet to use after authority is available: `/tmp/task07-repair10a11-continuation.txt` (SHA-256 `3c70b1d7ad453ae0f948974423d4227305d7dc88eb6246bf8aa616b8d3d332a3`)

Newest user request:
Use Antigravity/Gemini as the code builder and Codex as designer/planner/verifier. The latest request was to create this handoff. Newest user request wins.

Current state:
- HEAD is `dc262e5`; branch is local; no PR was open at handoff time.
- The worktree is intentionally dirty with Task 07 work. Preserve it. No paths are staged.
- Antigravity displayed “Insufficient AI Credits / balance too low” and could not finish the frontend packet. Do not enable paid overages, switch builders, or send unrelated prompts without explicit user approval.
- The partial frontend unit suite is 124/125 in a fresh `env -i` positive-manifest mirror. The sole failure is `ReferenceError: require is not defined` in the Section J.3 AST test at `frontend/src/lib/authController.test.ts:3634`.
- The expected partial executable digest is `ae2f804ef7881d82331909228b485c6d0fa6f0ff945983eace979a801af95c2f`.

Critical constraints:
- Do not revert, reset, clean, checkout, broadly stage, commit, push, open a PR, deploy, or inspect/copy `.env*` files.
- Do not read, edit, or stage `AGENTS.md`, `frontend/AGENTS.md`, or `frontend/CLAUDE.md`.
- No provider, Firebase/GCP, cloud, credentials, live audio, device, or production actions.
- Do not send backend Repair 10B until frontend 10A independently qualifies at a new exact final digest.
- Gemini may edit only `frontend/src/lib/authAdmission.test.ts`, `frontend/src/lib/authController.test.ts`, and `frontend/e2e/auth-source-readiness.spec.ts` under the continuation packet. Gemini must not use Git or update reports.

Facts and evidence:
- `git diff --check` passed at handoff.
- `/tmp/task07_frontend_gate.sh` is the root-owned clean-room wrapper; invoke it as `"/tmp/task07_frontend_gate.sh" <gate-name> <command...>`.
- The manifest is `docs/builder/task-07-source-manifest.txt` (212 regular non-symlink paths; SHA-256 `e6c238bdc24c78825c80ac659e125183097a30ab3fc4c2a4e30ce706609df1a3`).
- The original approved frontend packet is `/tmp/task07-repair10a11-draft.txt` (SHA-256 `f6db03dac034ae85a6c46690a696de467f77bbc3db63263d6dec162a7e7fa099`).

Next recommended action:
1. Verify branch/status/digest without touching protected files. If the user has not resolved the builder authority gap, report the blocker and wait; do not make alternate-model assumptions.
2. When authorized, send the continuation packet verbatim to the existing Transcriptor Antigravity conversation, then independently inspect its exact three-file diff.
3. Require a fresh clean-room unit replay to reach 125/125, then separately run TypeScript and offline auth E2E. After final frontend causal review, only then prepare backend 10B.

Verification expected:
- `git diff --check`
- `"/tmp/task07_frontend_gate.sh" unit npm --prefix frontend test`
- clean-room TypeScript and offline-auth E2E in distinct fresh mirrors after the unit repair

Known risk:
The current state is partial, not qualified. Prior green results and reviews do not apply once any source/manifests change.

If anything conflicts, the newest user request wins. Start by running:

```bash
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"
git status --short --branch
```
