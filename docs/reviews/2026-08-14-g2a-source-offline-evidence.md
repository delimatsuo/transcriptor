# G2-A source-only offline evidence checkpoint

Status: implementation checkpoint; the C# availability gate now passes, but
G2 remains incomplete pending the full three-language matrix and renewed
exact-tree review.

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
  `6668bd804c4b191943fa344cfab1ee3cf2b6879a`, tree
  `579d5f0f196c39cc44346e8cbbf04ee1b93722cc`. It includes the canonical-JSON
  and scratch-build remediation from `8561073bc4a20aa14d3beae89618236ae4fc11ce`
  and aligns the canonical control envelope with the governing 65,536-byte
  limit. This evidence child and its source parent require fresh exact-tree
  review.
- C# execution checkpoint: commit
  `fb77990891fa3cbfe4da87de4726ecc7ad78308a`, tree
  `5c3245d183957bc8f8b93518780c1ef46c642d66`. It executes the restored and
  compiled C# assembly directly from scratch and removes the compiler warning
  exposed by the first SDK-backed run. This evidence child and source parent
  require renewed exact-tree review.
- Worktree: `/private/tmp/transcriptor-native-g2a-source`.
- Branch: `codex/native-g2a-source`.
- Worktree was clean at the source checkpoint. No protected checkout, PR #8,
  N11D-C worktree, provider, device, cloud, credential, capture, deployment,
  merge, release, or candidate-data activity occurred. The owner authorized a
  Homebrew download to install the keg-only `.NET 8` SDK; all qualification
  processes themselves remained network-denied.

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
The 65,536-byte canonical control boundary is accepted exactly and a
65,537-byte encoding is rejected.

## Verification

- `git diff --check`: passed.
- Full inherited Phase 1A guarded wrapper: **79 Python tests and 7 Swift tests
  passed twice**, networking denied and scrubbed environment.
- Complete G2-A guarded wrapper: **25 Python tests, 7 Swift tests, and 11 C#
  vectors passed twice** in network-denied, scrubbed environments.
- Shared Python/Swift canonical v2 vectors: passed, including atomic coverage,
  terminal coverage, and transcript segment identities.
- G2 artifact scan: passed against the exact authorization range
  `8398fa8b345e326320e54d2a598977e47ee67fa7..HEAD`, with zero artifacts,
  forbidden imports, and out-of-scope paths.
- C# vector runner: `.NET SDK 8.0.130` and runtime `8.0.30` compiled and ran
  the conventional dependency-free entry point. Four positive identity checks
  and seven negative validation checks passed twice. The guarded C# path
  clears all NuGet package sources, restores only from installed SDK packs,
  creates restore/build/output directories only under a fresh scratch root,
  executes the resulting DLL directly from that root, and deletes the root
  after each run. The final artifact scan found no `bin` or `obj` residue.

## Remaining required evidence

Before G2 can exit, complete the remaining cross-language rejection,
crash/recovery, fencing, quiescence, quota, custody, deletion, identity, and
long-duration matrix from clean scratch; scan the final exact tree; and obtain
independent staff and security/privacy approval bound to that final
commit/tree. This checkpoint does not authorize provider, device, capture,
hosted, deployment, merge, or release work.
