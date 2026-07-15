# Phase 1A Guard Review

**Reviewed:** 2026-07-15T20:18:33Z

**Reviewed tip:** `9ea95803e92ae740e6078903b2665cf604e1db09`

**Base guard commit:** `f7c16f233a51766f0ea622fc2b9534689865d89e`

## Decision

**Approve.** The executable offline boundary has no remaining P0 or P1 finding. Phase 1A protocol/state-machine conformance may proceed inside the named clean worktree only.

## Highest-risk issue and resolution

The initial guard exposed `FixtureCatalog.from_path`, which allowed trusted Phase 1A code to load an arbitrary manifest path. That weakened the manifest-only claim and was treated as P1.

Commit `9ea9580` removed the arbitrary path loader, pinned the exact committed manifest SHA-256, and added a negative/tracking test. The hardened suite passes 13 tests twice with identical summaries.

## Evidence

- Process execution uses `/usr/bin/sandbox-exec` with `(deny network*)` and an `env -i` environment.
- The network probe fails with `EPERM`.
- Missing offline mode aborts before test discovery.
- Credential/project/endpoint/proxy state, environment mutation, writes, subprocess/fork paths, production/cloud imports, and `ctypes` are blocked.
- Only the two explicitly allowlisted JSON inputs are unignored and tracked.
- The fixture manifest is content-free, generated in memory, and pinned by digest.
- Unknown fixtures and a tampered fixture digest fail closed.
- No generated payload, audio, bytecode, ignored artifact, or feature-WIP path remains after execution.
- The guard code is isolated from the production backend/frontend and deployment paths.

## Conditions carried into conformance

- Conformance code must call the pinned `load_committed_catalog()` path; arbitrary `FixtureCatalog` construction remains test-only.
- Every test runs through `scripts/run_offline_guard.sh`; direct test execution is not evidence.
- Protocol/state code may use only the standard library and the isolated `companion/protocol/` surface.
- Schema, deterministic vectors, and test evidence must be tracked and tied to exact commits.
- Phase 1B-1D, push, merge, deployment, cloud/provider access, native capture, ambient/human audio, real data, and legacy mutation remain prohibited.

## Recommended next action

Implement the Python coverage and terminal-outcome binding first, with deterministic vectors for coverage identity, retry identity, overlap rejection, unknown-end gaps, and message bounds. Then implement the deterministic provider/reconnect/fencing simulator. Swift validation follows against the same vectors.
