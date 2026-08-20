# G2-A0 Exact-Tree Panel Review Record

**Review date:** 2026-08-14

**Owner authorization:** Deli Matsuo, direct docs-only G2-A0 authorization on
2026-08-13; the follow-up remains documentation-only.

**Reviewer attestations:** Independent Staff Engineering, Security/Privacy
Engineering, and Product UI/UX panel roles. Each reviewer rebound the exact
commit/tree below read-only; their conditional decisions are recorded here.

**Scope:** Documentation-only review of the owner-authorized G2-A0 governing
amendment. No source, hosted, provider, device, credential, network, capture,
pilot, merge, deploy, or release activity was authorized or performed.

## Exact binding reviewed

- Worktree: `/private/tmp/transcriptor-native-launch-salvage-audits`
- Branch: `codex/native-launch-salvage-audits`
- Commit: `3d8148fc35b33c3fee0303e16396fe255ea8e520`
- Tree: `901664f8578358b75628146c6c8a032ab75b991e`
- Parent: `98f7f271d881485775b8e5b9d2294b1aa1b3eed6`
- Worktree at review: clean

The amendment paths were exactly:

- `docs/architecture/0002-companion-stream-protocol.md`
- `docs/architecture/0003-native-capture-launch-boundary.md`
- `docs/plans/2026-08-13-native-capture-launch-roadmap.md`
- `docs/privacy/data-flow-retention-contract.md`

The panel also read the protocol closure plan and the existing product state
contract as dependency/context artifacts. It did not modify the product state
contract.

## Panel decision

**Approve with conditions for the documentation-only G2-A0 direction.** No P0
was found. The conditions below are P1 source-corridor blockers and later
hosted/pilot gates; they do not authorize implementation or make a provider,
device, or privacy outcome true.

### Staff engineering

Approve with conditions. Required before a source corridor:

- gap-aware disjoint forwarding release after an earlier gap;
- terminal identity bound to the complete ordered atomic-coverage list;
- provider egress-fence/recovery and persistent non-success
  `effect_quiescence_required` state; and
- explicit frozen-versus-unresolved disposition for Protocol 0002 values.

### Security and privacy

Approve with conditions. Required before a source corridor or hosted/pilot
claim:

- unambiguous pre-ack `local_privacy_discard`, durable discard,
  `effect_pending`, deletion, and late-callback semantics;
- explicit limits on claims about allocator/transport/OS/crash/provider
  retention and direct artifact-scan evidence;
- provider stream cancellation/egress fencing and deletion escalation; and
- later exact-environment authentication, distributed DoS, parser-resource,
  quota, spend, provider-retention, and backup/deletion evidence.

### Product UI/UX

Approve with conditions. G3C must be a separately versioned docs-only artifact
with Product UI/UX and accessibility review, reconciling legacy state labels,
local discard versus `effect_pending`, deletion/quiescence, browser-close and
companion-disappearance behavior, accessible copy, and English/pt-BR fault
coverage before UI implementation.

## Required follow-up

The amendment is not source-ready until a new exact commit/tree incorporates
the semantic corrections above and receives renewed architecture and
security/privacy approval. The G3C product-state/privacy UX artifact remains a
separate gate. Until then, retain the no-source, no-hosted, no-provider,
no-device, no-candidate-data, no-merge, no-deploy, and no-release boundaries.

This record is a content-free role-based review attestation, not a claim that
raw-audio physical erasure, provider deletion, hosted authentication, or pilot
privacy has been proven.
