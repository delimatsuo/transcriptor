# G2-A source-only offline evidence checkpoint

Status: implementation checkpoint; G2 is not complete because C# execution is
blocked by the unavailable .NET SDK on this host.

## Exact binding

- Authorization: direct owner authorization for G2-A source-only offline work
  from publication commit `8398fa8b345e326320e54d2a598977e47ee67fa7`, tree
  `419feca4702be389c8f85129e0face1afe912419`.
- Source implementation checkpoint: commit
  `bc2c22a9d2d3c9f840ade771ac82b2faca0f1e52`, tree
  `61687a51dbc3f7513df3417895db4ee6ccd49965`.
- Independently approved prior checkpoint: commit
  `0c9c23313927577489169132932c10ad4a91bdcb`, tree
  `d0fa27c926789db4b8b64f3cdc6f3861b1a51c5c`. Staff and security/privacy
  review reported no P0/P1 findings on that exact tree.
- Independently approved expanded checkpoint: commit
  `1bf467d3ed9cfb7d45ea33c3d90498dc527a8685`, tree
  `0f3561eb7cb43c2607ea74e887554a538d3cb788`, with source parent
  `e71d8e471d92ddbd7c28a036f043b598ab0c73c1`. Staff and security/privacy
  review reported no P0/P1 source-semantic findings and blocked G2 exit at the
  unavailable C# and full-matrix evidence gates.
- Post-review offline-hardening source checkpoint: commit
  `8561073bc4a20aa14d3beae89618236ae4fc11ce`, tree
  `2b8879222d22ac363df04de77ab93c0b37a2b919`. It closes the reviewers'
  offline-remediable canonical-JSON and build-artifact P2s. This evidence
  child and its source parent require fresh exact-tree review.
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
bounded runs. Forwarded coverage cannot be replaced by a gap, and transcript
segments require forwarded atomic coverage. Python, Swift, and the C# source
now reject duplicate, same-sequence, adjacent, and nonadjacent overlapping
atomic lists; invalid segment bounds; non-NFC provenance; and NUL-bearing
identity fields. Python also exercises canonical-JSON rejection, uint64
boundaries, conflicting segment replay, sparse gap release, and stale effect
and deletion generations. Its bounded canonical parser rejects duplicate
keys, non-canonical ordering/whitespace/escaping, floats, negative and unsafe
integers, invalid UTF-8, lone surrogates, non-NFC and NUL-bearing strings,
oversized envelopes, and excessive nesting.

## Verification

- `git diff --check`: passed.
- Full inherited Phase 1A guarded wrapper: **79 Python tests and 7 Swift tests
  passed twice**, networking denied and scrubbed environment.
- G2-A guarded prefix: **25 Python tests and 7 Swift tests passed twice** in
  the network-denied, scrubbed environment before the C# availability check.
- Shared Python/Swift canonical v2 vectors: passed, including atomic coverage,
  terminal coverage, and transcript segment identities.
- G2 artifact scan: passed against the exact authorization range
  `8398fa8b345e326320e54d2a598977e47ee67fa7..HEAD`, with zero artifacts,
  forbidden imports, and out-of-scope paths.
- C# vector runner: a conventional dependency-free entry point contains four
  positive identity checks and seven negative validation checks, but neither
  compilation nor execution is available because this host has no `.NET`
  SDK or C# compiler. The guarded C# path now clears all NuGet package sources,
  creates restore/build/output directories only under a fresh scratch root,
  and deletes that root after each run. The G2 wrapper exited `2` after
  reporting `dotnet SDK unavailable` twice. No package install or network
  fetch was attempted. G2 exit evidence therefore remains blocked.

## Remaining required evidence

Before G2 can exit, compile and run the C# vectors twice with networking
denied, re-run the full Python/Swift/C# conformance and long-duration matrix
from clean scratch, scan the final exact tree, and obtain independent staff
and security/privacy approval bound to that final commit/tree. This checkpoint
does not authorize provider, device, capture, hosted, deployment, merge, or
release work.
