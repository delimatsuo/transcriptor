# G3A Minimal Gateway Entry Plan

Status: documentation-only draft. This plan does not authorize source
implementation, hosted activation, provider calls, credentials, deployment,
real audio, candidate data, merge, or release.

## 1. Exact anchor and authority

This plan is drafted from the merged G2-A source/offline checkpoint:

- merge commit: `65051a8863ec9b3430318b63b091369d668cd1b0`;
- merged PR head: `9c082516b07e733b2536565bd38aa2f95f09dcd5`;
- merged tree: `b5e0358b286e5f71e731f17653acffaf78aaeebd`;
- frozen protocol source checkpoint: `14b9d77526b879209af8af2b87c62e25a950d63f`;
- frozen protocol source tree: `2d2cba5e35a2ca49f3b880165dab0912b4ae7092`.

The repository has one engineer. The owner performs the architecture,
security/privacy, scope, and claim-ceiling review for this plan and records an
exact-tree attestation. No team reviewer, GitHub approval, or remote CI check
is assumed. A later source authorization must name the exact implementation
commit, tree, and allowed paths before any G3A source write.

## 2. Purpose and claim ceiling

G3A is the minimal authenticated gateway track that exercises the frozen v2
protocol against generated or synthetic inputs. It exists to prove gateway
authority, bounded custody, forwarding/coverage state, provider-effect
fencing, deletion ordering, and content-free operations in an isolated test
environment.

G3A evidence may claim only source/offline or isolated synthetic gateway
behavior at the exact reviewed tree. It may not claim provider delivery,
provider deletion, hosted authentication or tenancy, cloud quota/spend
safety, production reliability, real-audio handling, candidate-data safety,
physical erasure, deployment readiness, pilot readiness, or launch readiness.
Hosted activation and any real provider account remain G4 gates.

## 3. Frozen boundary

The gateway must preserve the v2 authority split from ADR 0003 and the merged
G2 artifact:

- the gateway owns server-derived user, organization, session, and stream
  authority, one fenced capture lease, transport/coverage state, quotas,
  forwarding journals, transcript-or-gap persistence, retention/deletion, and
  kill controls;
- admission is not a release; only `audio.forwarded` releases successfully
  forwarded raw audio;
- a discard CAS that wins before provider preparation creates an exact or
  honest unknown-end gap; an already-prepared or invoking effect keeps its
  original owner/fence and resolves as forwarded or ambiguous;
- companion events never assert durable coverage, finalization, completion, or
  deletion; `physicalCaptureState`, `transportState`, and `coverageState`
  remain independent axes;
- deletion enters `delete_quiescing`, fences admission/reconnect/effects and
  content writes, proves positive owner/stream/provider quiescence, rejects
  late callbacks, and emits `deleted` only after two independent absence
  passes; inability to prove egress or provider quiescence is non-success
  `effect_quiescence_required`/`delete_quiescing`; and
- logs, metrics, diagnostics, crash artifacts, and test outputs are
  content-free.

## 4. Entry conditions before source authorization

The owner must record all of the following against one exact documentation
tree before requesting G3A source authority:

1. This plan and the companion G3B plan have been reviewed together for
   protocol ownership, deletion ordering, quotas, and claim ceilings.
2. The G2-A merge and source checkpoint above remain unchanged; any protocol
   change creates a new plan review and invalidates dependent evidence.
3. The G3C product-state/privacy UX reconciliation remains separate and is not
   silently included in G3A.
4. The intended synthetic environment, account/resource names, data class,
   rollback, and stop conditions are named before any hosted test. No real
   project, provider account, candidate, participant, or customer resource may
   be selected.
5. The later direct authorization names the exact gateway source, test, guard,
   schema, and configuration paths. No backend, frontend, native-capture,
   route, deployment, credential, or protected-worktree path is implied here.

## 5. Proposed G3A work packages

### A. Authority and admission

- Derive user and organization membership from the authenticated server-side
  authority; never trust request-supplied tenancy.
- Issue short-lived enrollment and renewal material with revocation,
  generation, audience, actor, session, stream, and capture-generation
  binding.
- Enforce a current disclosure acknowledgement containing notice version,
  actor, session, organization, time, and legal basis before audio admission.
- Make REST, WebSocket, audio, transcript, notes, reports, exports, storage,
  and deletion authorization fail closed and non-enumerating.
- Admit at most one active fenced capture lease per session and reject stale
  generations without revealing resource existence.

### B. Bounded transport and custody

- Apply the frozen per-source/session/tenant/process limits for audio events,
  audio bytes, metadata/prefix bytes, bursts, duration, frame size, pending
  pre-auth handshakes, aggregate receive buffers, active sessions, streams,
  outstanding mutations, and provider attempts.
- Reject before queue allocation or custody mutation; retries consume their
  attempt budget and cannot grow custody with wall-clock duration.
- Keep transient objects content-free except for the narrowly bounded audio
  custody required by the frozen v2 semantics. No durable raw-audio artifact
  is created by the gateway.

### C. Provider-effect seam and forwarding

- Implement a deterministic provider adapter seam and rotation simulator with
  no network, credential, or provider account. The seam must model prepared,
  invoking, forwarded, ambiguous, discard, quiescence, and stale-callback
  outcomes under the original opaque owner/fence.
- Advance forwarding only after an immutable content-free journal record and
  contiguous interval validation.
- Preserve retry identity, exact typed ranges, full ordered terminal coverage
  identity, and the distinction between provider-effect outcome and discard.
- Keep real Google STT streaming, provider credentials, and hosted egress out
  of G3A; those belong to a separately authorized G4 implementation/evidence
  gate.

### D. Coverage and durable projection

- Persist idempotent transcript-or-gap outcomes for every known atomic range;
  preserve explicit unknown-end gaps.
- Reject duplicate, overlap, reorder, foreign, stale, changed-text, and
  cross-generation terminal claims.
- Keep multiple provider finals inside one chunk as distinct segment identities
  while preserving non-overlapping atomic audio coverage.
- Derive web-visible state only from the versioned three-axis precedence table;
  never assign top-level completion from a companion or provider event.

### E. Deletion and recovery

- Atomically increment deletion generation, publish the egress barrier, fence
  admission/reconnect/effects/content writes, and enter `delete_quiescing`.
- Require positive quiescence acknowledgements from every worker, connection,
  prepared/invoking effect, provider stream, and late-callback lane; heartbeat
  expiry alone is insufficient.
- Resume idempotently after each injected crash boundary without recreating
  execution authority from a snapshot.
- Run two absence passes over the approved T.A.R.S.-controlled inventory;
  unverified provider-retention or unavailable-store state remains retryable
  `deletion_failed` and never becomes `deleted`.

### F. Operations and fail-safe controls

- Provide content-free audit tombstones, metrics, diagnostics, kill switch,
  rollback, and non-success states for any missing fence or quiescence proof.
- Ensure rollback cannot re-enable a default route, virtual audio device,
  provider credential, or stale effect lane.
- Keep all generated artifacts in fresh scratch and fail the guard on payload,
  credential, endpoint, project, package-resolution, or undeclared-file drift.

## 6. Verification matrix

The later source authorization must bind deterministic tests for:

- authentication, disclosure, tenant/session/stream/capture-generation,
  revocation, replay, stale-fence, and non-enumerating failures;
- every quota row, burst, pre-auth handshake, receive buffer, and rejection
  before allocation/mutation;
- forwarding journal crash points, retries, provider rotation, ambiguous
  outcomes, discard races, stale callbacks, and effect recovery;
- exact interval ordering, transcript-or-gap terminalization, multiple finals,
  unknown ends, and idempotent replay;
- deletion fencing, positive quiescence, late-callback rejection, restart,
  two absence passes, unavailable stores, and `effect_quiescence_required`;
- deterministic generated-byte runs twice with networking denied, plus an
  isolated synthetic gateway run only after its environment is separately
  approved; and
- artifact scans over source, scratch, logs, metrics, crash output, backups,
  and package/build directories.

The exit record must state the exact repository/worktree, commit/tree, path
set, environment, data class, command results, artifact hashes, rollback, and
claim ceiling. It must not turn source/offline or synthetic evidence into a
hosted or provider claim.

## 7. Stop conditions

Stop and return to planning if G3A requires a real provider account or
credential, network-enabled package fetch, production or customer resource,
real audio/candidate data, an unbounded queue or retention exception, a
request-supplied tenant authority, a default route/virtual-device fallback,
provider deletion evidence not available in the approved synthetic boundary,
or any path outside the later direct authorization.

## 8. Next action

The next action is owner review of this G3A plan together with the G3B plan,
followed—if approved—by a separate direct source-implementation
authorization naming exact paths. This draft itself authorizes no source
change or hosted/provider action.
