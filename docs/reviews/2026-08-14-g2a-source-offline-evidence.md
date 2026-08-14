# G2-A source-only offline evidence candidate

Status: candidate final G2-A source/offline evidence, published for review in
PR #11. Source implementation and clean-scratch qualification are complete at
the exact source parent below. The PR is cumulative with the documentation
history published in PR #10; PR #10 must not be merged separately. G2 exit
is gated by the sole owner's exact-tree architecture/security/privacy
attestation, deterministic offline guards, artifact scan, and claim ceiling;
no team reviewer, GitHub approval, or remote CI check is assumed.

## Exact binding and authority

- Owner-authorized G2-A source baseline: commit
  `8398fa8b345e326320e54d2a598977e47ee67fa7`, tree
  `419feca4702be389c8f85129e0face1afe912419`.
- Candidate final source checkpoint: commit
  `14b9d77526b879209af8af2b87c62e25a950d63f`, tree
  `2d2cba5e35a2ca49f3b880165dab0912b4ae7092`.
- Framing/retry parity checkpoint: commit
  `2a10d6e6b107c473f4561ad65620c8de0787e8e8`, tree
  `0b5ccdbe75fe67a06e61ec8cc7b0aac414738235`.
- Lifecycle/bounds checkpoint reviewed with findings: commit
  `794a19152d14cbaf4a0d875f799b1864d1e5dc5e`, tree
  `30d6f9f77435d6171e45d489fa2da11933e55fd2`.
- Worktree: `/private/tmp/transcriptor-native-g2a-source`.
- Branch: `codex/native-g2a-source`; published at the source parent/evidence
  checkpoint in PR #11, targeting `codex/native-launch-boundary`.
- Publication state at this record: PR #11 is open, ready for review, clean,
  and has no submitted GitHub reviews or reported CI checks; this is expected
  for the single-engineer repository and is not a G2 blocker. PR #10 remains
  an open draft containing the cumulative documentation history; it must not
  be merged separately from PR #11.
- Owner governance attestation: the sole project engineer directed this
  correction to remove the team-review assumption and accepts owner-performed
  architecture/security/privacy review backed by the exact guards, artifact
  scan, manifest, and claim ceiling below.

The owner authorized this source-only offline corridor and separately
authorized the Homebrew download needed to install the keg-only .NET 8 SDK.
No provider, device, capture, hosted service, credential, cloud, deployment,
merge, release, customer, participant, candidate, or protected-checkout action
was performed. Qualification processes ran with networking denied.

## Scope

Changes from the authorization baseline remain confined to the approved
`companion/protocol/` source/test/guard corridor, the owner-authorized protocol
closure plan, and this evidence record. The implementation is pure and
memory-only:

- Python supplies the canonical reference model, parser, retry ledger,
  terminal projection, admission/quota/custody/transport oracles, provider-
  effect fence, lifecycle projection, and resumable deletion simulator.
- Swift supplies pure framing, identities, retry HMAC, rate-derived custody,
  quota, provider-effect, lifecycle, pre-auth transport, and deletion reducers.
- C# supplies a dependency-free executable that validates the same canonical
  frame/HMAC/identity/rejection cases plus custody, quota, provider-effect,
  pre-auth transport, deletion, and long-duration vectors. It is not a WASAPI
  implementation.

There are no backend, frontend, native-capture, route, CI, provider adapter,
filesystem-persistence, network, cloud, credential, or deployment changes.

## Exact offline matrix

The candidate source tree covers:

- canonical v2 binary frames, exact metadata/payload lengths, lowercase
  SHA-256 digests, typed `aevt_` identities, and the session-scoped retry HMAC
  input in Python, Swift, and C#;
- canonical JSON ordering and byte comparison; duplicate/extra fields;
  invalid UTF-8, lone-surrogate/non-NFC/NUL strings; negative, fractional,
  exponent, boolean, unsafe, overflowing, and non-canonical numeric forms;
  truncated, oversized, mismatched, and changed-content retries;
- exact atomic `acov_`, full-list terminal `covr_`, and transcript `seg_`
  identities, including duplicate/overlapping/foreign coverage rejection and
  multiple final segments over one atomic chunk; every pair in a sparse
  interval set is checked for sample or sequence overlap;
- durable owner/effect identity, single invocation, immutable journal before
  forwarded release, runtime-epoch and egress-fence recovery, foreign/stale
  opaque-capability rejection, terminal non-reopening, and positive provider/
  owner quiescence; the custody ledger binds each live audio range to its
  original opaque owner/effect authority before invocation, prevents generic
  forwarding around that binding, and prevents a locally released range from
  acquiring a new effect; quiescence acknowledgements require distinct current-
  fence provider/owner capabilities and matching actor identities; restart
  snapshots contain no provider execution capability, restored owned effects
  enter fail-closed quiescence, and cross-field state invariants reject forged
  invocation or journal history;
- exact durable-discard gap identity and idempotence, forwarding/discard
  conflict rejection, local privacy-timeout release without advancing the
  forwarded watermark, no discard claim while a provider effect is pending,
  atomic cancellation-and-discard only for an uninvoked prepared effect,
  rejection of invocation/callback after that terminal cancellation,
  original-effect journal resolution to forwarded coverage or post-quiescence
  resolution to an exact ambiguous-effect gap, the 10-second reconcile
  threshold, and 30-second absolute custody expiry scheduling model;
- 20–250 ms per-event alignment, 64,000-byte event limit, sample-rate-derived
  two-second frame/raw-byte limits, 100-event/metadata/reservation/resident
  limits, and fail-before-mutation oversized-event cases;
- source/session/tenant/process token buckets, retry token consumption,
  active-session/attempt/resident reservations, pre-auth handshake/IP/header/
  auth-event/receive-buffer limits, authenticated parser allocation, and
  backward/deadline failure;
- non-enumerating authentication, expiry, revocation, audience, tenant, actor,
  enrollment, session, stream, generation, fence, version, notice, and legal-
  basis rejection;
- origin-separated physical/transport/coverage lifecycle state with
  conservative derived completion and upgrade gating; and
- deletion admission fencing, worker/connection/effect quiescence, late-
  callback rejection, crash-copy/resume, injected store failure, ordered and
  idempotent two-pass absence verification, complete restore-state validation,
  and no success while a modeled store or participant remains.

The 60-, 90-, and 120-minute matrix runs simultaneous generated 8 kHz mono and
48 kHz stereo sources at the adversarial 20 ms minimum. Every event charges
4,100 metadata-plus-prefix bytes and the exact payload against source,
session, tenant, and process rows, then releases modeled custody. Python,
Swift, and C# all finish with zero retained quota custody.

## Verification at the candidate source checkpoint

- `git diff --check`: passed.
- `companion/protocol/scripts/run_offline_guard.sh`: passed twice with **95
  Python tests and 16 Swift tests** per deterministic pass under a scrubbed,
  network-denied environment.
- `companion/protocol/scripts/run_g2_offline_guard.sh`: passed twice with **41
  Python tests, 16 Swift tests, and 55 C# vectors** per deterministic pass under
  the reviewed network-denied sandbox.
- `companion/protocol/scripts/run_g2_artifact_scan.sh`: passed with **zero
  artifacts, forbidden imports, and out-of-scope paths**.
- The exact baseline-to-HEAD path set is 26 paths: the approved protocol
  corridor plus `docs/plans/2026-08-13-protocol-closure-entry-plan.md` and this
  evidence record.
- .NET SDK `8.0.130` and runtime `8.0.30` compiled and executed the C# DLL from
  fresh scratch twice. NuGet sources were cleared; restore/build/output stayed
  in scratch; no `bin`, `obj`, `.build`, `.swiftpm`, `__pycache__`, payload
  file, credential, endpoint, or package-resolution artifact remained.

Staff and security/privacy independently reviewed `794a191...` /
`30d6f9f...` and blocked it on cross-effect Swift tokens, terminal effect
recovery, conflicting discard replay, per-event custody bypass, negative quota
rows, backward pre-auth time, arbitrary Swift retry metadata, Swift overflow
traps, stale evidence, and incomplete cross-language state cases. Source commit
`92a8e93...` resolved that round. Their review of the following evidence commit
`3d58574...` then found terminal recovery and deletion-participant acceptance
gaps in Python, typed retry-input drift, unchecked/fractional numeric domains,
and a cross-language race between local raw-audio release and a prepared or
in-flight provider effect. Source commits `c9bae11...` and `830025e...` resolved
that second round and bound range resolution to the original effect
owner/object. Review of evidence commit `a1971e6...` then found a
prepared-effect discard/invocation race, pre-fence quiescence acknowledgements,
non-adjacent interval overlap, and permissive Python numeric helper inputs.
Source commit `381f5bb...` resolves that third round with explicit
Python/Swift/C# rejection and recovery cases. Review of evidence commit
`9441bfa...` then found forgeable value-only effect authority, quiescence
acknowledgements without current-fence actor capabilities, permissive Python
numeric/restore coercions, and an identity omission on idempotent prepared-
discard replay. Source commit `8402d2d...` resolves that fourth round with
opaque authority, strict scalar validation, and foreign/forged/stale/wrong-
actor vectors. Review of evidence commit `ed6dc7b...` then found that Python
restart restoration recreated a provider execution capability, accepted
inconsistent effect and deletion lifecycle states, and that C# allowed an
ownerless effect to mint owner-quiescence authority. Source commit
`14b9d77...` resolves that fifth round by excluding execution capabilities from
snapshots, forcing restored owned effects into quiescence, validating complete
effect/deletion state proofs, and rejecting ownerless C# recovery. Because
these changes create a new exact tree, none of the prior blocked reviews is
approval of this candidate.

The initial publication review of PR #11 at the prior evidence head found no
source P0-P3 defect but did find package-level corrections: stale publication
metadata, unsupported approval wording, and unresolved PR #10/PR #11 stacking
semantics. Those historical team-review dispositions are retained for
provenance only; this repository has one engineer, so the owner now performs
the architecture/security/privacy self-review and exact-tree attestation.
GitHub review and CI records are informative only and are not exit gates.

## Claim ceiling and remaining gate

This evidence can support only protocol-v2 closure under deterministic,
generated-byte, source-only offline tests after the sole owner's exact-tree
architecture/security/privacy attestation. It does not prove provider delivery
or deletion,
hosted authentication/tenancy/rate enforcement, storage transactions or
absence, physical RAM erasure during OS suspension, macOS or Windows capture,
permissions, packaging, deployment, pilot behavior, launch readiness, merge,
or release.

The remaining G2-A publication gate asserted by this record is the sole
owner's exact-tree architecture/security/privacy attestation of the enclosing
evidence commit and its exact source parent, with deterministic guards,
artifact scan, preserved manifest, and no unresolved P0-P3 finding. Any
correction expires that attestation and requires a new exact commit/tree
review. Later G3A gateway and G3B native-capture work remain separately
planned and separately authorized.

The evidence file binds its exact source parent above. Its enclosing evidence
commit cannot self-identify without changing itself; final binding therefore
must be supplied by the owner's attestation and exported offline bundle
manifest, each naming the same enclosing commit and tree.
