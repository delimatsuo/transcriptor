# G2-A source-only offline evidence checkpoint

Status: implementation checkpoint; G2 is not complete because C# execution is
blocked by the unavailable .NET SDK on this host.

## Exact binding

- Authorization: direct owner authorization for G2-A source-only offline work
  from publication commit `8398fa8b345e326320e54d2a598977e47ee67fa7`, tree
  `419feca4702be389c8f85129e0face1afe912419`.
- Source implementation checkpoint: commit
  `c8497eff2a77cf3de597638674608e40959f01f7`, tree
  `2581c37ce57fc78e4429b190315d7050df19516e`.
- Parent source commit: `75bb55dea228b4f01d676a9b10da29dca8968869`.
- Worktree: `/private/tmp/transcriptor-native-g2a-source`.
- Branch: `codex/native-g2a-source`.
- Worktree was clean at the source checkpoint. No protected checkout, PR #8,
  N11D-C worktree, provider, device, network, cloud, credential, capture,
  deployment, merge, release, or candidate-data activity occurred.

## Approved implementation paths

Only the approved `companion/protocol` corridor changed: README, v2 schema and
vectors, pure Python v2 model/simulator/tests, pure Swift v2 model/tests,
dependency-free C# vector runner, and offline/artifact guards. No backend,
frontend, native-capture, route, CI, hosted, or protected-handoff path changed.

The Python model covers canonical ordered interval sets, rate-derived custody
and quota limits, complete-list `covr_` terminal identities, self-contained
`seg_` transcript identities, terminal claims, provider runtime-epoch and
egress-fence single-use effects, positive provider/owner quiescence, deletion
fencing, late callback rejection, and mixed-rate 60/90/120-minute synthetic
bounded runs.

## Verification

- `git diff --check`: passed.
- Full inherited Phase 1A guarded wrapper: **67 Python tests and 6 Swift tests
  passed twice**, networking denied and scrubbed environment.
- G2-A Python runner: **13 tests passed** in the offline guarded environment.
- Shared Python/Swift canonical v2 vectors: passed, including atomic coverage,
  terminal coverage, and transcript segment identities.
- G2 artifact scan: passed with zero artifacts, forbidden imports, and
  out-of-scope paths.
- C# vector runner: source is present and dependency-free, but execution is
  unavailable because this host has no `.NET` SDK. No package install or
  network fetch was attempted. G2 exit evidence therefore remains blocked.

## Remaining required evidence

Before G2 can exit, run the C# vectors twice with networking denied, re-run the
full Python/Swift/C# conformance and long-duration matrix from clean scratch,
complete crash/recovery/fencing/quiescence and identity rejection coverage,
scan the final exact tree, and obtain independent staff and security/privacy
approval bound to that final commit/tree. This checkpoint does not authorize
provider, device, capture, hosted, deployment, merge, or release work.
