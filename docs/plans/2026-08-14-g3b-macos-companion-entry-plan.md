# G3B macOS Companion Entry Plan

Status: documentation-only draft. This plan does not authorize source
implementation, device activation, system-audio or microphone capture,
provider calls, credentials, deployment, real audio, candidate data, merge,
or release.

## 1. Exact anchor and authority

This plan is drafted from the merged G2-A source/offline checkpoint:

- merge commit: `65051a8863ec9b3430318b63b091369d668cd1b0`;
- merged PR head: `9c082516b07e733b2536565bd38aa2f95f09dcd5`;
- merged tree: `b5e0358b286e5f71e731f17653acffaf78aaeebd`;
- frozen protocol source checkpoint: `14b9d77526b879209af8af2b87c62e25a950d63f`;
- frozen protocol source tree: `2d2cba5e35a2ca49f3b880165dab0912b4ae7092`.

The repository has one engineer. The owner performs the architecture,
security/privacy, scope, accessibility, and claim-ceiling review for this
plan and records an exact-tree attestation. No team reviewer, GitHub approval,
or remote CI check is assumed. A later source authorization must name the
exact implementation commit, tree, native API choice, and allowed paths
before any G3B source write.

## 2. Purpose and claim ceiling

G3B is the offline macOS companion track for independently capturing the
microphone and system-audio sources, reducing them into the frozen v2
protocol, and exercising route, permission, lifecycle, custody, deletion, and
health behavior with simulators and generated controlled fixtures.

G3B evidence may claim only deterministic source/offline behavior and, later,
named controlled-fixture behavior at an exact reviewed tree. It may not claim
physical erasure, native-device reliability, macOS permission success in the
field, provider delivery/deletion, hosted integration, pilot readiness, real
audio safety, candidate-data safety, deployment readiness, or launch
readiness. Physical-device fixture evidence is a later separately authorized
gate; this draft performs no device action.

## 3. Frozen boundary

The companion must preserve the ADR 0003 ownership split:

- the companion owns physical capture, microphone/system-audio permissions,
  device and route health, timestamps, framing, per-source sequence,
  bounded in-memory raw-audio custody, local `local_privacy_discard`
  zeroization, authoritative physical pause/stop/degraded events, short-lived
  enrollment storage, and content-free diagnostics;
- the gateway owns durable transport, coverage, finalization, deletion,
  provider-effect state, durable gaps, and top-level completion labels;
- companion events never assert durable coverage, finalization, completion,
  or deletion;
- `audio.forwarded` is the only successful provider-forwarding release
  authority; local discard and emergency/privacy-timeout zeroization create
  visible gaps without attributing pending effects to discard;
- `physicalCaptureState`, `transportState`, and `coverageState` stay
  independent, with source-health sub-axes for microphone and system audio;
  and
- no virtual audio device, default-route activation, BlackHole, VB-CABLE,
  PyAudioWPatch, or automatic/manual virtual-device fallback is packaged,
  onboarded, supported, or used as rollback.

## 4. Entry conditions before source authorization

The owner must record all of the following against one exact documentation
tree before requesting G3B source authority:

1. This plan and the G3A plan have been reviewed together for framing,
   custody, state ownership, deletion ordering, and no-cross-track claims.
2. The G2-A merge and source checkpoint above remain unchanged; any protocol
   change creates a new plan review and invalidates dependent evidence.
3. The native system-audio API and supported macOS floor are selected in a
   separately recorded decision. The choice is not inferred from an old,
   dirty, or virtual-device worktree.
4. Microphone and system-audio capture are modeled as independent sources,
   each with explicit permission, device/route health, sequence, timestamp,
   overflow, and degraded-state behavior.
5. The later direct authorization names exact companion source, reducer,
   simulator, test, packaging, and guard paths. No N11D-C worktree is resumed
   mechanically, and no protected or unrelated worktree is edited.
6. Any later controlled-fixture run names the Mac matrix, fixture class,
   networking/provider-disabled environment, rollback, and stop conditions.

## 5. Proposed G3B work packages

### A. Capture boundary and source health

- Keep microphone and system audio independent from admission through frame
  identity, buffering, retry, gap, and health projection.
- Model permission denial/revocation, device loss/change, route changes,
  sleep/wake, interruption, forced termination, and recovery without silently
  claiming healthy capture.
- Emit only companion-owned physical states and content-free diagnostics;
  preserve last provable sequence/sample boundaries and explicit overflow or
  unknown-end gaps.
- Reject unsupported routes and stale capture generations before payload
  mutation.

### B. Framing, custody, and protocol adaptation

- Use the frozen v2 canonical framing, typed identity, retry commitment,
  rate-derived custody, and quota/resend bounds from G2.
- Bound in-memory raw-audio custody by the sample-rate-scaled duration/frame/
  byte contract for both mono/stereo sources and mixed-rate operation.
- Release local bytes only under the approved forwarding, durable-discard, or
  emergency/privacy-timeout semantics; local release never advances durable
  coverage or claims provider forwarding.
- Preserve exact gap boundaries on overflow, route loss, forced termination,
  reconnect, discard, and unknown-end cases.

### C. Enrollment and local secret handling

- Store only short-lived enrollment material in Keychain; never embed a
  permanent provider credential, project selection, endpoint, or cloud token.
- Fence enrollment, lease, session, stream, and capture-generation changes so
  stale gateway responses cannot revive a prior physical capture lane.
- Keep credential and endpoint values out of logs, crash output, fixtures,
  artifacts, and diagnostics.

### D. Lifecycle, deletion, and local kill control

- Implement pause, stop, finalization, upgrade, deletion quiescence, and
  immediate local kill as explicit state transitions with idempotent recovery.
- On local privacy action, clear unforwarded bytes immediately and record the
  exact or unknown local boundary; do not claim durable discard until the
  gateway acknowledges its durable gap.
- On deletion, stop new capture/reconnect work, fence late callbacks and
  stale gateway responses, wait for local workers/streams to quiesce, and
  preserve truthful non-success state when quiescence is unproven.
- A companion snapshot must not recreate gateway/provider execution authority.

### E. Health, accessibility, and packaging boundary

- Expose content-free per-source health with icon-plus-text and accessible
  labels; never rely on color or sound alone.
- Define recovery copy and focus behavior for permission denial, device loss,
  sleep/wake, overflow, degraded ranges, pause, stop, finalization, deletion,
  and browser-close/quit guidance. Product-state and pt-BR copy reconciliation
  remains the separately versioned G3C docs/UI gate.
- Inspect every release candidate for virtual-device activation, onboarding,
  support, rollback, default-route instructions, provider credentials, and
  undeclared payload/build artifacts; fail closed on any finding.

## 6. Verification matrix

The later source authorization must bind deterministic simulator/reducer and
fault tests for:

- permission denial/revocation, device/route changes, sleep/wake,
  interruption, forced termination, reconnect, stale leases, and capture
  generations;
- independent microphone/system-audio sequence, timestamps, framing,
  mixed-rate custody, overflow, unknown-end, and exact resend gaps;
- local discard, durable-discard acknowledgement, emergency/privacy-timeout
  zeroization, pending-effect fencing, and no false coverage/forwarding claims;
- pause/stop/finalization/upgrade/deletion ordering, local worker quiescence,
  late callbacks, idempotent restart, and snapshot authority exclusion;
- Keychain-only short-lived enrollment material and scans for credentials,
  endpoints, project names, payload bytes, and package artifacts;
- network-denied simulator runs twice, followed only by separately authorized
  generated controlled fixtures with providers disabled; and
- accessibility and packaging checks for the named macOS matrix, including
  VoiceOver, keyboard-only, 200-percent zoom, focus restoration, and
  pt-BR comprehension once the G3C copy artifact is approved.

The exit record must state the exact repository/worktree, commit/tree, native
API and OS floor, path set, fixture class, environment, commands, artifact
hashes, rollback, and claim ceiling. It must not turn simulator or controlled
fixture evidence into a physical-device, provider, hosted, pilot, or launch
claim.

## 7. Stop conditions

Stop and return to planning if G3B requires a virtual audio device or default
route, a real participant/candidate recording, provider or cloud credentials,
network-enabled package fetch, a device action outside a separately approved
fixture gate, a new API/OS floor without a docs decision, durable raw-audio
storage, physical-erasure language, product/UI implementation before G3C, or
any path outside the later direct authorization.

## 8. Next action

The next action is owner review of this G3B plan together with the G3A plan,
followed—if approved—by a separate native-API/source-implementation
authorization naming exact paths. This draft itself authorizes no source,
device, provider, cloud, or hosted action.
