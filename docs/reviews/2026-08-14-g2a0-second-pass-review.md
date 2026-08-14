# G2-A0 Second-Pass Exact-Tree Review Record

**Review date:** 2026-08-14

**Scope:** Independent second-pass read-only review of the conditional
documentation-only G2-A0 correction commit. No source, hosted, provider,
device, credential, network, capture, pilot, merge, deploy, or release activity
was authorized or performed.

## Exact binding reviewed

- Worktree: `/private/tmp/transcriptor-native-launch-salvage-audits`
- Branch: `codex/native-launch-salvage-audits`
- Reviewed commit: `d0a93dfdf7dda5e0e536f79911f793145b3c36c1`
- Reviewed tree: `f5f3ee2e25673a46d9e94524f761ba9e6babbc06`
- Reviewed parent: `3d8148fc35b33c3fee0303e16396fe255ea8e520`
- Worktree at review: clean

This record is a later documentation artifact and therefore is not part of the
reviewed tree above. A publication commit that carries this record must be
rebound to its own new commit/tree before any approval is treated as current.

## Panel decision on the reviewed tree

**Approve with conditions for the documentation-only G2-A0 direction.** No P0
was found. The direction remains non-operative for source and hosted work.

### Closed as design

- Reachable/unreachable pre-ack `local_privacy_discard`, durable discard,
  `effect_pending`, deletion generations, and late-callback semantics.
- Bounded privacy claim limited to absence of T.A.R.S.-controlled durable raw
  audio, with allocator/transport/OS/crash/provider surfaces deferred to G3A/G4.
- Provider `runtimeEpoch`, single-use effect token, egress fence,
  provider-close acknowledgement, and persistent non-success
  `effect_quiescence_required`.
- Frozen-versus-unresolved Protocol 0002 disposition and no-source authority.

### Required before any source-corridor request

- Define `audio.forwarded` acknowledgements and reconnect payloads as ordered
  disjoint intervals plus any derived contiguous prefix; a scalar must not cross
  an unresolved gap.
- Define byte-level terminal and transcript-segment identity ordering and
  encoding: tuple sort `(sequence, firstSample, lastSampleExclusive, coverageId)`
  ascending, one NUL-separated UTF-8 field prefix, a big-endian uint32 count,
  and big-endian uint32 UTF-8 byte length before each ID; reject overlap,
  duplicates, non-NFC IDs, embedded NUL, and length overflow.
- Add G2 simulator vectors for interval acknowledgement/reconnect, canonical
  identity bytes, runtime-epoch mismatch, egress-fence publication,
  provider-close/owner-termination acknowledgement loss, and persistent
  `effect_quiescence_required`.
- Align conformance evidence to Python, Swift, and C# plus 60/90/120-minute
  simultaneous two-source cases.
- Bind renewed architecture and security/privacy approval to the final
  publication commit/tree, not this historical reviewed tree.

### Separate later product/hosted gates

G3C must deliver the state/privacy UX artifact, accessible copy matrix,
browser-close/companion-disappearance behavior, deletion retry semantics,
English/pt-BR evidence, and reconciliation of the stale v1 candidate disclosure
script. G3A/G4 must directly evidence authentication, distributed ingress/DoS,
parser limits, spend controls, provider retention/deletion, and physical
transient-artifact surfaces. None of these gates is granted by this record.

## Attestations

Independent Staff Engineering, Security/Privacy Engineering, and Product UI/UX
reviewers each returned **approve with conditions** on the reviewed commit/tree
above. Their residual conditions are intentionally preserved here; no source
authorization or launch claim follows from the conditional decision.
