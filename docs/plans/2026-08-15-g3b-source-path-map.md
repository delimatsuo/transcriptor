# G3B macOS Companion Source-Only Path Map

Status: documentation-only authorization package. No path below is being
created or changed by this checkpoint. A later source authorization may create
only the listed implementation, test, guard, sandbox, and evidence paths.

## Exact anchor

- merged base commit: `47fc798885be4d09d983d16ddc14c26a1c90d366`;
- merged base tree: `160584b1fd9ddcb436a9c158f8658a6c6415fda3`;
- G3A merge commit: `bfc4d9b78f80635a7562f76f5c182890d672fa73`;
- G3A tree: `0fa6e032c7444a438d2be334d5efa1f111c04a35`;
- API/OS decision: `docs/plans/2026-08-15-g3b-native-api-and-os-floor-decision.md`;
- future worktree: `/private/tmp/transcriptor-native-g3b-source`;
- future branch: `codex/native-g3b-source`.

The repository is single-engineer. This map grants no source, device,
permission, provider, network, cloud, credential, deployment, merge, release,
or real-data authority. A later owner authorization must name this package's
exact commit/tree before any source write.

## Exact allowed path set

### Runtime and native adapters

- `companion/native-macos/Package.swift`;
- `companion/native-macos/Sources/TarsNativeCompanion/CompanionContracts.swift`;
- `companion/native-macos/Sources/TarsNativeCompanion/SourceIdentity.swift`;
- `companion/native-macos/Sources/TarsNativeCompanion/FrameReducer.swift`;
- `companion/native-macos/Sources/TarsNativeCompanion/CustodyRing.swift`;
- `companion/native-macos/Sources/TarsNativeCompanion/LifecycleCoordinator.swift`;
- `companion/native-macos/Sources/TarsNativeCompanion/DeletionCoordinator.swift`;
- `companion/native-macos/Sources/TarsNativeCompanion/Diagnostics.swift`;
- `companion/native-macos/Sources/TarsNativeCompanion/CaptureSource.swift`;
- `companion/native-macos/Sources/TarsNativeCompanion/AVAudioEngineMicrophoneSource.swift`;
- `companion/native-macos/Sources/TarsNativeCompanion/ScreenCaptureKitSystemAudioSource.swift`;
- `companion/native-macos/Sources/TarsNativeCompanion/GeneratedFixtureSource.swift`;
- `companion/native-macos/Sources/TarsNativeCompanion/OfflineCompanionSimulator.swift`.

The native adapter files may declare the selected OS APIs but must not be
invoked by offline tests. `GeneratedFixtureSource` and injected protocols are
the only execution path in the source/offline guard.

### Deterministic tests

- `companion/native-macos/Tests/TarsNativeCompanionTests/CompanionContractsTests.swift`;
- `companion/native-macos/Tests/TarsNativeCompanionTests/SourceIdentityTests.swift`;
- `companion/native-macos/Tests/TarsNativeCompanionTests/FrameReducerTests.swift`;
- `companion/native-macos/Tests/TarsNativeCompanionTests/CustodyRingTests.swift`;
- `companion/native-macos/Tests/TarsNativeCompanionTests/LifecycleTests.swift`;
- `companion/native-macos/Tests/TarsNativeCompanionTests/DeletionTests.swift`;
- `companion/native-macos/Tests/TarsNativeCompanionTests/RecoveryAndRaceTests.swift`;
- `companion/native-macos/Tests/TarsNativeCompanionTests/GeneratedFixtureTests.swift`.

### Guards, sandbox, and evidence

- `companion/native-macos/Scripts/run_g3b_offline_guard.sh`;
- `companion/native-macos/Scripts/run_g3b_artifact_scan.sh`;
- `companion/native-macos/Sandbox/g3b-offline.sb`;
- `docs/reviews/2026-08-15-g3b-source-offline-evidence.md`;
- `docs/reviews/2026-08-15-g3b-source-owner-attestation.md`.

No Xcode project, entitlements file, signing configuration, app bundle,
permission automation, update channel, or deployment artifact is allowed in
this source-only corridor.

## Excluded paths and capabilities

The later implementation may not modify existing protocol bindings, legacy
backend/capture/STT/storage/configuration paths, frontend, extension, PR #10,
PR #11, PR #13, N11D-C, protected checkouts, or unrelated worktrees. It may not
load credentials, open sockets, call HTTP/cloud/provider APIs, persist raw
audio, select a virtual/default-route device, request permissions, start a
native capture stream, or use participant/candidate data.

## Responsibility map

| Concern | Allowed owner | Explicit non-authority |
| --- | --- | --- |
| Source identity and framing | `SourceIdentity.swift`, `FrameReducer.swift` | gateway coverage or provider truth |
| Raw custody and local release | `CustodyRing.swift` | physical-erasure claim or durable gateway gap |
| Native capture boundary | adapter files and injected `CaptureSource` | virtual/default route, live device execution |
| Lifecycle/deletion | coordinator files | provider deletion or top-level completion |
| Offline evidence | simulator, tests, guards, evidence docs | physical, hosted, pilot, or launch claim |

Any need for an excluded path, dependency, API, device action, or protocol
semantic change invalidates this map and requires a new docs review.
