# Task 10 — Core Audio Process Tap engine, safe realtime bridge, selection, probe, watchdog

## Authority and evidence ceiling

Implement this task only in the isolated worktree
`/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/build/worktrees/task10-process-tap`
on branch `codex/task10-process-tap`, whose exact starting commit is
`e818083ac72650dc39638d112eba45a8d8ba8460`.

This is a source-only implementation and offline-verification task. Do not run live audio,
request or inspect TCC permissions, enumerate user applications, use a device, contact Apple,
sign/notarize/staple, access a provider, use network services, deploy, or use Git. Do not read or
touch `AGENTS.md`, `frontend/AGENTS.md`, `frontend/CLAUDE.md`, or any `.env*` file.

Passing this task may establish compilation, deterministic fake-HAL lifecycle behavior, bounded
realtime bridging, PCM conversion, engine policy, and offline causal tests. It must not be described
as proof of real Process Tap capture, TCC attribution or persistence, realtime performance, actual
HAL cleanup, signing, notarization, installability, or pilot/release readiness.

## Design decisions

Apple exposes `AudioHardwareCreateProcessTap` on macOS 14.2+, while this product deliberately
selects Process Tap automatically only on macOS 14.4+. The Swift package remains macOS 13. The
existing ScreenCaptureKit engine remains the 13.0–14.3 fallback.

The installed SDK marks the Core Audio IOProc as realtime. The IOProc must not allocate `Data`,
lock, create a `Task`, touch `AsyncStream`, call `AVAudioConverter`, read wall clock, log, construct
an `AudioFrame`, or call a sink. It may only validate descriptors, snapshot raw audio timestamps,
copy bounded bytes into preallocated storage, atomically publish or count an overflow, and return.

There is no public Process Tap permission preflight. `CGPreflightScreenCaptureAccess` and
`CGRequestScreenCaptureAccess` are ScreenCaptureKit-only. Start success and silent PCM do not prove
permission. Only `kAudioDevicePermissionsError` proves denial; only structurally valid nonzero PCM
provides functional positive evidence. Silent nonempty PCM proves callback/buffer liveness but
leaves permission unknown.

The approved “zero-buffer watchdog” is therefore defined as a watchdog for no callbacks or
zero-length/structurally empty callback buffers. All-zero sample amplitude is ordinary silence: it
must not deny, grant, rebuild, or repeat an alert.

The standalone `tars-companion` CLI stays on ScreenCaptureKit because it is not the signed app
bundle and does not share its Info.plist/TCC identity. Manual engine override belongs to the signed
`TarsCompanionApp` launch arguments and dependency-injected tests.

## Exact file authority

Create only:

- `companion/native-macos/Sources/TarsRealtimeAudioBridge/include/TarsRealtimeAudioBridge.h`
- `companion/native-macos/Sources/TarsRealtimeAudioBridge/TarsRealtimeAudioBridge.c`
- `companion/native-macos/Sources/TarsNativeCompanion/SystemAudioEngineSelector.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/CanonicalSystemAudioConverter.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/ProcessTapHAL.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/SystemAudioCaptureMonitor.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/ProcessTapSystemAudioSource.swift`
- `companion/native-macos/Tests/TarsNativeCompanionTests/ProcessTapRealtimeBridgeTests.swift`
- `companion/native-macos/Tests/TarsNativeCompanionTests/SystemAudioEngineSelectorTests.swift`
- `companion/native-macos/Tests/TarsNativeCompanionTests/ProcessTapPCMConversionTests.swift`
- `companion/native-macos/Tests/TarsNativeCompanionTests/SystemAudioCaptureMonitorTests.swift`
- `companion/native-macos/Tests/TarsNativeCompanionTests/ProcessTapSystemAudioSourceTests.swift`
- `docs/builder/task-10-report.md`

Modify only:

- `companion/native-macos/Package.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/CaptureSource.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/CompanionSessionController.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/ScreenCaptureKitSystemAudioSource.swift`
- `companion/native-macos/Sources/TarsCompanionApp/TarsCompanionApp.swift`
- `companion/native-macos/Tests/TarsNativeCompanionTests/NativeCaptureSourceTests.swift`
- `companion/native-macos/Tests/TarsNativeCompanionTests/CompanionSessionControllerTests.swift`

Do not modify the CLI, `CompanionOptions.swift`, the Info.plist, Task 09 signing scripts or
entitlements, backend, frontend, wire framing, or any other path. The existing app Info.plist already
contains a nonempty `NSAudioCaptureUsageDescription`; guard it with an offline test rather than
rewriting it.

## Engine and scope contract

Add public sendable/equatable types equivalent to:

```swift
public enum SystemAudioEnginePreference: String, Equatable, Sendable {
    case automatic
    case processTap = "process-tap"
    case screenCaptureKit = "screen-capture-kit"
}

public enum ResolvedSystemAudioEngine: String, Equatable, Sendable {
    case processTap = "process-tap"
    case screenCaptureKit = "screen-capture-kit"
}
```

The pure selector accepts an injected `OperatingSystemVersion`:

- automatic: ScreenCaptureKit on 13.x through 14.3; Process Tap on 14.4+;
- explicit Process Tap: fail loudly below the public 14.2 API boundary;
- explicit ScreenCaptureKit: select ScreenCaptureKit on every supported OS;
- no runtime fallback after any Process Tap side effect or failure;
- no fallback on denial, ambiguous probe, watchdog, route failure, or cleanup failure.

`TarsCompanionApp` accepts the manual launch argument
`--system-audio-engine auto|process-tap|screen-capture-kit`, defaulting to `auto`. Invalid or missing
values produce an actionable pt-BR app error without starting a source. Do not add this override to
the standalone CLI.

Task 10 implements one explicit immutable capture scope: global system mix excluding the companion
process. Model it as a typed scope rather than an empty-array convention. Resolve the companion PID
through `kAudioHardwarePropertyTranslatePIDToProcessObject`; `kAudioObjectUnknown` is a loud failure.
Build a mono global-exclusive `CATapDescription` containing exactly that process object ID. Set
unmuted and private explicitly. Never use the SDK-26-only `bundleIDs` or
`processRestoreEnabled` properties.

## Realtime bridge

Add a small SwiftPM C target named `TarsRealtimeAudioBridge` and make
`TarsNativeCompanion` depend on it. Its fixed-capacity single-producer/single-consumer ring is fully
allocated before `AudioDeviceStart` and uses C atomics with release/acquire publication.

Each slot stores a bounded owned copy of all input-buffer bytes plus the minimum immutable
descriptor/timestamp metadata needed off-thread: buffer count, per-buffer byte/channel layout,
ASBD identity, sample-time/host-time values and validity flags, and lifecycle generation. Never
retain an `AudioBufferList` pointer after IOProc return. Reject/count a descriptor that cannot fit;
never truncate and mislabel it. Ring full increments a bounded overflow episode counter and never
grows memory.

The concrete `AudioDeviceIOProc` and every helper reachable from its push path live in the C target.
Declare each of those functions `CA_REALTIME_API`; compile the target with
`-Werror=function-effects`. The reachable path uses direct calls only—no function pointers or other
indirection—and its named callee allowlist is limited to descriptor checks, timestamp/metadata
copies, bounded `memcpy`, and C atomics. Keep a source-contract test for the exact callback symbol,
annotation, and callee shape, but compiler enforcement is the hard gate. A negative XCTest compile
fixture written into that test's temporary directory must hide allocation behind a helper reachable
from a `CA_REALTIME_API` function and prove the compiler rejects it with the same warning-as-error.

Compiler effects do not prove the direct-callee restriction. Add a Clang JSON-AST reachability gate
over the production C translation unit: starting at the literal IOProc symbol, every reachable
`CallExpr` must resolve to a direct declaration in the literal allowlist (`memcpy` and the C11 atomic
builtins/macros used by this file), with no function-pointer/indirect call and no other project helper.
Keep descriptor classification inline in the callback so no hidden production helper is necessary.
Two additional temporary negative fixtures must remain compiler-valid `CA_REALTIME_API` code but fail
the AST gate: one direct call to an annotated extra helper with a volatile-global side effect, and one
indirect function-pointer call. The test must prove both mutations fail.

The C callback first increments callback arrival, then performs a total, mutually exclusive parse
against the prevalidated ASBD before changing any other liveness counter:

- interleaved input has exactly one buffer with the expected channel count and frame-byte alignment;
- planar input has exactly the expected number of one-channel buffers, equal byte lengths, and frame
  alignment;
- if every expected buffer has zero bytes, it is `empty` whether its pointer is null or nonnull;
- any positive byte length with a null pointer, missing/extra/partially empty plane, inconsistent
  lengths/channels/alignment, arithmetic overflow, or unexpected layout is `malformed` and loud;
- a structurally valid layout whose total bytes exceed one preallocated slot is `capacityRejected`
  and loud, not valid-nonempty;
- only after the entire descriptor is valid, bounded, and contains positive bytes does the callback
  increment `validNonemptyArrival` and attempt enqueue;
- ring full occurs only after that valid increment and is counted separately as `ringOverflow`.

Stale generation is also separate. Malformed/capacity-rejected inputs never cancel the no-buffer
watchdog. A valid callback remains observable even if the ring is full, so backpressure can never
masquerade as no capture.

The C API must support deterministic creation/destruction, nonblocking push, pop into caller-owned
preallocated output, counter snapshots, capacity/overflow inspection, and optimization-resistant
zeroization with `explicit_bzero` or `memset_s`. Consumed/reusable slots and every destruction path
are zeroized, with a test hook that inspects the slot immediately before deallocation.

A single non-realtime serialized Swift drain consumes and converts ring slots into a separate
fixed-capacity canonical-item delivery queue. It never awaits the sink while owning or referencing a
raw ring slot. A single delivery worker awaits the sink in order; canonical-queue pressure is bounded
and produces a causal overflow gap rather than memory growth. Do not use the existing unbounded
`OrderedFrameRelay` anywhere in the Process Tap path.

## HAL lifecycle

`ProcessTapSystemAudioSource` is a public `CaptureSource` guarded by
`@available(macOS 14.2, *)`. Keep resource handles and concrete HAL operations internal and
injectable under `@testable`.

Acquire in this order, recording each successful edge immediately and independently:

1. translate the current PID to a non-unknown Core Audio process object;
2. create a private, unmuted, mono global tap excluding that object;
3. read and validate the actual tap ASBD from `kAudioTapPropertyFormat`;
4. create the fixed realtime ring;
5. create a private aggregate with a fresh name/UID and attach exactly the tap;
6. create the IOProc;
7. create the non-realtime drain and bounded delivery workers;
8. register the sleep/wake observer and the service-reset, tap-list, device-alive, and tap-format
   (`kAudioTapPropertyFormat`) listeners, recording every token separately;
9. arm/snapshot the watchdog counters for this generation;
10. start the aggregate device.

Do not set `kAudioAggregateDeviceTapAutoStartKey`: idle system audio must not block startup or the
watchdog. Do not change the default input/output route and do not create a persistent or public
virtual device.

Teardown attempts every owned edge in this order even if an earlier cleanup reports an error:

1. fence the lifecycle generation so stale callbacks/timers cannot publish;
2. cancel the watchdog/scheduler registration;
3. unregister every sleep/wake and Core Audio listener while its object ID remains valid;
4. stop the aggregate device;
5. destroy the IOProc;
6. stop and join the ring drain so no callback/pop can reference the ring;
7. detach the tap if required by the concrete composition API;
8. destroy the aggregate;
9. destroy the process tap;
10. optimization-resistant zeroize and destroy the now-unreferenced ring;
11. stop the bounded delivery worker under an injected deadline.

On a normal user stop, already-converted canonical items may drain only within that finite deadline.
On failure or rebuild, discard and zeroize queued old-generation items after ordering one causal gap.
A sink that blocks past the deadline cannot be forcibly terminated by Swift cancellation. Fence its
generation, stop all new sink invocations, and move the in-flight delivery task plus its one immutable
canonical item into a self-contained quarantine capsule. The capsule owns no HAL ID, ring pointer,
source/controller callback, queue, or new-generation reference; it may only wait for that already
invoked sink call to return, then self-release. Record cleanup failure and block source restart while
any capsule remains. No post-stop sink invocation is permitted; the one already-invoked call may
complete later and is reported as in-flight tail. The raw ring is safe to join/zeroize/free before
that completion because the delivery worker never owns it. Test a sink that ignores cancellation,
later resumes, observes no second invocation, cannot publish source health, and blocks restart until
its capsule exits.

Teardown is idempotent. Aggregate destruction is asynchronous, so every start/rebuild uses a fresh
UID and never assumes immediate UID reuse. A persistent cleanup failure blocks automatic restart
and is retained as sanitized diagnostics. Start, stop, watchdog recovery, sleep/wake and Core Audio
service-reset recovery share one serialized lifecycle owner. A stop during start unwinds and ends
stopped, never running. A stale callback from generation N cannot affect generation N+1.

Listen for injected production equivalents of sleep/wake, device-alive/tap-list changes,
`kAudioTapPropertyFormat`, and `kAudioHardwarePropertyServiceRestarted`. A tap-format change fences
the old generation before another buffer is decoded and performs one serialized complete rebuild or
fails loudly. A service reset re-registers every listener on the new graph. Each event either
rebuilds the complete graph exactly once with a coverage gap/new generation, or fails loudly. Do not
keep stale IDs.

## PCM and frame contract

Read the actual tap ASBD. Accept and causally test planar/interleaved linear PCM at 44.1 and 48 kHz,
mono/stereo, including Float32. Unsupported structures fail loudly; never reinterpret bytes.

Off realtime, use `AVAudioConverter` (or equivalently causal Apple conversion) to produce:

- signed little-endian PCM16;
- 16,000 Hz;
- mono;
- exactly 800 samples / 1,600 bytes per 50 ms `AudioFrame`.

Downmix must use real channel data. Preserve fractional resampler accounting across callback
boundaries so long 44.1 kHz fixtures do not drift. Derive discontinuities from captured input sample
and host times, not callback count or wall clock. Establish one monotonic/wall-clock anchor outside
IOProc; do not call `Date()` there.

The first Process Tap frame sequence is 0, then increments once per emitted frame. `firstSample` is
the canonical output-sample position. Fix the existing ScreenCaptureKit system-source first-frame
sequence to the same zero-based contract and add a mutation-effective test; do not modify microphone
behavior in this task.

Ring overflow, timestamp gap/overlap/regression, format change, rebuild, sleep/wake or HAL reset must
emit a causal gap or advance the capture generation. Every new capture generation starts at sequence
0 and canonical `firstSample` 0; the old-generation unknown-end gap is delivered first, before any
new-generation frame. Do not invent an exact duration for a period with no callbacks. Keep raw audio
memory-bounded and in memory only; do not log or persist it.

## Probe and watchdog truth contract

Process Tap startup must not call CoreGraphics permission APIs. Initial permission is unknown.

- `kAudioDevicePermissionsError`: fail with denied and this exact copy:
  `O macOS negou a captura de áudio do sistema. Autorize o TarsCompanion em Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema e tente novamente.`
- first valid nonempty PCM containing at least one finite decoded actual-channel sample with nonzero
  amplitude: functionally proven/granted; signed zero, padding, NaN, and infinity are not signal;
- valid nonempty silent PCM: callback/buffer liveness only, permission remains unknown, capture may
  continue without rebuild or denial;
- no callback or only structurally empty/all-null buffers for two injected monotonic seconds: one ambiguous
  terminal/recovery result with this exact copy:
  `Nenhum áudio verificável foi recebido. Isso não confirma uma permissão negada: a causa também pode ser silêncio, ausência de áudio sendo reproduzido, rota indisponível ou uma falha do sistema de áudio. Verifique se há áudio sendo reproduzido e se o TarsCompanion está autorizado em Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema.`

Malformed descriptors, slot-limit violations, unsupported ASBDs, inconsistent byte/channel layouts,
NaN/Inf-only content, and format changes are loud non-permission capture failures. They are never fed
to the no-buffer watchdog and never described as denial.

Do not report granted from setup/start, denied from silence, or revoked without prior functional
proof plus an explicit permission error.

The source-level watchdog observes the C callback-arrival and structurally-valid-nonempty-arrival
counters, not ring pops, conversion, frames, delivery, or gateway `framesSent`. Snapshot/arm those
counters before `AudioDeviceStart` so an immediate callback is not lost. After start, no callback or
only empty/all-null callbacks for two seconds may claim exactly one bounded rebuild for that user
start. A valid nonempty buffer, silent or not—even if the ring is full or the drain is stalled—cancels
the no-buffer deadline. Ring-full and rejected-descriptor episodes remain distinct. The rebuild fences
the old generation, emits one ordered route-loss/unknown-end gap, tears down the complete graph,
rebuilds once, and starts a fresh probe. If the rebuilt graph also produces no valid buffer, fail
loudly and require user action. The one-rebuild budget belongs to the user start and is not reset by
advancing the lifecycle generation.

At most one automatic rebuild is allowed per user start. Stop cancels the deadline; buffer/deadline
races and stale timers must resolve at most once. There is no ScreenCaptureKit fallback.

## Controller and app integration

Add a narrow source-health observation contract in `CaptureSource.swift`, invoked only off realtime.
Use a sendable update value containing lifecycle generation plus `CaptureSourceStatus`, and explicit
observer install/remove methods returning an opaque token. Installation occurs before source start
and immediately reports the current state; the first started Process Tap health is `.unknown`.
Every later update is generated on the serialized non-realtime lifecycle owner. The controller moves
it to `@MainActor`, accepts it only when both observer token/source identity and active generation
still match, and updates an `@Published` health value. Stop removes the observer before releasing the
source; rebuild replaces the accepted generation before new health can publish. Late old-generation,
late-after-stop, removed-observer, and quarantined-delivery updates are ignored.

The app controller defaults to automatic selection and exposes the resolved engine and truthful
source health for visible pt-BR status. Tests must prove initial unknown, unknown → granted on
functional signal, silent staying unknown, explicit permission error becoming denied, observer
removal exactly once, and late-update rejection across stop/rebuild. While Process Tap permission is
unknown, do not label it as granted. A custom source/HAL/selector in tests bypasses real CoreGraphics,
Core Audio, TCC and device work.

ScreenCaptureKit selection preserves its existing preflight/request behavior. Process Tap selection
never calls those functions. The controller retains the source and sink for the entire session.

## Required RED/GREEN tests

Write failing tests first for every new behavior and record the RED commands/output in the report.
For invariants already true at baseline (the CLI staying on ScreenCaptureKit and the existing plist
key), record baseline characterization plus mutation/negative-fixture RED evidence instead of
claiming an impossible initial RED. Then implement. The tests must instantiate the real
`ProcessTapSystemAudioSource` with a fake production HAL boundary; a fake whole `CaptureSource` is
insufficient for source lifecycle proof.

Required causal cases:

- OS policy matrix: 13.x, 14.1, 14.2, 14.3, 14.4 and 26; explicit overrides; no fallback after a
  Process Tap side effect;
- app launch-argument default/valid/missing/invalid parsing; CLI remains ScreenCaptureKit;
- exact global-exclusive/self-exclusion, private/unmuted/mono tap description; unknown process ID
  rejected; static UID mutation fails fresh-UID test;
- failure after every acquisition/listener/worker edge cleans every prior edge exactly once; start
  failure after IOProc destroys it; every listener is removed exactly once; cleanup continues after
  one cleanup failure; duplicate/concurrent stop; stop during every start phase; blocked-sink stop;
  no callback/pop after ring release;
- ring capacity is fixed, overflow is bounded/episode-gated, retained slots stay ordered, callback
  pointers are not retained, and slot/ring bytes are zeroized after pop/stop; the descriptor-category
  matrix covers zero-buffer, all-null, zero-byte nonnull, partially-null/partially-empty planar,
  positive-size null, wrong buffer/channel count, unequal plane lengths, misalignment, arithmetic
  overflow, slot-limit rejection, valid enqueue, and valid-but-ring-full;
- compiler-enforced realtime annotation/callee gate plus the hidden-allocating-helper negative compile
  fixture; the source-contract test also fails on indirect calls or a callback outside the C target;
- planar/interleaved 44.1/48 kHz mono/stereo fixtures; left-only/right-only downmix; exact 16 kHz
  PCM16 1,600-byte frames; long 44.1 kHz fixture proves no cumulative drift; no-op resampler/downmix
  mutations fail;
- first sequence 0 and contiguous output samples for Process Tap and ScreenCaptureKit; timestamp
  gap, overlap and regression; ring overflow gap;
- explicit permission error differs from unsupported/bad-object/malformed/unsupported-format failures;
  start success alone is unknown; finite decoded nonzero PCM grants functional evidence; signed
  zero/padding/NaN/Inf do not; silent nonempty PCM neither denies/grants/rebuilds; no callback or
  empty/all-null buffers use ambiguous copy and never denial copy;
- buffer/deadline race emits once; stop-before-deadline emits nothing; disconnected sink has no
  effect on capture watchdog; valid callbacks during ring-full/drain-stall do not rebuild; stale
  callback/deadline from generation N cannot affect N+1;
- a non-cooperative sink produces a quarantined in-flight tail, no second/post-stop invocation, no
  status publication, safe ring destruction, and a blocked restart until the capsule exits;
- first no-buffer episode produces one gap + one rebuild; persistent no-buffer after rebuild fails;
  the rebuild budget does not reset with generation; format change cannot decode under old ASBD;
  sleep/wake and HAL reset rebuild once, re-register listeners, and order old-generation gap before
  a zero-based new generation;
- app Info.plist guard proves nonempty `NSAudioCaptureUsageDescription` without editing the plist.

Tests use injected fake HAL, clock, scheduler, OS version, UUID factory and event monitor. No test or
production branch may use `XCTest`, an environment-only bypass, or `liveCaptureEnabled=false` to
skip the real lifecycle under test.

## Builder verification and report

The builder may run only local source checks in this worktree:

```bash
swift test --package-path companion/native-macos --filter ProcessTapRealtimeBridgeTests
swift test --package-path companion/native-macos --filter ProcessTapSystemAudioSourceTests
swift test --package-path companion/native-macos --filter ProcessTapPCMConversionTests
swift test --package-path companion/native-macos --filter SystemAudioEngineSelectorTests
swift test --package-path companion/native-macos --filter SystemAudioCaptureMonitorTests
swift test --package-path companion/native-macos --filter CompanionSessionControllerTests
swift test --package-path companion/native-macos
swift build --package-path companion/native-macos -c release --product TarsCompanionApp
plutil -lint companion/native-macos/Resources/TarsCompanionApp-Info.plist
plutil -lint companion/native-macos/Resources/TarsCompanionApp.entitlements
bash -n scripts/release_menubar_app.sh
```

Do not run Thread Sanitizer in the builder pass; the verifier will decide whether the host/runtime
supports a reliable TSAN run. Do not run the release/notary script.

Write `docs/builder/task-10-report.md` with exact files changed, RED/GREEN commands and counts,
implementation mapping, known evidence ceiling, and explicit confirmation that no live capture,
TCC, signing, provider, device, network, deployment, production, Git, or release action occurred.
