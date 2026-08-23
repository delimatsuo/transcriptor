# Task 01 — Menu bar app shell: `TarsCompanionApp` target + `CompanionSessionController`

Read `docs/builder/README.md` first (protocol, hard rules). Repo: `"/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"` (quote the path — it has a space). All work happens in `companion/native-macos/` (a SwiftPM package) except where listed. Branch is already checked out; do NOT run git commands.

## Objective

Add a SwiftUI menu-bar app target that can join a T.A.R.S. session (pasted link/code for now; real deep-link handling is Task 02), start/stop system-audio capture using the EXISTING proven library, and show live state — plus the library-level session controller and join-input parser that make it testable.

## Existing library facts you will build on (do not modify these files)

All in `companion/native-macos/Sources/TarsNativeCompanion/`:

- `CaptureSource.swift`: `public protocol CaptureFrameSink: Sendable { func receive(_ frame: AudioFrame) async throws; func receiveGap(_ gap: CoverageGap) async throws }`; `public protocol CaptureSource: AnyObject, Sendable { var source: AudioSource { get }; var configuration: CaptureSourceConfiguration { get }; var status: CaptureSourceStatus { get }; func start() async throws; func stop() async }`; `public struct CaptureSourceConfiguration { public init(identity: SourceIdentity, deviceIdentity: String? = nil) }`.
- `SourceIdentity.swift`: `public struct SourceIdentity` with throwing init `init(sessionID: String, streamID: String, captureGeneration: UInt64, source: AudioSource, sampleRate: Int, channelCount: Int) throws`. `AudioSource` enum has `.microphone` / `.systemAudio` (rawValues "microphone"/"system_audio").
- `ScreenCaptureKitSystemAudioSource.swift`: `public init(configuration: CaptureSourceConfiguration, liveCaptureEnabled: Bool = true, sink: CaptureFrameSink? = nil)`; `start()` throws on failure (permission/display). macOS 13+.
- `ReconnectingAudioSink.swift`: `public final class ReconnectingAudioSink: CaptureFrameSink` — `init(sessionID: String, transportFactory: @escaping @Sendable () -> AudioStreamTransport, bufferCapacityFrames: Int = 600, reconnectDelaysSeconds: [Double] = [1,2,4,8,16,30], connectTimeoutSeconds: Double = 5, sendTimeoutSeconds: Double = 5, sleep: ...)`; `func start()`, `func stop() async`, `func framesSent(for source: AudioSource) -> Int`, `var isConnected: Bool`, `var onStateChange: ((Bool) -> Void)?` (called on connectivity transitions, transition-gated). `public protocol AudioStreamTransport: Sendable { func connect() async throws; func send(_ data: Data) async throws; func sendText(_ text: String) async throws; func cancel() }`.
- `CompanionOptions.swift`: `CompanionOptions.Sources` enum (`.systemAudio` rawValue "system_audio"); `gatewayURL()` builds `<base>/<session>?stream_key=<percent-encoded>`. You will NOT use CompanionOptions in the controller — build the URL the same way via your own helper or reuse if convenient.
- `Sources/TarsCompanionCLI/main.swift` (executable target `tars-companion`): contains `final class URLSessionWebSocketTransport: AudioStreamTransport` (~60 lines near the top, look for the class definition). The ONLY change you may make to this file is REMOVING that class (see File plan) — every other line stays byte-identical.

## File plan

**Create** (all new):
1. `companion/native-macos/Sources/TarsNativeCompanion/URLSessionWebSocketTransport.swift` — MOVE the class verbatim from `main.swift`, add `public` to the class and its init/members as needed so both the CLI and the new app can use it. Behavior must not change.
2. `companion/native-macos/Sources/TarsNativeCompanion/JoinLink.swift`:
```swift
public struct JoinRequest: Equatable, Sendable {
    public let sessionID: String
    public let streamKey: String
    public let gateway: String?   // nil → caller's default
}
public enum JoinLink {
    /// Accepts either a full deep link `tars-companion://join?session=X&key=Y[&gateway=Z]`
    /// or the compact form `X:Y`. Trims whitespace. Returns nil for anything else
    /// (missing/empty session or key, wrong scheme/host, malformed URL).
    public static func parse(_ input: String) -> JoinRequest?
}
```
Gateway value in the URL form is percent-decoded by URLComponents automatically; pass it through as-is.
3. `companion/native-macos/Sources/TarsNativeCompanion/CompanionSessionController.swift`:
```swift
public enum CompanionState: Equatable, Sendable {
    case idle, connecting, capturing, reconnecting
    case error(String)
}

@MainActor
public final class CompanionSessionController: ObservableObject {
    @Published public private(set) var state: CompanionState = .idle
    @Published public private(set) var activeSessionID: String? = nil
    public var systemAudioFramesSent: Int { /* sink?.framesSent(for: .systemAudio) ?? 0 */ }

    public typealias TransportFactory = @Sendable (URL) -> AudioStreamTransport
    public typealias SourceFactory = (CaptureSourceConfiguration, CaptureFrameSink) -> any CaptureSource

    /// Defaults: URLSessionWebSocketTransport + ScreenCaptureKitSystemAudioSource.
    /// Both injectable so unit tests never touch real sockets or ScreenCaptureKit.
    public init(transportFactory: TransportFactory? = nil, sourceFactory: SourceFactory? = nil)

    public func start(sessionID: String, streamKey: String, gatewayBase: String) async
    public func stop() async
}
```
Behavior contract:
- `start` while not idle/error → ignore (no-op).
- `start`: state `.connecting`; permission preflight `CGPreflightScreenCaptureAccess()` (import CoreGraphics) — on false, call `CGRequestScreenCaptureAccess()`, re-check, and if still false set `.error("Permissão ausente. Habilite em Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema para o TarsCompanion, e tente novamente.")` and return. **Skip the preflight entirely when a custom `sourceFactory` was injected** (unit tests).
- Build the WS URL: `gatewayBase + "/" + sessionID + "?stream_key=" + <RFC3986-unreserved percent-encoded key>` (same encoding rule as `CompanionOptions.gatewayURL()` — alphanumerics + `-._~`).
- Create `ReconnectingAudioSink(sessionID:transportFactory:)` with a factory closing over that URL; set `sink.onStateChange = { connected in ... }` marshaled to the MainActor: connected==true → state `.capturing`; connected==false while running → `.reconnecting`. Call `sink.start()`.
- Create the system-audio source via `sourceFactory` (default: `ScreenCaptureKitSystemAudioSource(configuration:liveCaptureEnabled:true, sink:)`) with `SourceIdentity(sessionID: sessionID, streamID: "system", captureGeneration: 1, source: .systemAudio, sampleRate: 16_000, channelCount: 1)` and `deviceIdentity: "ScreenCaptureKit.SystemAudio"`; `try await source.start()` — on throw: `await sink.stop()`, state `.error("Falha ao iniciar a captura de áudio do sistema: \(error.localizedDescription)")`, return. On success set `activeSessionID` and state `.capturing` (if not already set by onStateChange).
- **CRITICAL (documented production bug class in this repo): the controller MUST retain the source and sink as stored properties for the whole session.** Never leave them as locals — Swift ARC may release a local after its last use and silently tear down the capture stream. Add a comment saying exactly this at the properties.
- `stop`: `await source.stop()`, `await sink.stop()`, nil both properties, `activeSessionID = nil`, state `.idle`. Safe to call from any state.
4. `companion/native-macos/Sources/TarsCompanionApp/TarsCompanionApp.swift` — the app:
```swift
@main struct TarsCompanionApp: App { ... MenuBarExtra ... }
```
- `MenuBarExtra` with `.menuBarExtraStyle(.window)`; label = SF Symbol by state: idle `waveform.circle`, connecting `ellipsis.circle`, capturing `waveform.circle.fill`, reconnecting `arrow.triangle.2.circlepath.circle`, error `exclamationmark.triangle.fill`.
- Window content (pt-BR): status line ("Ocioso" / "Conectando…" / "Capturando — sessão <id-prefix8>" / "Reconectando…" / the error text); when capturing, a small frames counter ("<N> quadros enviados", refresh via a 1 s `Timer`/task while the window is open); a TextField "Cole o link ou código da sessão" + button "Conectar" → `JoinLink.parse`; on nil show inline "Link inválido" text, on success call `controller.start(...)` using parsed gateway or the setting; button "Parar" (visible when not idle); divider; "Abrir T.A.R.S." (opens `cockpitURL` setting via `NSWorkspace.shared.open`); "Ajustes" DisclosureGroup with two TextFields persisted in `UserDefaults`: gateway base (key `tars_gateway_base`, default `ws://127.0.0.1:8000/api/stream/native`) and cockpit URL (key `tars_cockpit_url`, default `http://localhost:3000`); divider; "Sair" → `NSApplication.shared.terminate(nil)`.
- Keep ALL logic in the controller/parsers; the view layer is dumb.
5. `companion/native-macos/Tests/TarsNativeCompanionTests/JoinLinkTests.swift` — XCTest, minimum cases: full URL with gateway; full URL without gateway → gateway nil; compact `abc123:key456`; percent-encoded key in URL decodes; rejects: empty string, `foo://join?...` wrong scheme, missing key, missing session, `justonepart`.
6. `companion/native-macos/Tests/TarsNativeCompanionTests/CompanionSessionControllerTests.swift` — XCTest with an injected fake transport (conform to `AudioStreamTransport`; connect/send succeed) and fake source (records start/stop calls, pushes nothing). Cases: (a) start → state becomes `.capturing`, activeSessionID set, fake source started; (b) transport that fails every connect → state `.reconnecting` (not error); (c) source whose `start()` throws → state `.error` containing "Falha ao iniciar", sink stopped; (d) stop from capturing → `.idle`, source stop called; (e) start ignored while capturing (second start doesn't re-create source — fake factory call count stays 1). Use `await` + short expectation polling on `@MainActor` state; no wall-clock sleeps beyond tens of ms.

**Modify** (exactly two files, minimally):
7. `companion/native-macos/Package.swift` — add executable target `TarsCompanionApp` (path `Sources/TarsCompanionApp`, depends on `TarsNativeCompanion`); add product if products are listed for executables. Touch nothing else in the manifest.
8. `companion/native-macos/Sources/TarsCompanionCLI/main.swift` — DELETE the `URLSessionWebSocketTransport` class (now in the library). No other edit; the file must still compile because the library exports it publicly.

## Verification (all must pass; run from the paths shown)

```
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift test    # 55 existing + your new tests, 0 failures
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift build   # all three targets build
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests -q   # 290 passed, untouched
```

TDD order: write JoinLinkTests + controller tests first, run `swift test`, record the compile/assert failures (that is your RED), then implement, then GREEN.

## Out of scope for this task (do NOT do)

Deep-link event handling / URL-scheme registration / Info.plist / .app bundling (Task 02); any cockpit/frontend change (Task 03); taps engine; signing; modifying any existing library source file other than creating the new ones listed; modifying the CLI beyond the single class deletion.

## Report

`docs/builder/task-01-report.md` per the protocol: files changed, RED/GREEN outputs, suite results, honest notes on anything uncertain (e.g., MenuBarExtra behavior you could not verify without launching the app — say so; the designer will smoke-launch it).
