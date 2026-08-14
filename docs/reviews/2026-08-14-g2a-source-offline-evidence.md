# G2-A source-only offline evidence candidate

Status: candidate final G2 source/offline evidence. Source implementation and
clean-scratch qualification are complete at the exact source parent below.
Independent staff and security/privacy approval of the enclosing evidence
commit remains required before G2 exit may be recorded.

## Exact binding and authority

- Owner-authorized G2-A source baseline: commit
  `8398fa8b345e326320e54d2a598977e47ee67fa7`, tree
  `419feca4702be389c8f85129e0face1afe912419`.
- Candidate final source checkpoint: commit
  `8402d2dc629bd02952fe03189b0af1bf8e9afe6a`, tree
  `f15444552f2ca9e48f3b581a6faa54cedaafe0c7`.
- Framing/retry parity checkpoint: commit
  `2a10d6e6b107c473f4561ad65620c8de0787e8e8`, tree
  `0b5ccdbe75fe67a06e61ec8cc7b0aac414738235`.
- Lifecycle/bounds checkpoint reviewed with findings: commit
  `794a19152d14cbaf4a0d875f799b1864d1e5dc5e`, tree
  `30d6f9f77435d6171e45d489fa2da11933e55fd2`.
- Worktree: `/private/tmp/transcriptor-native-g2a-source`.
- Branch: `codex/native-g2a-source`; local and unpushed.

The owner authorized this source-only offline corridor and separately
authorized the Homebrew download needed to install the keg-only .NET 8 SDK.
No provider, device, capture, hosted service, credential, cloud, deployment,
merge, release, customer, participant, candidate, or protected-checkout action
was performed. Qualification processes ran with networking denied.

## Scope

Changes from the authorization baseline remain confined to the approved
`companion/protocol/` source/test/guard corridor and this evidence record. The
implementation is pure and memory-only:

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
  fence provider/owner capabilities and matching actor identities;
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
  idempotent two-pass absence verification, and no success while a modeled
  store or participant remains.

The 60-, 90-, and 120-minute matrix runs simultaneous generated 8 kHz mono and
48 kHz stereo sources at the adversarial 20 ms minimum. Every event charges
4,100 metadata-plus-prefix bytes and the exact payload against source,
session, tenant, and process rows, then releases modeled custody. Python,
Swift, and C# all finish with zero retained quota custody.

## Verification at the candidate source checkpoint

- `git diff --check`: passed.
- `companion/protocol/scripts/run_offline_guard.sh`: passed twice with **93
  Python tests and 16 Swift tests** per deterministic pass under a scrubbed,
  network-denied environment.
- `PATH=/opt/homebrew/opt/dotnet@8/bin:/usr/bin:/bin:/usr/sbin:/sbin
  companion/protocol/scripts/run_g2_offline_guard.sh`: passed twice with **39
  Python tests, 16 Swift tests, and 53 C# vectors** per deterministic pass under
  the reviewed network-denied sandbox.
- `companion/protocol/scripts/run_g2_artifact_scan.sh`: passed with **zero
  artifacts, forbidden imports, and out-of-scope paths**.
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
opaque authority, strict restore validation, and foreign/forged/stale/wrong-
actor vectors. Because these changes create a new exact tree, none of the prior
blocked reviews is approval of this candidate.

## Claim ceiling and remaining gate

This evidence can support only protocol-v2 closure under deterministic,
generated-byte, source-only offline tests after renewed exact-tree staff and
security/privacy approval. It does not prove provider delivery or deletion,
hosted authentication/tenancy/rate enforcement, storage transactions or
absence, physical RAM erasure during OS suspension, macOS or Windows capture,
permissions, packaging, deployment, pilot behavior, launch readiness, merge,
or release.

The only remaining G2-A source gate asserted by this record is independent
staff and security/privacy review of the enclosing evidence commit and its
exact source parent with no unresolved P0–P3 finding. Any correction expires
that approval and requires a new exact commit/tree review. Later G3A gateway
and G3B native-capture work remain separately planned and separately
authorized.

The evidence file binds its exact source parent above. Its enclosing evidence
commit cannot self-identify without changing itself; final binding therefore
must be supplied by the independent reviewer dispositions and the exported
offline bundle manifest, each naming the same enclosing commit and tree.
