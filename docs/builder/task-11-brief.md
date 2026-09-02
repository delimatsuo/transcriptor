# Task 11 — Source-only Process Tap live-proof harness and signed-app attestation

## Authority and evidence ceiling

Base this work only on clean commit
`5ea4e703cf6c4d6beb958b0946539d3127ff5066` in the isolated worktree
`/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/build/worktrees/task11-live-process-tap-source`
on branch `codex/task11-live-process-tap-source`.

This task prepares the later live proof; it does not run it. Do not launch an app, use
LaunchServices, enumerate or kill applications, request or inspect TCC, capture audio, play audio,
open a socket outside loopback/AF_UNIX test fixtures, contact a backend or provider, inspect
credentials or Keychain, sign, timestamp, notarize, staple, create a DMG, install, deploy, or use
Git. Do not read or edit `AGENTS.md`, `frontend/AGENTS.md`, `frontend/CLAUDE.md`, or `.env*`.

Offline tests may use temporary AF_UNIX sockets and injected/fake command runners. They must not
use AF_INET/AF_INET6, `open`, `say`, `security`, real `codesign`, `xcrun`, `hdiutil`, `stapler`,
`spctl`, or provider commands. Source/build/test evidence must not be reported as live Process Tap,
TCC, signing, notarization, provider, installation, release, deployment, or production proof.

## Current causal mismatch

`scripts/verify_live_system_audio.py` currently builds and launches the standalone
`.build/release/tars-companion` CLI. That CLI intentionally constructs
`ScreenCaptureKitSystemAudioSource`; Task 10 intentionally did not migrate it. The existing script
can therefore pass from a `Candidato` transcript without ever constructing Process Tap. Its
ScreenCaptureKit readiness prose and binary mtime are not engine or artifact provenance.

The menu-bar app is the Process Tap product path. Task 11 must prepare a future live harness that:

1. consumes only an explicit Developer-ID-signed `TarsCompanion.app`;
2. requests explicit `process-tap` with no CLI/ad-hoc/fallback path;
3. binds the credential to the exact authenticated app process without placing the Task 11 stream
   key in a URL, argv, log, event, evidence file, or retained diagnostic;
4. attests the concrete capture source actually started, not merely the selector value;
5. keeps engine start, permission truth, nonzero functional audio, and transcript success as
   separate conjuncts;
6. requires fresh process/nonce/attempt/generation evidence after restart; and
7. binds later signed evidence to the then-current clean Task 11 implementation HEAD/tree, never
   hard-coding the Task 10 parent as the future artifact.

Normal product deep links remain out of scope for redesign and may still contain the pilot pairing
key. Nevertheless, `AppDelegate` must stop logging the raw URL. The narrower Task 11 invariant is
that the live-harness credential never enters any URL or observable/retained output.

## Exact file authority

The builder may modify only:

- `scripts/verify_live_system_audio.py`
- `scripts/release_menubar_app.sh`
- `companion/native-macos/Sources/TarsNativeCompanion/CaptureSource.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/ProcessTapSystemAudioSource.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/ScreenCaptureKitSystemAudioSource.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/CompanionSessionController.swift`
- `companion/native-macos/Sources/TarsCompanionApp/AppDelegate.swift`
- `companion/native-macos/Sources/TarsCompanionApp/TarsCompanionApp.swift`
- `companion/native-macos/Tests/TarsNativeCompanionTests/CompanionSessionControllerTests.swift`

The builder may create only:

- `scripts/live_system_audio_harness.py`
- `scripts/test_live_system_audio_harness.py`
- `companion/native-macos/Sources/TarsNativeCompanion/LiveHarnessProtocol.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/LiveHarnessControl.swift`
- `companion/native-macos/Tests/TarsNativeCompanionTests/LiveHarnessTests.swift`
- `docs/builder/task-11-report.md`

This designer-owned brief is frozen. No other file may be read for instructions or changed.

## Signed-app-only future packaging mode

Add an explicit source-level mode to `scripts/release_menubar_app.sh` for building the later local
live-proof app. Parse the mode before the existing certificate/notary preflights. The mode must:

- require a caller-supplied clean Task 11 HEAD/tree provenance input derived at execution time;
- embed a canonical provenance resource before final signing;
- build/package the menu-bar app, perform Developer ID signing and strict public-metadata readback
  only when that later action is authorized; and
- stop before DMG creation and every notary/distribution action.

The default distribution path remains unchanged. The signed-app-only branch must be structurally
incapable of reaching notary profile history, submit/log, DMG, stapler, Gatekeeper, app launch, TCC,
audio, or provider work. An offline fake-command test must prove that the branch order stops before
those commands. Do not execute either real signing path in this task.

The future artifact is eligible only when its sealed provenance reports the exact then-current
Task 11 HEAD/tree and `dirty=false`; the executable digest, bundle identifier
`com.ellaexecutivesearch.tarscompanion`, Team ID `3FLG8W6B95`, hardened runtime, entitlements, and
strict signature must be reverified before the credential is sent. Do not use mtime as provenance.

## Typed concrete-source identity and controller ordering

Add a typed system-audio capture-source identity contract in `CaptureSource.swift`. Both concrete
sources return their real engine identity. Update injected controller test sources accordingly.

`CompanionSessionController` must construct the sink and concrete source, compare the selected and
actual source identities, and fail before `ReconnectingAudioSink.start()` or `CaptureSource.start()`
when they differ. A Process Tap selector string paired with a ScreenCaptureKit source is terminal
`engineMismatch`; it must produce no WebSocket/provider side effect and no activation event.

Expose a narrowly typed, removable harness observer/event sink. Events are fenced to:

- a random attempt UUID, not only the numeric session attempt;
- the active source object and observer token;
- the exact capture generation;
- requested, resolved, and actual engine identities; and
- the active session ID without any stream key.

After the concrete source successfully starts, emit an activation for that generation. If the
Process Tap source rebuilds and health advances to a replacement generation, emit a new activation
for that generation before any health event for that generation becomes eligible. Late, removed,
old-source, old-attempt, old-generation, and post-stop events remain rejected. Process restart may
reset numeric counters; freshness is the tuple `(kernel peer audit identity, launch nonce, attempt
UUID, generation)`.

Preserve Task 10 permission semantics exactly: start success is engine/graph evidence only; valid
silence remains `.unknown`; only finite nonzero decoded PCM may make functional permission
`.granted`; explicit `kAudioDevicePermissionsError` is `.denied`; no-buffer/empty behavior is
inconclusive, never silently denial or PASS.

## Strict live-harness protocol and AF_UNIX control core

Put the protocol and control core in the testable `TarsNativeCompanion` library target. App wiring
must remain thin.

Use a versioned, strict, length-prefixed protocol:

- four-byte unsigned big-endian payload length;
- fixed small maximum payload (at most 64 KiB);
- canonical JSON object payloads;
- exact field allowlists per message; reject unknown, missing, duplicate, trailing, malformed,
  oversized, zero-length, or unsupported-version messages;
- bounded read/write deadlines and exactly one session command; and
- no event schema field capable of carrying the stream key.

`SOCK_STREAM` fragmentation/coalescing must be handled correctly. The inbound session command may
contain `session_id`, `stream_key`, and gateway only after peer authentication. Raw framing buffers
must not be logged or persisted. Do not claim that Swift `String` memory is zeroized; instead keep
credential lifetime narrow and prove non-emission/non-persistence.

The app is the AF_UNIX client. The later Python harness creates a mode-0700 run directory and
mode-0600 socket. The harness authenticates kernel-derived peer eUID, PID/audit token, and
executable path before sending the credential. The JSON-claimed PID is only a consistency check.
The app authenticates the server eUID. macOS `LOCAL_PEERPID`/`LOCAL_PEEREPID`/`LOCAL_PEERTOKEN`
access must live behind a small injected Darwin boundary so offline tests can causally reject the
wrong peer without requiring live process enumeration. The threat boundary is same-host/same-user:
filesystem mode and kernel peer identity reject other users and stale/wrong processes but do not
claim protection from a compromise of the same macOS account.

Only the nonsecret socket path and launch nonce may enter argv. Harness mode accepts one peer and
one session. It rejects duplicate commands, competing peers, control loss, and manual/deep-link
start attempts.

## App startup and secret-safe logging

`AppDelegate` must detect the presence of harness mode during `init`, before registering an Apple
Event handler. In harness mode it must never register, parse, buffer, or deliver URL events—even
when the remaining harness arguments are malformed. Normal mode may retain deep-link behavior but
must never `NSLog` the raw URL or key; log only constant text or a parsed session prefix and
gateway-present boolean.

`TarsCompanionApp` parses the complete harness arguments fail-closed, installs the control client,
disables interactive/manual/deep-link session starts, and starts/stops the controller only from the
authenticated one-session command. No credential is rendered in UI or logs.

## Python policy and future live orchestration

Create `scripts/live_system_audio_harness.py` as a pure/importable policy module. It owns:

- signed-artifact/provenance validation policy;
- exact `.app` LaunchServices specification with explicit Process Tap and no CLI fallback;
- AF_UNIX server framing and peer/session state machine;
- strict activation/health/restart validation;
- canonical secret-free JSON evidence and Markdown projection; and
- one pure positive-claim predicate.

Modify `scripts/verify_live_system_audio.py` to use that module later. It must require an explicit
signed app path and never build or fall back to `.build/release/tars-companion`. No Task 11 stream
key may enter argv, a deep link, stdout/stderr, diagnostics, evidence, or retained files. The
candidate phrase must not play until a same-peer activation proves requested/resolved/actual
`process-tap` for the current nonce/attempt/generation. A transcript can satisfy only the transcript
conjunct; it can never override a missing/mismatched engine or health fact.

Restart may kill only the authenticated harness PID, must wait for process death/socket close, then
must create a fresh socket and nonce and require a new peer/attempt activation before playing the
restart phrase. Do not enumerate or kill any process in offline tests.

The positive Process Tap predicate requires all of:

1. sealed exact Task 11 artifact provenance and signature-policy facts;
2. authenticated current peer and launch nonce;
3. current random attempt UUID and generation activation;
4. requested = resolved = actual = `process-tap`;
5. functional permission granted from finite nonzero PCM for that same tuple; and
6. the existing source-labeled transcript assertions.

Table-drive every conjunct independently absent or hostile. Canonical evidence must omit the PASS
claim on any blocked, inconclusive, or failed result.

## Required RED and mutation-effective GREEN tests

Record real RED evidence for the existing failures before implementation:

- live harness targets the standalone CLI and accepts ScreenCaptureKit readiness;
- stream key enters current CLI argv;
- `AppDelegate` logs the raw URL;
- no strict framing/peer/one-session boundary exists;
- selected/actual engine mismatch is not rejected before sink start;
- stale generation/restart facts can satisfy no current strict predicate; and
- the current release flow reaches notary-profile preflight before any signed-app-only branch.

Python GREEN tests must prove:

- launch spec can target only an explicit `.app`, always requests Process Tap, and has no CLI,
  ad-hoc, build, or fallback path;
- artifact provenance, bundle/team/runtime/entitlement/digest checks fail closed;
- fragmentation, coalescing, oversized/zero/trailing/unknown/duplicate messages fail correctly;
- wrong peer/nonce/eUID/PID/audit identity, duplicate peer/session, timeout, and control loss fail;
- a unique credential sentinel is absent from argv/URL/logs/events/evidence/retained files;
- ScreenCaptureKit or transcript-only success cannot produce Process Tap PASS;
- silence/start/no-buffer do not grant permission; explicit denial stays denied;
- restart requires a fresh peer/nonce/attempt tuple even when numeric generation resets; and
- deleting each claim-predicate conjunct fails its dedicated case.

Swift GREEN tests must exercise the real testable control core, not only Python policy:

- fragmented/coalesced frames, exact-field decoding, unsupported versions, bounds, timeouts, and
  duplicate/trailing commands;
- injected kernel peer acceptance/rejection and server-eUID rejection;
- one-session ownership and credential sentinel non-emission;
- harness-mode URL suppression from AppDelegate initialization;
- concrete Process Tap/ScreenCaptureKit identities;
- mismatch rejection before sink/source start and before any activation;
- activation-before-health for every replacement generation;
- late source/token/attempt/generation and post-stop fencing;
- unknown/silent/denied/nonzero-granted truth; and
- restart freshness with reset numeric counters.

Release-script tests use fake command runners and must prove offline qualification invokes zero real
`open`, `say`, `security`, `codesign`, `xcrun`, `hdiutil`, `stapler`, `spctl`, AF_INET, or provider
actions. The signed-app-only branch must be mutation-effective: moving it below notary history or
allowing fallthrough must fail.

## Offline verification

Run only local/offline checks:

- focused Python harness tests and `py_compile`;
- focused Swift control/controller/source tests;
- full Swift debug and optimized tests;
- release `TarsCompanionApp` compilation only;
- existing full backend and frontend unit tests;
- `bash -n` and `shellcheck` for changed scripts;
- plist lint;
- secret-sentinel and forbidden-command source/fixture guards;
- `git diff --check`, exact changed-path review, and a fresh-context review.

Do not run the live harness or release script. Do not claim TSan unless it independently runs green;
Task 10's signal-11 result remains non-green and unrelated to Task 11 qualification.

## Builder report

`docs/builder/task-11-report.md` must list exact files, RED/GREEN commands and counts, mutation
evidence, the secret-sentinel result, the exact source-only proof ceiling, and explicit confirmation
that no live audio/TCC/LaunchServices/app launch, credential/Keychain, signing/timestamp/notary/DMG,
provider/network, deployment, production, or Git action occurred.

## Later owner gates

After source qualification, separate explicit authorization is still required for:

1. Developer ID certificate/private-key use and any timestamp network call for the exact new clean
   Task 11 HEAD/tree;
2. disposable-account LaunchServices/TCC/live synthetic-audio proof, including SIGKILL only of the
   authenticated harness instance;
3. isolated endpoint/ADC/STT/provider execution with cost, retention, and revocation boundaries; and
4. optional DMG/notary/staple/Gatekeeper distribution qualification.
