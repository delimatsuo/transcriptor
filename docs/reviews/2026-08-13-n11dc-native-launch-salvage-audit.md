# N11D-C Native-Launch Salvage Audit

**Status:** Read-only audit complete; exact-artifact staff and security/privacy
review required before publication. Approval must be recorded outside this
document and bound to the published commit and tree.

**Date:** 2026-08-13

**Governing decision:**
`docs/architecture/0003-native-capture-launch-boundary.md`

**Audit rule:** The dirty worktree is preserved evidence, not an implementation
baseline or launch artifact. This audit grants no permission prompt, route
read, route mutation, capture, playback, process, network, provider, staging,
commit, cleanup, or worktree change.

## 1. Exact state binding

| Field | Bound value |
| --- | --- |
| Worktree | `/Volumes/Extreme Pro/myprojects/Transcriptor-worktrees/offline-companion-qualification` |
| Branch | `codex/offline-companion-qualification` |
| HEAD | `12e8b083d04fbab5bfaee28c8103da8675f25615` |
| HEAD tree | `19a387e25cd0a8f134f8bd6a80f4509fc6f0a286` |
| Index tree | `f988c2943641cb612155c3980b599262f281d2fd` |
| Full-index binary staged patch SHA-256 | `2b49ad2b638bf3c15bbb70e37b150032779f93ab5ad378088ad94e6c22436736` |
| Full-index binary unstaged patch SHA-256 | `7352f02b8be836f5e0193821b83e499d57d52db3b44c1733f18881dfefaf6dbc` |
| Working `Package.swift` SHA-256 | `49f6d918994d77ce00ce6463c4d9b9034a6b0b59aed6ec22786a8949b3cd5d38` |
| Working Swift source/test manifest SHA-256 | `44f7d187cd637ae20776bc41e617d2eaf616beca03f57ace5e1d657c3ae71724` |

The current committed lineage is:

- `319c4a8` — sealed N11D0 contracts
- `9b46f5d` — offline record custody adapter
- `cd770a6` — freeze N11D-B
- `0073850` — offline process-safety adapters
- `3d5df7f` — amend causal route custody
- `4dab583` — authorize watchdog transport path
- `b37ab21` — V2 publisher conformance
- `12e8b08` — guard pins V2 publisher

The two most recent commits are a reviewed, source-only, dormant
`RouteV2RecordPublishing` addition. Their local implementation review states
that they have no production call site and no live Darwin transport body.
That evidence applies only to those committed changes, not to the staged or
unstaged N11D-C work. It also does not make `12e8b08` full-guard-qualified:
the committed guard's global source/test digest was not advanced with the
publisher commits, as Section 3 records.

Protected untracked `docs/handoffs/` content was not opened. The two other
untracked files were read because they are the task's N11D-C plan/review
records and are outside the protected handoff path:

- `docs/reviews/2026-07-31-phase-1c-n11d-c-watchdog-v2-publisher-implementation-review.md`, SHA-256 `c62ded58b1ab712a77a72706fc2a4097119294503c09924e68afb1b6200119cf`
- `docs/superpowers/plans/2026-07-31-n11dc-watchdog-v2-publisher.md`, SHA-256 `7f2cd6881b03ef6ec48c498beb3769c227270b6b45c1ddd78e91f3db7717174c`

## 2. Staged and unstaged topology

### 2.1 Index-only state is not a coherent build

The index adds exactly two files and 2,875 lines:

| Staged path | Additions |
| --- | ---: |
| `Sources/TarsPhase1CCoreAudioRouteAdapter/CoreAudioRouteAdapter.swift` | 1,134 |
| `Tests/TarsPhase1CCoreAudioRouteAdapterTests/CoreAudioRouteAdapterTests.swift` | 1,741 |

`Package.swift` is not staged, so the index does not declare either target.
The staged adapter/test pair is therefore an orphaned intermediate, not an
independently buildable or reviewable slice. The working tree then rewrites
both staged files substantially into a different V2-only design.

### 2.2 Working state is a partial V1-to-V2 cutover

The unstaged patch changes ten files with 5,373 additions and 2,487 deletions:

| Path | Working purpose | Launch disposition |
| --- | --- | --- |
| `Package.swift` | Adds isolated CoreAudio route adapter and tests. | Development-harness only; no launch edge. |
| `RouteRecoveryRecordCodec.swift` | Adds canonical V2 route-recovery documents, progress, setter custody, digests, and transitions. | Reuse only if a future native path truly needs default-route mutation. It is not the streaming protocol. |
| `RouteSafetyContracts.swift` | Adds V2 route permits, record/publisher interfaces, receipts, and facades. | Same conditional reuse; do not import as the gateway/companion wire contract. |
| `RouteSupervisorReducer.swift` | Adds a fixture-step coordinator that persists/publishes an arm before one setter, then persists/publishes terminal and fold states. | The ordering is a useful fault-model reference, but the implementation is fixture-route specific. |
| `CoreAudioRouteAdapter.swift` | Rewrites the staged adapter into a V2-only actor that reads default devices and uses the macOS 15 async CoreAudio setter. | Exclude from the supported launch path. It mutates default routes for the virtual-fixture loop; it does not capture audio. |
| `RouteRecordStoreAdapter.swift` | Adds V2 compare-and-replace persistence and uncertain-result reconciliation. | Potentially useful only for the separate route-recovery harness after a fresh plan. |
| Four matching test files | Exercise V2 record transitions, publication ordering, one-shot setter behavior, and store replay. | Retain as design evidence; they do not prove launch capture or protocol conformance. |

The governing N11D-C plan allowed a larger coherent cutover across codec,
contracts, supervisor and recovery reducers, process/watchdog adapters, route
executors, CoreAudio adapter, tests, and the build guard. The current patch
does not update the recovery reducer, process fence, watchdog, route
executors, or guard. It therefore cannot satisfy that plan's own coherent
cutover rule.

## 3. Guard state

The committed build guard pins:

- `Package.swift` SHA-256
  `bbc5e35ca9f78db99c54e17ce8f7887ac6c22545d80774218d99ae8a442a8bf5`;
- a complete source/test path allowlist that does not contain the CoreAudio
  route-adapter target; and
- Swift source/test manifest SHA-256
  `df0a3f1a72e60c50d5a1408aa5e51a4cc0e695b825829fbb4605100335d9bb2c`.

The working hashes differ, so the guard must stop at its package digest before
the patch can receive source qualification. No guard run was used to alter or
qualify the dirty tree during this read-only audit. The untracked review of
the committed publisher slice records the same first failure and explicitly
excludes the unrelated WIP from its approval.

The clean committed `12e8b08` baseline has a separate guard defect even
without the dirty CoreAudio patch:

- its committed `Package.swift` SHA-256 is
  `bbc5e35ca9f78db99c54e17ce8f7887ac6c22545d80774218d99ae8a442a8bf5`,
  which matches the package pin;
- its committed Swift source/test manifest recomputes to
  `be5548337d6d2295003a48a46c6a59da22d59380b8c80e8ebe380f5efe3ac2e4`;
  and
- the guard still expects
  `df0a3f1a72e60c50d5a1408aa5e51a4cc0e695b825829fbb4605100335d9bb2c`,
  the manifest reproduced at pre-publisher commit `4dab583`.

Therefore `12e8b08` is a clean committed source reference but **guard-red**.
Any future reuse plan must explicitly repair the guard pin, rerun the full
guard and tests from clean scratch, and obtain renewed exact-tree review.
Neither this audit nor the publisher-slice review grants that authority or
qualification.

## 4. Protocol 0002 compatibility

N11D-C does not implement protocol 0002. Its V2 terminology describes a local
route-recovery record and the custody of one default-device setter. It has no
companion/gateway binary framing, `sessionId`, `streamId`, capture generation,
per-source sequence, admission/forwarding/durable watermarks, reconnect
negotiation, transcript coverage, or durable gap projection.

The patch does not directly weaken protocol 0002 because it has no executable
or network edge. It also cannot be cited as evidence that the protocol is
closed. Reusing its record-publish ordering as a general design motif would
require new protocol-specific types and vectors, not renaming the route types.

The only launch-relevant principle worth carrying forward is the strict order
the patch tests: durable authority before an effect; one-shot effect custody;
durable, acknowledged terminal state before progress advances; and an explicit
uncertain result instead of replay after an ambiguous effect.

## 5. Native macOS API decision

The native capture choice is already visible in unaffected, committed source:

- `ScreenCaptureKit` `SCStream` with `.audio` output for application/system
  audio; and
- `AVAudioEngine` input tap for the independent microphone source.

The local Xcode 26 SDK marks `SCStreamConfiguration.capturesAudio`, sample
rate/channel count, and `excludesCurrentProcessAudio` as available from macOS
13.0. The package currently declares `.macOS(.v13)`. The CoreAudio async
default-device setter used by the dirty route adapter is macOS 15-only, but
that setter is not required for native launch capture.

Planning decision for the first pilot:

- keep **macOS 13** only as the core-library source/API compile-and-test floor
  while the existing capture primitives are extracted; and
- set the pilot application's deployment and supported-runtime minimum to
  **macOS 15 or later**, reducing the initial security/device matrix without
  coupling capture to route mutation.

The current ScreenCaptureKit preparer is fixture-specific: it resolves only
`com.tars.phase1c.fixture.signal-emitter`. Product work must replace that fixed
filter with an explicitly selected and visibly reported meeting-application
filter. It must continue registering only `.audio`, excluding the companion's
own audio, and exposing source loss rather than silently broadening to all
desktop audio. The exact application-selection UX and multi-app policy belong
in the G3B implementation plan and device matrix.

## 6. Smallest coherent salvage unit

For the supported launch path, the smallest coherent salvage unit from the
dirty N11D-C patch is **none**. Do not stage the remaining files, do not finish
the route cutover, and do not make the route adapter a dependency of a capture
executable.

Instead:

1. Preserve this worktree exactly until its WIP is separately archived or
   retired under explicit authority.
2. Start later macOS planning from clean committed but guard-red `12e8b08` (or
   an exact reviewed descendant), after G2 closes the companion protocol; the
   separately authorized implementation plan must repair and requalify the
   guard before source reuse.
3. Reuse the unaffected ScreenCaptureKit, AVAudioEngine, bounded-PCM,
   permission, callback-fencing, and terminalization components only through a
   fresh allowed-path plan and exact review.
4. Replace the hard-coded fixture filter and inert main with a new
   protocol-bound composition in a fresh branch. Do not add a product edge to
   the default-route adapter.
5. If the virtual-device fixture harness still has a development need, audit
   and complete its V2 route recovery on a separate non-product branch. Its
   guards and evidence can never satisfy native-launch gates.

This approach retains the difficult offline capture and ownership work while
removing BlackHole/default-route mutation from the commercial architecture.
The packaged pilot and every release artifact must exclude the
`TarsPhase1CCoreAudioRouteAdapter` target and all default-route activation,
even if development begins from clean committed `12e8b08`, where the later
dirty target is not present.

## 7. Claim ceiling

This audit establishes exact dirty-tree topology, guard drift, protocol
separation, and a planning-level macOS API/floor recommendation. It does not
prove that the current native adapters build on the dirty tree, capture real
audio, handle permissions or sleep/wake, work with meeting applications, meet
the pilot OS/device matrix, package/sign/update correctly, or connect to an
authenticated gateway. No physical, hosted, integrated, pilot, or launch claim
follows from this document.
