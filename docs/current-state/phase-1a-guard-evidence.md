# Phase 1A Guard-First Implementation Evidence

**Recorded:** 2026-07-15T19:12:27Z

**Status:** Guard-first slice implemented, hardened, verified, and approved for Phase 1A conformance. See `docs/reviews/2026-07-15-phase-1a-guard-review.md`.

**Authorization:** The user explicitly approved the staff decision to begin Phase 1A under the guard-first conditions. This did not authorize Phase 1B-1D, push, merge, deployment, cloud mutation, native capture, ambient/human audio, real data, or legacy-data mutation.

**Base guard commit:** `f7c16f233a51766f0ea622fc2b9534689865d89e`

**Reviewed hardened tip:** `9ea95803e92ae740e6078903b2665cf604e1db09`

**Parent evidence commit:** `95266a0dfc801dcec4c6ca2c11c35159df48e924`

**Branch/worktree:** `codex/native-companion-phase1` in `/Volumes/Extreme Pro/myprojects/Transcriptor-worktrees/native-companion-phase1`

## 1. Implemented boundary

- Added an isolated `companion/protocol/` surface that is not imported by the production backend/frontend and is absent from deployment configuration.
- Added a tracked canonical JSON metadata schema plus exact coverage-identity, terminal-outcome, retry, and message-bound rules.
- Added a tracked machine-readable manifest for the three content-free, generated, memory-only fixtures.
- Narrowly allowlisted only the schema and fixture-manifest JSON files through the repository's broad `*.json` ignore rule.
- Added a macOS Seatbelt profile containing `(deny network*)`.
- Added a wrapper that launches `/usr/bin/python3` under `sandbox-exec` and `env -i`, with `HOME=/var/empty`, deterministic Python settings, and a `PYTHONPATH` containing only the isolated Phase 1A package.
- Added fail-closed environment, import, filesystem-mutation, environment-mutation, subprocess/fork, and `ctypes` guards before test discovery.
- Added a closed fixture catalog that accepts only committed manifest IDs, generates bytes in memory, and verifies length plus SHA-256 before returning them.

No provider simulator, protocol state machine, Swift binding, production integration, hosted endpoint, cloud SDK call, or native capture was added in this slice.

## 2. Verification results

The committed wrapper `companion/protocol/scripts/run_offline_guard.sh` executes the suite twice and compares deterministic metadata-only summaries.

Post-commit result:

```json
{"errors":0,"failures":0,"phase":"1A-guard","successful":true,"testsRun":13}
```

Both runs passed all 13 tests. Verified behaviors include:

- inherited fake credential, gateway, and proxy variables are removed by the wrapper;
- missing `TARS_PHASE1A_MODE=offline` aborts before test discovery;
- credential-, project-, endpoint-, proxy-, token-, and secret-shaped test environments fail closed;
- production backend, Google/Firebase/cloud-network libraries, and `ctypes` imports are blocked;
- an IPv4 loopback connection fails with `EPERM` under the process sandbox;
- payload-file creation, subprocess execution, fork/spawn paths, and post-start environment mutation are blocked;
- schema and manifest are present, unignored, and tracked in the Git index;
- the three fixture recipes reproduce their committed lengths and digests;
- the only runtime catalog loader pins the committed manifest SHA-256 and exposes no arbitrary path loader;
- unlisted fixture IDs and a tampered digest are rejected;
- the schema exposes bounded metadata only and no audio or transcript-content field;
- repeated executions return identical summaries.

## 3. Artifact and scope checks

- Post-run clean status, including ignored files: `0` lines.
- Unexpected `__pycache__`, `.pyc`, audio, binary payload, or forbidden-payload files: `0`.
- Production/cloud import statements in the Phase 1A implementation: `0`; the test module refers to forbidden names only as negative probes.
- Network-client import statements in the Phase 1A implementation: `0`.
- Fixture payload files committed to the tree: `0`.
- Feature-WIP paths in the guard commit: `0`.
- Staged/committed files outside `.gitignore` and `companion/protocol/`: `0`.
- `git diff --check` passed for the guard commit.

## 4. Review result

The guard review passed after commit `9ea9580` resolved the arbitrary-manifest-path finding. Phase 1A protocol state-machine and Swift/Python conformance may proceed inside the clean worktree. This does not authorize any Phase 1B-1D activity.
