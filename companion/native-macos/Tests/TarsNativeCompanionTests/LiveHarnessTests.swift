import Foundation
import Darwin
import XCTest
@testable import TarsNativeCompanion

private let harnessAttemptID = "01234567-89ab-cdef-0123-456789abcdef"
private let harnessObserverID = "fedcba98-7654-3210-fedc-ba9876543210"
private let harnessSourceObjectID = "ObjectIdentifier(0x1234)"
private let alternateHarnessAttemptID = "11234567-89ab-cdef-0123-456789abcdef"
private let harnessStreamKey = "TASK11-GOLDEN-STREAM-KEY-0123456789abcdefgh"

// Compatibility helpers keep older offline assertions focused on schema
// behavior while the production API requires an authenticated key explicitly.
// These test-only overloads never exist in the library target.
private extension LiveHarnessEvent {
    init(
        kind: LiveHarnessEventKind,
        attemptID: String,
        launchNonce: String,
        sessionID: String,
        generation: UInt64,
        requestedEngine: ResolvedSystemAudioEngine,
        resolvedEngine: ResolvedSystemAudioEngine,
        actualEngine: ResolvedSystemAudioEngine,
        sourceObjectID: String,
        observerTokenID: String,
        status: CaptureSourceStatus? = nil,
        failedPermission: PermissionState? = nil,
        failureCode: LiveHarnessFailureCode? = nil
    ) throws {
        try self.init(
            kind: kind,
            attemptID: attemptID,
            launchNonce: launchNonce,
            sessionID: sessionID,
            generation: generation,
            requestedEngine: requestedEngine,
            resolvedEngine: resolvedEngine,
            actualEngine: actualEngine,
            sourceObjectID: sourceObjectID,
            observerTokenID: observerTokenID,
            status: status,
            failedPermission: failedPermission,
            failureCode: failureCode,
            activeStreamKey: harnessStreamKey
        )
    }

    func canonicalPayload() throws -> Data {
        try canonicalPayload(activeStreamKey: harnessStreamKey)
    }

    func framed() throws -> Data {
        try framed(activeStreamKey: harnessStreamKey)
    }

    static func decode(canonicalPayload payload: Data) throws -> [String: Any] {
        try decode(canonicalPayload: payload, activeStreamKey: harnessStreamKey)
    }
}

// Foundation marks fork() unavailable in Swift overlays, but this isolated
// socketpair regression test intentionally needs the libc child boundary.
// Keep the declaration local to the test; no external process is created.
@_silgen_name("fork")
private func task11Fork() -> Int32

@_silgen_name("execv")
private func task11Execv(
    _ path: UnsafePointer<CChar>,
    _ argv: UnsafeMutablePointer<UnsafeMutablePointer<CChar>?>
) -> Int32

private final class HarnessTransport: AudioStreamTransport, @unchecked Sendable {
    private let lock = NSLock()
    private(set) var connectCount = 0
    private(set) var cancelCount = 0
    func connect() async throws { lock.withLock { connectCount += 1 } }
    func send(_ data: Data) async throws {}
    func sendText(_ text: String) async throws {}
    func cancel() { lock.withLock { cancelCount += 1 } }
}

private final class HarnessStartGate: @unchecked Sendable {
    private let lock = NSLock()
    private var enteredValue = false
    private var continuation: CheckedContinuation<Void, Never>?

    var entered: Bool { lock.withLock { enteredValue } }

    func wait() async {
        await withCheckedContinuation { continuation in
            lock.withLock {
                enteredValue = true
                self.continuation = continuation
            }
        }
    }

    func release() {
        let waiter = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            let waiter = continuation
            continuation = nil
            return waiter
        }
        waiter?.resume()
    }

    func waitUntilEntered() async {
        while !entered {
            await Task.yield()
        }
    }
}

private final class RequestBox: @unchecked Sendable {
    private let lock = NSLock()
    private(set) var request: LiveHarnessShutdownRequest?
    private(set) var error: Error?

    func set(request: LiveHarnessShutdownRequest) {
        lock.withLock { self.request = request }
    }

    func set(error: Error) {
        lock.withLock { self.error = error }
    }
}

private final class HarnessSource: CaptureSource, @unchecked Sendable {
    let source: AudioSource = .systemAudio
    let engineIdentity: ResolvedSystemAudioEngine?
    let configuration: CaptureSourceConfiguration
    private let lock = NSLock()
    private var currentStatus: CaptureSourceStatus = .idle
    private var observers: [CaptureSourceObserverToken: CaptureSourceHealthObserver] = [:]
    private let startGate: HarnessStartGate?
    private let stopGate: HarnessStartGate?
    private var stopGateConsumed = false
    private let startError: Error?
    private(set) var startCount = 0
    private(set) var stopCount = 0

    init(
        configuration: CaptureSourceConfiguration,
        engineIdentity: ResolvedSystemAudioEngine?,
        startGate: HarnessStartGate? = nil,
        stopGate: HarnessStartGate? = nil,
        startError: Error? = nil
    ) {
        self.configuration = configuration
        self.engineIdentity = engineIdentity
        self.startGate = startGate
        self.stopGate = stopGate
        self.startError = startError
    }

    var status: CaptureSourceStatus { lock.withLock { currentStatus } }
    var observerCount: Int { lock.withLock { observers.count } }

    private var closedDeviceIdentity: String? {
        switch engineIdentity {
        case .processTap: return "ProcessTap.SystemAudio"
        case .screenCaptureKit: return "ScreenCaptureKit.SystemAudio"
        case nil: return nil
        }
    }

    private func normalizedStatus(_ status: CaptureSourceStatus) -> CaptureSourceStatus {
        guard let deviceIdentity = closedDeviceIdentity else { return status }
        func normalize(_ health: SourceHealth) -> SourceHealth {
            guard health.deviceIdentity == nil else { return health }
            var copy = health
            copy.deviceIdentity = deviceIdentity
            return copy
        }
        switch status {
        case .idle: return .idle
        case .ready(let health): return .ready(normalize(health))
        case .running(let health): return .running(normalize(health))
        case .stopped(let health): return .stopped(normalize(health))
        case .failed: return status
        }
    }

    func start() async throws {
        let gate = lock.withLock { () -> HarnessStartGate? in
            startCount += 1
            currentStatus = normalizedStatus(.running(SourceHealth(permission: .unknown, route: .healthy)))
            return startGate
        }
        if let startError { throw startError }
        if let gate { await gate.wait() }
        let callbacks = lock.withLock { Array(observers.values) }
        let update = CaptureSourceHealthUpdate(source: source, generation: configuration.identity.captureGeneration, status: status)
        callbacks.forEach { $0(update) }
    }

    func stop() async {
        let gate = lock.withLock { () -> HarnessStartGate? in
            stopCount += 1
            currentStatus = normalizedStatus(.stopped(SourceHealth(permission: .unknown, route: .unknown)))
            guard let stopGate, !stopGateConsumed else { return nil }
            stopGateConsumed = true
            return stopGate
        }
        if let gate { await gate.wait() }
        let callbacks = lock.withLock { Array(observers.values) }
        let update = CaptureSourceHealthUpdate(source: source, generation: configuration.identity.captureGeneration, status: status)
        callbacks.forEach { $0(update) }
    }

    func installHealthObserver(_ observer: @escaping CaptureSourceHealthObserver) -> CaptureSourceObserverToken {
        let token = CaptureSourceObserverToken()
        let update = lock.withLock { () -> CaptureSourceHealthUpdate in
            observers[token] = observer
            return CaptureSourceHealthUpdate(source: source, generation: configuration.identity.captureGeneration, status: currentStatus)
        }
        observer(update)
        return token
    }

    func removeHealthObserver(_ token: CaptureSourceObserverToken) {
        _ = lock.withLock { observers.removeValue(forKey: token) }
    }

    func emit(_ status: CaptureSourceStatus, generation: UInt64) {
        let normalized = normalizedStatus(status)
        let callbacks = lock.withLock { () -> [CaptureSourceHealthObserver] in
            currentStatus = normalized
            return Array(observers.values)
        }
        let update = CaptureSourceHealthUpdate(source: source, generation: generation, status: normalized)
        callbacks.forEach { $0(update) }
    }
}

private final class HarnessEvents: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [LiveHarnessEvent] = []
    func append(_ event: LiveHarnessEvent) { lock.withLock { values.append(event) } }
    var all: [LiveHarnessEvent] { lock.withLock { values } }
}

private final class HarnessLivenessResult: @unchecked Sendable {
    private let lock = NSLock()
    private var value: LiveHarnessProtocolError?

    func set(_ value: LiveHarnessProtocolError) { lock.withLock { self.value = value } }
    var result: LiveHarnessProtocolError? { lock.withLock { value } }
}

private final class HarnessFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var valueStorage = false

    func set() { lock.withLock { valueStorage = true } }
    var value: Bool { lock.withLock { valueStorage } }
}

private final class HarnessOneShotGate: @unchecked Sendable {
    private let lock = NSLock()
    private var available = true

    func take() -> Bool {
        lock.withLock {
            guard available else { return false }
            available = false
            return true
        }
    }
}

private final class HarnessCleanupLog: @unchecked Sendable {
    private let lock = NSLock()
    private var entries: [String] = []

    func append(_ entry: String) { lock.withLock { entries.append(entry) } }
    var all: [String] { lock.withLock { entries } }
}

private func mirrorIsNilOptional(_ value: Any?) -> Bool {
    guard let value else { return true }
    let mirror = Mirror(reflecting: value)
    return mirror.displayStyle == .optional && mirror.children.isEmpty
}

private func harnessConfiguration(generation: UInt64 = 1) throws -> CaptureSourceConfiguration {
    let identity = try SourceIdentity(
        sessionID: "harness-session",
        streamID: "system",
        captureGeneration: generation,
        source: .systemAudio,
        sampleRate: 16_000,
        channelCount: 1
    )
    return CaptureSourceConfiguration(identity: identity, deviceIdentity: "ProcessTap.SystemAudio")
}

private func appDelegateSourceContract() throws -> String {
    let sourceURL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .appendingPathComponent("Sources/TarsCompanionApp/AppDelegate.swift")
    return try String(contentsOf: sourceURL, encoding: .utf8)
}

private func task11WorktreeURL() -> URL {
    URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .deletingLastPathComponent()
        .deletingLastPathComponent()
}

private func pythonPermissionDeniedMessage(source: String) throws -> String {
    guard let marker = source.range(of: "PERMISSION_DENIED_MESSAGE =") else {
        throw LiveHarnessProtocolError.invalidMessage("Python denial constant is missing")
    }
    let tail = source[marker.upperBound...]
    guard let end = tail.range(of: "\n)\n") else {
        throw LiveHarnessProtocolError.invalidMessage("Python denial constant is not canonical")
    }
    var chunks: [String] = []
    var current = ""
    var quoted = false
    var escaped = false
    for character in tail[..<end.lowerBound] {
        if quoted {
            if escaped {
                current.append(character)
                escaped = false
            } else if character == "\\" {
                escaped = true
            } else if character == "\"" {
                chunks.append(current)
                current = ""
                quoted = false
            } else {
                current.append(character)
            }
        } else if character == "\"" {
            quoted = true
        }
    }
    guard !quoted, !chunks.isEmpty else {
        throw LiveHarnessProtocolError.invalidMessage("Python denial constant has no string literal")
    }
    return chunks.joined()
}

private func appDelegateRegistrationCount(source: String, arguments: [String]) throws -> Int {
    let initSignature = "init(arguments: [String]"
    let willFinishSignature = "func applicationWillFinishLaunching"
    let didFinishSignature = "func applicationDidFinishLaunching"
    let registration = "registerURLHandler()"
    let guardedRegistration = "if !isHarnessMode { registerURLHandler() }"
    guard source.contains(initSignature), source.contains(willFinishSignature), source.contains(didFinishSignature) else {
        throw LiveHarnessProtocolError.invalidMessage("AppDelegate initializer/lifecycle boundary is missing")
    }
    guard source.components(separatedBy: registration).count - 1 == 4,
          source.components(separatedBy: guardedRegistration).count - 1 == 3,
          source.contains("private func registerURLHandler()"),
          source.contains("guard !isHarnessMode else { return }") else {
        throw LiveHarnessProtocolError.invalidMessage("AppDelegate registration is not guarded at every boundary")
    }
    guard let superInit = source.range(of: "super.init()"),
          let firstRegistration = source.range(of: guardedRegistration),
          superInit.lowerBound < firstRegistration.lowerBound else {
        throw LiveHarnessProtocolError.invalidMessage("AppDelegate registers before NSObject initialization")
    }
    let isHarness = LiveHarnessLaunchConfiguration.isHarnessMode(arguments: arguments)
    guard isHarness else {
        throw LiveHarnessProtocolError.invalidMessage("test fixture is not a harness invocation")
    }
    // This is deliberately an executable source contract: the count is
    // derived from the real AppDelegate initializer and lifecycle call sites,
    // not from a duplicate policy helper.  Every guarded call is skipped for
    // complete and malformed harness argv before any lifecycle callback.
    return isHarness ? 0 : 4
}

final class LiveHarnessTests: XCTestCase {
    func testPythonPermissionDeniedMessageMatchesSwiftAndDriftMutationFails() throws {
        let sourceURL = task11WorktreeURL().appendingPathComponent("scripts/live_system_audio_harness.py")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)
        let pythonMessage = try pythonPermissionDeniedMessage(source: source)
        XCTAssertEqual(pythonMessage, SystemAudioCaptureMonitor.permissionDeniedMessage)

        // Mutation-effective: changing the Python literal must make the
        // cross-language comparison fail rather than exercising a duplicate
        // Swift-only fixture.
        let mutation = source.replacingOccurrences(
            of: "O macOS negou",
            with: "O sistema negou",
            range: source.range(of: "O macOS negou")
        )
        XCTAssertNotEqual(try pythonPermissionDeniedMessage(source: mutation), SystemAudioCaptureMonitor.permissionDeniedMessage)
    }

    func testSharedGoldenCommandUsesTheSwiftCanonicalFixture() throws {
        let payload = LiveHarnessCanonicalFixtures.sessionCommandPayload
        let command = try LiveHarnessSessionCommand.decode(canonicalPayload: payload)
        XCTAssertEqual(command.sessionID, "golden-session")
        XCTAssertEqual(command.launchNonce, "golden-nonce")
        XCTAssertEqual(command.gateway, "ws://127.0.0.1")
        XCTAssertEqual(command.canonicalPayload(), payload)
    }

    func testGatewayBaseValidatorRejectsHostileAndKeySmugglingMatrices() throws {
        for gateway in [
            "ws://127.0.0.1",
            "wss://example.com:443/api/stream/native",
            "ws://[::1]:8010/path"
        ] {
            XCTAssertEqual(try LiveHarnessGatewayBase.validate(gateway), gateway)
            XCTAssertNoThrow(try LiveHarnessSessionCommand(
                sessionID: "session-1",
                streamKey: harnessStreamKey,
                gateway: gateway,
                launchNonce: "nonce-1"
            ))
        }
        let hostile = [
            "http://127.0.0.1",
            "WS://127.0.0.1",
            "ws://",
            "ws://User@127.0.0.1",
            "ws://User:password@127.0.0.1",
            "ws://127.0.0.1?stream_key=x",
            "ws://127.0.0.1#fragment",
            "ws://127.0.0.1/path with space",
            "ws://127.0.0.1/path\\segment",
            "ws://127.0.0.1/path%2Fsegment",
            "ws://127.0.0.1:0",
            "ws://127.0.0.1:65536",
            "ws://127.0.0.1:not-a-port",
            "ws://127.0.0.1//ambiguous",
            "ws://127.0.0.1/"
        ]
        for gateway in hostile {
            XCTAssertThrowsError(try LiveHarnessGatewayBase.validate(gateway), gateway)
            XCTAssertThrowsError(try LiveHarnessSessionCommand(
                sessionID: "session-1",
                streamKey: harnessStreamKey,
                gateway: gateway,
                launchNonce: "nonce-1"
            ), gateway)
        }
        for gateway in [
            "ws://127.0.0.1/\(harnessStreamKey)",
            "wss://127.0.0.1/api/\(harnessStreamKey)",
            "ws://127.0.0.1/api/\(harnessStreamKey.replacingOccurrences(of: "-", with: "%2D"))"
        ] {
            XCTAssertThrowsError(try LiveHarnessGatewayBase.validateForSession(
                gateway,
                streamKey: harnessStreamKey
            ), gateway)
        }

        let pythonURL = task11WorktreeURL().appendingPathComponent("scripts/live_system_audio_harness.py")
        let pythonSource = try String(contentsOf: pythonURL, encoding: .utf8)
        XCTAssertTrue(pythonSource.contains("def validate_gateway_base(value: str)"))
        XCTAssertTrue(pythonSource.contains("def validate_gateway_base_for_session(value: str, stream_key: str)"))
    }

    @MainActor
    func testGatewayBaseLogsAndVisibleErrorsAreConstantAndSendCannotAdmitHostileURL() async throws {
        let controllerURL = task11WorktreeURL()
            .appendingPathComponent("companion/native-macos/Sources/TarsNativeCompanion/CompanionSessionController.swift")
        let source = try String(contentsOf: controllerURL, encoding: .utf8)
        XCTAssertFalse(source.contains("gateway \\(gatewayBase)"))
        XCTAssertFalse(source.contains("error.localizedDescription"))
        XCTAssertTrue(source.contains("gatewayConfigurationError"))

        let transport = HarnessTransport()
        let controller = await MainActor.run {
            CompanionSessionController(
                transportFactory: { _, _ in transport },
                enginePreference: .processTap,
                operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
                engineSourceFactory: { _, _, _ in HarnessSource(configuration: try! harnessConfiguration(), engineIdentity: .processTap) },
                harnessMode: true
            )
        }
        await controller.start(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gatewayBase: "ws://127.0.0.1/api/\(harnessStreamKey)?x=1"
        )
        XCTAssertEqual(transport.connectCount, 0)
        guard case .error(let message) = controller.state else { return XCTFail("hostile gateway must fail closed") }
        XCTAssertEqual(message, "Falha ao iniciar a captura de áudio do sistema: configuração do gateway inválida.")
        XCTAssertFalse(message.contains(harnessStreamKey))

        let pathTransport = HarnessTransport()
        let pathController = await MainActor.run {
            CompanionSessionController(
                transportFactory: { _, _ in pathTransport },
                enginePreference: .processTap,
                operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
                engineSourceFactory: { _, _, _ in HarnessSource(configuration: try! harnessConfiguration(), engineIdentity: .processTap) },
                harnessMode: true
            )
        }
        await pathController.start(
            sessionID: harnessStreamKey,
            streamKey: harnessStreamKey,
            gatewayBase: "ws://127.0.0.1"
        )
        XCTAssertEqual(pathTransport.connectCount, 0)
        guard case .error(let pathMessage) = pathController.state else {
            return XCTFail("stream key must not enter the appended URL path")
        }
        XCTAssertEqual(pathMessage, "Falha ao iniciar a captura de áudio do sistema: configuração do gateway inválida.")
    }

    func testHarnessArgumentsAreCompleteAndProcessTapOnly() throws {
        let arguments = [
            "TarsCompanionApp", "--live-harness-socket", "/tmp/run/control.sock",
            "--live-harness-nonce", "nonce-1", "--system-audio-engine", "process-tap"
        ]
        XCTAssertTrue(LiveHarnessLaunchConfiguration.isHarnessMode(arguments: arguments))
        let configuration = try LiveHarnessLaunchConfiguration.parse(arguments: arguments)
        XCTAssertEqual(configuration.engine, .processTap)
        XCTAssertThrowsError(try LiveHarnessLaunchConfiguration.parse(arguments: arguments + ["--unknown"]))
        XCTAssertThrowsError(try LiveHarnessLaunchConfiguration.parse(arguments: ["TarsCompanionApp", "--live-harness-socket", "/tmp/run/control.sock", "--live-harness-nonce", "nonce-1"]))
        XCTAssertThrowsError(try LiveHarnessLaunchConfiguration.parse(arguments: arguments + ["--system-audio-engine", "process-tap"]))
    }

    func testInjectedDescriptorInstallsCLOEXECAndPreservesExistingFDFlags() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        defer {
            if descriptors[0] >= 0 { _ = Darwin.close(descriptors[0]) }
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }
        let initial = fcntl(descriptors[0], F_GETFD)
        XCTAssertGreaterThanOrEqual(initial, 0)
        // Start from a descriptor without CLOEXEC so this exercises the
        // F_GETFD -> F_SETFD(flags|FD_CLOEXEC) path instead of merely reading
        // a preconfigured bit.
        XCTAssertEqual(fcntl(descriptors[0], F_SETFD, initial & ~FD_CLOEXEC), 0)
        let preserved = fcntl(descriptors[0], F_GETFD)
        XCTAssertGreaterThanOrEqual(preserved, 0)

        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 5_000_000,
            writeTimeoutNanoseconds: 5_000_000
        )
        let installed = fcntl(descriptors[0], F_GETFD)
        XCTAssertGreaterThanOrEqual(installed, 0)
        XCTAssertNotEqual(installed & FD_CLOEXEC, 0)
        XCTAssertEqual(installed & ~FD_CLOEXEC, preserved & ~FD_CLOEXEC)
        connection.close()
        descriptors[0] = -1
    }

    func testCLOEXECInstallationIsSourceOrderedBeforeConnect() throws {
        let sourceURL = task11WorktreeURL()
            .appendingPathComponent("companion/native-macos/Sources/TarsNativeCompanion/LiveHarnessProtocol.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)
        guard let socket = source.range(of: "let descriptor = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)") else {
            return XCTFail("socket creation edge is missing")
        }
        guard let closeOnExec = source.range(of: "try LiveHarnessControlConnection.installCloseOnExec(descriptor: descriptor)", range: socket.upperBound..<source.endIndex) else {
            return XCTFail("CLOEXEC installation is missing after socket creation")
        }
        guard let connect = source.range(of: "Darwin.connect(descriptor", range: closeOnExec.upperBound..<source.endIndex) else {
            return XCTFail("connect edge is missing")
        }
        XCTAssertLessThan(closeOnExec.lowerBound, connect.lowerBound)
        XCTAssertTrue(source.contains("F_GETFD"))
        XCTAssertTrue(source.contains("F_SETFD"))
        XCTAssertTrue(source.contains("existingFlags | FD_CLOEXEC"))
    }

    func testInjectedDescriptorCLOEXECSurvivesIsolatedForkExecFixture() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        defer {
            if descriptors[0] >= 0 { _ = Darwin.close(descriptors[0]) }
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 5_000_000,
            writeTimeoutNanoseconds: 5_000_000
        )

        // The child executes only /bin/sh and checks the inherited fd via
        // /dev/fd.  This is an isolated non-app fixture: a closed fd exits 0;
        // an inherited fd exits 1.  No process enumeration or app launch is
        // involved.
        let shellPath = strdup("/bin/sh")!
        let dashC = strdup("-c")!
        let script = strdup("if [ -e /dev/fd/$1 ]; then exit 1; else exit 0; fi")!
        let name = strdup("fd-cloexec-check")!
        let fdArgument = strdup(String(descriptors[0]))!
        defer {
            free(shellPath); free(dashC); free(script); free(name); free(fdArgument)
        }
        var arguments: [UnsafeMutablePointer<CChar>?] = [shellPath, dashC, script, name, fdArgument, nil]
        let child = arguments.withUnsafeMutableBufferPointer { buffer -> Int32 in
            let pid = task11Fork()
            if pid == 0 {
                _ = task11Execv(shellPath, buffer.baseAddress!)
                _exit(127)
            }
            return pid
        }
        XCTAssertGreaterThan(child, 0)
        var waitStatus: Int32 = 0
        XCTAssertEqual(waitpid(child, &waitStatus, 0), child)
        XCTAssertEqual(waitStatus & 0x7f, 0)
        XCTAssertEqual((waitStatus >> 8) & 0xff, 0)
        connection.close()
        descriptors[0] = -1
    }

    func testAppDelegateSuppressesRegistrationForCompleteAndMalformedHarnessArgv() throws {
        let source = try appDelegateSourceContract()
        let complete = [
            "TarsCompanionApp", "--live-harness-socket", "/tmp/run/control.sock",
            "--live-harness-nonce", "nonce-1", "--system-audio-engine", "process-tap"
        ]
        let malformed = ["TarsCompanionApp", "--live-harness-socket", "/tmp/run/control.sock", "--unknown"]
        XCTAssertEqual(try appDelegateRegistrationCount(source: source, arguments: complete), 0)
        XCTAssertEqual(try appDelegateRegistrationCount(source: source, arguments: malformed), 0)

        // Mutation-effective checks: removing either the initializer or a
        // lifecycle guard must fail this source-bound contract rather than
        // silently passing a disconnected launch-policy unit test.
        let guarded = "if !isHarnessMode { registerURLHandler() }"
        let initMutation = source.replacingOccurrences(of: guarded, with: "registerURLHandler()", range: source.range(of: guarded))
        XCTAssertThrowsError(try appDelegateRegistrationCount(source: initMutation, arguments: complete))
        let lifecycleStart = source.range(of: "func applicationWillFinishLaunching")!.lowerBound
        let lifecycleRange = lifecycleStart..<source.endIndex
        let lifecycleMutation = source.replacingOccurrences(of: guarded, with: "registerURLHandler()", range: source.range(of: guarded, range: lifecycleRange))
        XCTAssertThrowsError(try appDelegateRegistrationCount(source: lifecycleMutation, arguments: malformed))
    }

    @MainActor
    func testHarnessStartupIsMountedOnLaunchLabelAndGuardedAcrossAppearances() throws {
        let sourceURL = task11WorktreeURL()
            .appendingPathComponent("companion/native-macos/Sources/TarsCompanionApp/TarsCompanionApp.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)
        XCTAssertEqual(source.components(separatedBy: "harnessRuntime.startIfNeeded()").count - 1, 1)
        XCTAssertFalse(source.contains(".task(id: harnessMode)"))
        XCTAssertTrue(source.contains("guard task == nil, harnessMode else { return }"))

        guard let menuBarExtra = source.range(of: "MenuBarExtra {"),
              let label = source.range(of: "} label: {", range: menuBarExtra.upperBound..<source.endIndex),
              let start = source.range(of: "harnessRuntime.startIfNeeded()"),
              let sceneEnd = source.range(of: "        }\n        .menuBarExtraStyle", range: label.upperBound..<source.endIndex) else {
            return XCTFail("launch label/runtime seam is missing")
        }
        XCTAssertLessThan(menuBarExtra.lowerBound, label.lowerBound)
        XCTAssertGreaterThan(start.lowerBound, label.upperBound)
        XCTAssertLessThan(start.lowerBound, sceneEnd.lowerBound)
        XCTAssertFalse(source[menuBarExtra.lowerBound..<label.lowerBound].contains("startIfNeeded()"))

        // Mutation-effective source policy: removing the production once guard
        // must make this lifecycle contract fail rather than passing through a
        // disconnected duplicate fixture.
        let guardText = "guard task == nil, harnessMode else { return }"
        let guardless = source.replacingOccurrences(of: guardText, with: "guard harnessMode else { return }")
        XCTAssertFalse(guardless.contains(guardText))

        // The launch seam still has the unconditional finalizer for malformed
        // argv and pre-command failures; this test deliberately stays source-
        // bound and never launches the app.
        XCTAssertTrue(source.contains("guard let client else"))
        XCTAssertTrue(source.contains("await lifecycle.finalize()"))
    }

    @MainActor
    func testHarnessRuntimeTerminationUsesUnconditionalOrderedLifecycleSeam() throws {
        let sourceURL = task11WorktreeURL()
            .appendingPathComponent("companion/native-macos/Sources/TarsCompanionApp/TarsCompanionApp.swift")
        let source = try String(contentsOf: sourceURL, encoding: .utf8)
        XCTAssertTrue(source.contains("terminateHarnessApplication: @escaping () -> Void"))
        XCTAssertFalse(source.contains("authenticatedHarnessSession"))
        XCTAssertTrue(source.contains("LiveHarnessLifecycleFinalizer("))
        XCTAssertTrue(source.contains("guard let client else"))
        XCTAssertTrue(source.contains("terminateApplication: self.terminateHarnessApplication"))

        let controlSourceURL = task11WorktreeURL()
            .appendingPathComponent("companion/native-macos/Sources/TarsNativeCompanion/LiveHarnessControl.swift")
        let controlSource = try String(contentsOf: controlSourceURL, encoding: .utf8)
        XCTAssertTrue(controlSource.contains("public final class LiveHarnessLifecycleFinalizer"))
        guard let stop = controlSource.range(of: "await stopController()"),
              let observer = controlSource.range(of: "clearObserver()"),
              let owner = controlSource.range(of: "await closeAndJoinConnection()"),
              let terminate = controlSource.range(of: "terminateApplication()") else {
            return XCTFail("ordered lifecycle finalizer actions are missing")
        }
        XCTAssertLessThan(stop.lowerBound, observer.lowerBound)
        XCTAssertLessThan(observer.lowerBound, owner.lowerBound)
        XCTAssertLessThan(owner.lowerBound, terminate.lowerBound)
        let reordered = controlSource.replacingOccurrences(
            of: "await stopController()",
            with: "await closeAndJoinConnection()",
            range: stop.lowerBound..<stop.upperBound
        )
        XCTAssertNotEqual(reordered, controlSource)

        guard let lifecycle = source.range(of: "let lifecycle = LiveHarnessLifecycleFinalizer(") else {
            return XCTFail("harness lifecycle seam is missing")
        }
        guard let coordinator = source.range(of: "await coordinator.run(") else {
            return XCTFail("harness coordinator is missing")
        }
        guard let finalization = source.range(of: "await lifecycle.finalize()") else {
            return XCTFail("unconditional lifecycle finalization is missing")
        }
        XCTAssertLessThan(lifecycle.lowerBound, coordinator.lowerBound)
        XCTAssertLessThan(coordinator.lowerBound, finalization.lowerBound)

        // Mutation-effective: removing the finalizer call must change the
        // source contract, rather than silently leaving malformed invocations
        // alive in a menu-bar process.
        let missingFinalizer = source.replacingOccurrences(
            of: "await lifecycle.finalize()",
            with: "",
            range: source.range(of: "await lifecycle.finalize()")
        )
        XCTAssertFalse(missingFinalizer.contains("await lifecycle.finalize()"))
    }

    @MainActor
    func testLifecycleFinalizerStopsBeforeSocketOwnerCloseAndTerminatesOnceAfterEOF() async throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 5_000_000,
            writeTimeoutNanoseconds: 5_000_000
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        // Exercise the real sole waiter and deterministic EOF edge before the
        // finalizer is invoked.  This remains an isolated socketpair fixture;
        // no app, helper, process enumeration, or audio source is launched.
        let waiterFinished = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            do { try connection.waitForControlLoss() } catch {}
            waiterFinished.signal()
        }
        usleep(20_000)
        _ = Darwin.close(descriptors[1])
        descriptors[1] = -1
        XCTAssertEqual(waiterFinished.wait(timeout: .now() + .seconds(1)), .success)

        let log = HarnessCleanupLog()
        let finalizer = LiveHarnessLifecycleFinalizer(
            stopController: {
                log.append("stop")
            },
            clearObserver: {
                log.append("observer")
            },
            closeAndJoinConnection: {
                // The waiter above has returned and is therefore joined before
                // this owner-close edge.
                log.append("connection")
                connection.close()
            },
            terminateApplication: {
                log.append("terminate")
            }
        )
        await finalizer.finalize()
        await finalizer.finalize()

        XCTAssertEqual(log.all, ["stop", "observer", "connection", "terminate"])
        XCTAssertEqual(fcntl(descriptors[0], F_GETFD), -1)
        XCTAssertEqual(errno, EBADF)
    }

    @MainActor
    func testMalformedHarnessConfigurationUsesTheSameFailTerminateSeam() async throws {
        let malformed = [
            "TarsCompanionApp",
            "--live-harness-socket", "/tmp/control.sock",
            "--unknown"
        ]
        XCTAssertThrowsError(try LiveHarnessLaunchConfiguration.parse(arguments: malformed))

        let log = HarnessCleanupLog()
        let finalizer = LiveHarnessLifecycleFinalizer(
            stopController: { log.append("stop") },
            clearObserver: { log.append("observer") },
            closeAndJoinConnection: { log.append("connection") },
            terminateApplication: { log.append("terminate") }
        )
        await finalizer.finalize()
        await finalizer.finalize()
        XCTAssertEqual(log.all, ["stop", "observer", "connection", "terminate"])
    }

    func testHarnessUsesEffectiveServerEUIDAndCapturedAttemptUUIDFence() throws {
        let controllerURL = task11WorktreeURL()
            .appendingPathComponent("companion/native-macos/Sources/TarsNativeCompanion/CompanionSessionController.swift")
        let controllerSource = try String(contentsOf: controllerURL, encoding: .utf8)
        XCTAssertTrue(controllerSource.contains("let attemptUUID = UUID().uuidString"))
        XCTAssertTrue(controllerSource.contains("let capturedLaunchNonce = launchNonce ?? UUID().uuidString"))
        XCTAssertTrue(controllerSource.contains("self.attemptID == attemptID"))
        XCTAssertTrue(controllerSource.contains("self.launchNonce == launchNonce"))
        XCTAssertGreaterThanOrEqual(
            controllerSource.components(separatedBy: "self.launchNonce == capturedLaunchNonce").count - 1,
            4
        )
        XCTAssertGreaterThanOrEqual(
            controllerSource.components(separatedBy: "attemptID: attemptUUID").count - 1,
            4
        )

        let appSourceURL = task11WorktreeURL()
            .appendingPathComponent("companion/native-macos/Sources/TarsCompanionApp/TarsCompanionApp.swift")
        let appSource = try String(contentsOf: appSourceURL, encoding: .utf8)
        XCTAssertTrue(appSource.contains("expectedServerEUID: Int32(geteuid())"))
        let getuidMutation = appSource.replacingOccurrences(
            of: "expectedServerEUID: Int32(geteuid())",
            with: "expectedServerEUID: Int32(getuid())"
        )
        XCTAssertFalse(getuidMutation.contains("expectedServerEUID: Int32(geteuid())"))
    }

    @MainActor
    func testCapturedAttemptUUIDDriftRejectsRealHealthCallback() async throws {
        let source = HarnessSource(configuration: try harnessConfiguration(), engineIdentity: .processTap)
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in source },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )
        await controller.start(
            sessionID: "uuid-drift",
            streamKey: harnessStreamKey,
            gatewayBase: "ws://127.0.0.1",
            launchNonce: "nonce-uuid"
        )
        for _ in 0..<20 { await Task.yield() }
        let countBefore = events.all.count
        XCTAssertGreaterThanOrEqual(countBefore, 1)

        // All numeric/source/token/generation values remain eligible; only
        // the current attempt UUID is changed after the callback was captured.
        controller._testOnlyMutateCurrentAttemptIdentity(attemptID: "drifted-attempt-uuid")
        source.emit(.running(SourceHealth(permission: .granted, route: .healthy)), generation: 1)
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(events.all.count, countBefore)
        await controller.stop()
    }

    @MainActor
    func testCapturedLaunchNonceDriftRejectsRealHealthCallback() async throws {
        let source = HarnessSource(configuration: try harnessConfiguration(), engineIdentity: .processTap)
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in source },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )
        await controller.start(
            sessionID: "nonce-drift",
            streamKey: harnessStreamKey,
            gatewayBase: "ws://127.0.0.1",
            launchNonce: "nonce-captured"
        )
        for _ in 0..<20 { await Task.yield() }
        let countBefore = events.all.count
        XCTAssertGreaterThanOrEqual(countBefore, 1)

        // Keep the same attempt UUID and all source/generation fields, but
        // drift only the captured launch nonce before a real source callback.
        controller._testOnlyMutateCurrentAttemptIdentity(launchNonce: "drifted-launch-nonce")
        source.emit(.running(SourceHealth(permission: .granted, route: .healthy)), generation: 1)
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(events.all.count, countBefore)
        await controller.stop()
    }

    func testFragmentedCoalescedStrictCommandAndDuplicateTrailingRejection() throws {
        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1:8010/api",
            launchNonce: "nonce-1"
        )
        let wire = try command.framed()
        var decoder = LiveHarnessFrameDecoder()
        XCTAssertEqual(try decoder.append(wire.prefix(3)), [])
        XCTAssertEqual(try decoder.append(wire.dropFirst(3)), [command.canonicalPayload()])
        XCTAssertEqual(try decoder.append(wire + wire), [command.canonicalPayload(), command.canonicalPayload()])
        XCTAssertThrowsError(try LiveHarnessSessionCommand.decode(canonicalPayload: command.canonicalPayload() + Data([0x20])))
        XCTAssertThrowsError(try LiveHarnessSessionCommand.decode(canonicalPayload: Data(#"{"gateway":"g","launch_nonce":"nonce-1","session_id":"session-1","stream_key":"x","stream_key":"x","type":"session","version":1}"#.utf8)))
        XCTAssertThrowsError(try LiveHarnessFrameCodec.frame(payload: Data()))
        XCTAssertThrowsError(try LiveHarnessFrameCodec.frame(payload: Data(repeating: 0, count: LiveHarnessFrameCodec.maximumPayloadLength + 1)))
    }

    func testShutdownRequestAndAcknowledgementAreFragmentableNonceBoundAndKeyless() throws {
        let sessionRef = LiveHarnessControlBinding.sessionBinding(
            sessionID: "session-1",
            launchNonce: "nonce-1"
        )
        let nonce = "sn1_0123456789abcdef0123456789abcdef"
        let binding = LiveHarnessControlBinding.shutdownBinding(
            sessionBinding: sessionRef,
            shutdownNonce: nonce
        )
        let request = try LiveHarnessShutdownRequest(
            shutdownNonce: nonce,
            shutdownBinding: binding
        )
        let requestPayload = request.canonicalPayload()
        XCTAssertFalse(String(decoding: requestPayload, as: UTF8.self).contains("session-1"))
        XCTAssertFalse(String(decoding: requestPayload, as: UTF8.self).contains(harnessStreamKey))
        var decoder = LiveHarnessFrameDecoder()
        let framedRequest = try request.framed()
        XCTAssertEqual(try decoder.append(framedRequest.prefix(2)), [])
        XCTAssertEqual(try decoder.append(framedRequest.dropFirst(2)), [requestPayload])
        XCTAssertEqual(try LiveHarnessShutdownRequest.decode(canonicalPayload: requestPayload), request)

        let acknowledgement = try LiveHarnessShutdownAcknowledgement(
            shutdownNonce: nonce,
            shutdownBinding: binding
        )
        XCTAssertEqual(
            try LiveHarnessShutdownAcknowledgement.decode(canonicalPayload: acknowledgement.canonicalPayload()),
            acknowledgement
        )
        XCTAssertThrowsError(try LiveHarnessShutdownAcknowledgement(
            shutdownNonce: nonce,
            shutdownBinding: "not-binding"
        ))
    }

    func testControlConnectionAdmitsFragmentedShutdownThenWritesMatchingAck() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 5_000_000,
            writeTimeoutNanoseconds: 500_000_000
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let nonce = "sn1_0123456789abcdef0123456789abcdef"
        let sessionRef = LiveHarnessControlBinding.sessionBinding(sessionID: "session-1", launchNonce: "nonce-1")
        let request = try LiveHarnessShutdownRequest(
            shutdownNonce: nonce,
            shutdownBinding: LiveHarnessControlBinding.shutdownBinding(
                sessionBinding: sessionRef,
                shutdownNonce: nonce
            )
        )
        let requestWire = try request.framed()
        let split = requestWire.count / 2
        requestWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, split, 0)
            _ = Darwin.send(descriptors[1], raw.baseAddress!.advanced(by: split), requestWire.count - split, 0)
        }
        _ = Darwin.shutdown(descriptors[1], SHUT_WR)
        let admitted = try connection.waitForShutdownRequest()
        XCTAssertEqual(admitted, request)
        XCTAssertNoThrow(try connection.sendShutdownAcknowledgement(admitted))

        var bytes = [UInt8](repeating: 0, count: 4096)
        let count = Darwin.recv(descriptors[1], &bytes, bytes.count, 0)
        XCTAssertGreaterThan(count, 4)
        var decoder = LiveHarnessFrameDecoder()
        let payloads = try decoder.append(Data(bytes[0..<count]))
        XCTAssertEqual(payloads, [try LiveHarnessShutdownAcknowledgement(
            shutdownNonce: nonce,
            shutdownBinding: request.shutdownBinding
        ).canonicalPayload()])
    }

    func testShutdownWaiterAcceptsFragmentRemainderAfterBlockedRecv() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 500_000_000,
            writeTimeoutNanoseconds: 500_000_000
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let nonce = "sn1_0123456789abcdef0123456789abcdef"
        let sessionRef = LiveHarnessControlBinding.sessionBinding(sessionID: "session-1", launchNonce: "nonce-1")
        let request = try LiveHarnessShutdownRequest(
            shutdownNonce: nonce,
            shutdownBinding: LiveHarnessControlBinding.shutdownBinding(
                sessionBinding: sessionRef,
                shutdownNonce: nonce
            )
        )
        let requestWire = try request.framed()
        let split = requestWire.count / 2
        let ready = DispatchSemaphore(value: 0)
        let finished = DispatchSemaphore(value: 0)
        let result = RequestBox()
        DispatchQueue.global().async {
            do {
                result.set(request: try connection.waitForShutdownRequest(onReady: { ready.signal() }))
            } catch {
                result.set(error: error)
            }
            finished.signal()
        }
        XCTAssertEqual(ready.wait(timeout: .now() + .seconds(1)), .success)
        requestWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, split, 0)
        }
        XCTAssertEqual(finished.wait(timeout: .now() + .milliseconds(50)), .timedOut)
        requestWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!.advanced(by: split), requestWire.count - split, 0)
        }
        _ = Darwin.shutdown(descriptors[1], SHUT_WR)
        XCTAssertEqual(finished.wait(timeout: .now() + .seconds(1)), .success)
        XCTAssertNil(result.error)
        XCTAssertEqual(result.request, request)
        XCTAssertNoThrow(try connection.sendShutdownAcknowledgement(request))
    }

    func testShutdownWaiterPollsPastOneReadTimeoutThenAdmitsTheRequest() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 20_000_000,
            writeTimeoutNanoseconds: 500_000_000
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let nonce = "sn1_0123456789abcdef0123456789abcdef"
        let sessionRef = LiveHarnessControlBinding.sessionBinding(sessionID: "session-1", launchNonce: "nonce-1")
        let request = try LiveHarnessShutdownRequest(
            shutdownNonce: nonce,
            shutdownBinding: LiveHarnessControlBinding.shutdownBinding(
                sessionBinding: sessionRef,
                shutdownNonce: nonce
            )
        )
        let requestWire = try request.framed()
        let ready = DispatchSemaphore(value: 0)
        let finished = DispatchSemaphore(value: 0)
        let result = RequestBox()
        DispatchQueue.global().async {
            do {
                result.set(request: try connection.waitForShutdownRequest(onReady: { ready.signal() }))
            } catch {
                result.set(error: error)
            }
            finished.signal()
        }
        XCTAssertEqual(ready.wait(timeout: .now() + .seconds(1)), .success)
        XCTAssertEqual(finished.wait(timeout: .now() + .milliseconds(50)), .timedOut)
        requestWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, requestWire.count, 0)
        }
        _ = Darwin.shutdown(descriptors[1], SHUT_WR)
        XCTAssertEqual(finished.wait(timeout: .now() + .seconds(1)), .success)
        XCTAssertNil(result.error)
        XCTAssertEqual(result.request, request)
    }

    func testShutdownAcknowledgementWriteIsBoundedAndRetiresAuthority() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 500_000_000,
            writeTimeoutNanoseconds: 5_000_000
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let nonce = "sn1_0123456789abcdef0123456789abcdef"
        let sessionRef = LiveHarnessControlBinding.sessionBinding(sessionID: "session-1", launchNonce: "nonce-1")
        let request = try LiveHarnessShutdownRequest(
            shutdownNonce: nonce,
            shutdownBinding: LiveHarnessControlBinding.shutdownBinding(
                sessionBinding: sessionRef,
                shutdownNonce: nonce
            )
        )
        let requestWire = try request.framed()
        requestWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, requestWire.count, 0)
        }
        _ = Darwin.shutdown(descriptors[1], SHUT_WR)
        XCTAssertEqual(try connection.waitForShutdownRequest(), request)

        let flags = Darwin.fcntl(descriptors[0], F_GETFL)
        XCTAssertGreaterThanOrEqual(flags, 0)
        XCTAssertEqual(Darwin.fcntl(descriptors[0], F_SETFL, flags | O_NONBLOCK), 0)
        let filler = [UInt8](repeating: 0x5a, count: 8192)
        while true {
            let count = filler.withUnsafeBytes { raw in
                Darwin.send(descriptors[0], raw.baseAddress!, filler.count, Int32(MSG_DONTWAIT))
            }
            if count <= 0 {
                if count < 0 {
                    XCTAssertTrue(errno == EAGAIN || errno == EWOULDBLOCK)
                }
                break
            }
        }

        let started = DispatchTime.now().uptimeNanoseconds
        XCTAssertThrowsError(try connection.sendShutdownAcknowledgement(request)) { error in
            XCTAssertTrue(error is LiveHarnessProtocolError)
        }
        let elapsed = DispatchTime.now().uptimeNanoseconds - started
        XCTAssertLessThan(elapsed, 250_000_000)

        let event = try LiveHarnessEvent(
            kind: .activation,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID
        )
        XCTAssertThrowsError(try connection.send(event: event))
    }

    func testShutdownAcknowledgementFollowsEventWriteAndRequiresAdmittedStop() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 5_000_000,
            writeTimeoutNanoseconds: 500_000_000
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let event = try LiveHarnessEvent(
            kind: .activation,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID
        )
        // This is the event-writer completion edge: the event crosses the
        // sole writer before the controller's admitted stop can be acked.
        XCTAssertNoThrow(try connection.send(event: event))

        let nonce = "sn1_0123456789abcdef0123456789abcdef"
        let sessionRef = LiveHarnessControlBinding.sessionBinding(sessionID: "session-1", launchNonce: "nonce-1")
        let request = try LiveHarnessShutdownRequest(
            shutdownNonce: nonce,
            shutdownBinding: LiveHarnessControlBinding.shutdownBinding(
                sessionBinding: sessionRef,
                shutdownNonce: nonce
            )
        )
        let requestWire = try request.framed()
        requestWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, requestWire.count, 0)
        }
        _ = Darwin.shutdown(descriptors[1], SHUT_WR)
        let admitted = try connection.waitForShutdownRequest()
        XCTAssertEqual(admitted, request)
        XCTAssertNoThrow(try connection.sendShutdownAcknowledgement(admitted))

        var bytes = [UInt8](repeating: 0, count: 4096)
        let count = Darwin.recv(descriptors[1], &bytes, bytes.count, 0)
        XCTAssertGreaterThan(count, 4)
        var decoder = LiveHarnessFrameDecoder()
        let payloads = try decoder.append(Data(bytes[0..<count]))
        XCTAssertEqual(payloads.count, 2)
        XCTAssertEqual(payloads[0], try event.canonicalPayload())
        XCTAssertEqual(payloads[1], try LiveHarnessShutdownAcknowledgement(
            shutdownNonce: nonce,
            shutdownBinding: request.shutdownBinding
        ).canonicalPayload())

    }

    func testQueuedEventDrainsBeforeAdmittedShutdownAcknowledgement() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let eventWritePaused = DispatchSemaphore(value: 0)
        let releaseEventWrite = DispatchSemaphore(value: 0)
        let pauseGate = HarnessOneShotGate()
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 5_000_000,
            writeTimeoutNanoseconds: 500_000_000,
            beforeWriteAdmission: {
                if pauseGate.take() {
                    eventWritePaused.signal()
                    _ = releaseEventWrite.wait(timeout: .now() + .seconds(2))
                }
            },
            beforeShutdownSyscall: nil
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let event = try LiveHarnessEvent(
            kind: .activation,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID,
            activeStreamKey: harnessStreamKey
        )
        let eventFinished = DispatchSemaphore(value: 0)
        let eventResult = HarnessLivenessResult()
        DispatchQueue.global().async {
            do {
                try connection.send(event: event)
            } catch let error as LiveHarnessProtocolError {
                eventResult.set(error)
            } catch {
                eventResult.set(.invalidMessage("unexpected event send error"))
            }
            eventFinished.signal()
        }
        XCTAssertEqual(eventWritePaused.wait(timeout: .now() + .seconds(1)), .success)

        let nonce = "sn1_0123456789abcdef0123456789abcdef"
        let sessionRef = LiveHarnessControlBinding.sessionBinding(sessionID: "session-1", launchNonce: "nonce-1")
        let request = try LiveHarnessShutdownRequest(
            shutdownNonce: nonce,
            shutdownBinding: LiveHarnessControlBinding.shutdownBinding(
                sessionBinding: sessionRef,
                shutdownNonce: nonce
            )
        )
        let requestWire = try request.framed()
        requestWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, requestWire.count, 0)
        }
        _ = Darwin.shutdown(descriptors[1], SHUT_WR)
        let admitted = try connection.waitForShutdownRequest()
        XCTAssertEqual(admitted, request)

        // The admitted shutdown is not the acknowledgement boundary.  The
        // queued event writer must retain the active key and serialize its
        // frame before the acknowledgement clears that authority.
        releaseEventWrite.signal()
        XCTAssertEqual(eventFinished.wait(timeout: .now() + .seconds(1)), .success)
        XCTAssertNil(eventResult.result)
        XCTAssertNoThrow(try connection.sendShutdownAcknowledgement(admitted))

        var decoder = LiveHarnessFrameDecoder()
        var payloads: [Data] = []
        while payloads.count < 2 {
            var bytes = [UInt8](repeating: 0, count: 4096)
            let count = Darwin.recv(descriptors[1], &bytes, bytes.count, 0)
            XCTAssertGreaterThan(count, 0)
            payloads.append(contentsOf: try decoder.append(Data(bytes[0..<count])))
        }
        XCTAssertEqual(payloads, [
            try event.canonicalPayload(activeStreamKey: harnessStreamKey),
            try LiveHarnessShutdownAcknowledgement(
                shutdownNonce: nonce,
                shutdownBinding: request.shutdownBinding
            ).canonicalPayload()
        ])
    }

    func testControllerStopBeforeAcknowledgementCannotWriteAck() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 5_000_000,
            writeTimeoutNanoseconds: 500_000_000
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let nonce = "sn1_0123456789abcdef0123456789abcdef"
        let sessionRef = LiveHarnessControlBinding.sessionBinding(sessionID: "session-1", launchNonce: "nonce-1")
        let request = try LiveHarnessShutdownRequest(
            shutdownNonce: nonce,
            shutdownBinding: LiveHarnessControlBinding.shutdownBinding(
                sessionBinding: sessionRef,
                shutdownNonce: nonce
            )
        )
        let requestWire = try request.framed()
        requestWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, requestWire.count, 0)
        }
        _ = Darwin.shutdown(descriptors[1], SHUT_WR)
        let admitted = try connection.waitForShutdownRequest()
        XCTAssertEqual(admitted, request)

        // This models a controller/control-loss retirement winning before the
        // app-side ack turn.  Retirement must make the exact admitted request
        // unusable and emit no acknowledgement bytes.
        connection.requestShutdown()
        XCTAssertThrowsError(try connection.sendShutdownAcknowledgement(admitted))

        var byte: UInt8 = 0
        let preCloseDeadline = DispatchTime.now().uptimeNanoseconds + 50_000_000
        while DispatchTime.now().uptimeNanoseconds < preCloseDeadline {
            let count = Darwin.recv(descriptors[1], &byte, 1, Int32(MSG_DONTWAIT))
            if count > 0 {
                XCTFail("Received unexpected ACK/data byte from shutdown connection before close")
                break
            }
            if count == 0 {
                // EOF or peer shutdown is acceptable before close.
                break
            }
            let err = errno
            if err == EAGAIN || err == EWOULDBLOCK || err == EINTR {
                usleep(1_000)
                continue
            }
            if err == EBADF || err == ENOTCONN || err == ECONNRESET {
                break
            }
            XCTFail("Unexpected recv error before close: \(err)")
            break
        }

        // The connection owner closes the descriptor after waiter join / retirement.
        connection.close()

        var sawEOF = false
        let postCloseDeadline = DispatchTime.now().uptimeNanoseconds + 1_000_000_000
        while DispatchTime.now().uptimeNanoseconds < postCloseDeadline {
            let count = Darwin.recv(descriptors[1], &byte, 1, Int32(MSG_DONTWAIT))
            if count == 0 {
                sawEOF = true
                break
            }
            if count > 0 {
                XCTFail("Received unexpected ACK/data byte from shutdown connection after close")
                break
            }
            let err = errno
            if err == EAGAIN || err == EWOULDBLOCK || err == EINTR {
                usleep(1_000)
                continue
            }
            if err == EBADF || err == ENOTCONN || err == ECONNRESET {
                sawEOF = true
                break
            }
            XCTFail("Unexpected recv error after close: \(err)")
            break
        }
        XCTAssertTrue(sawEOF, "Expected EOF on peer descriptor within deadline after close")
    }

    func testMalformedWrongBindingDuplicateAndEOFNeverAdmitShutdownAck() throws {
        let nonce = "sn1_0123456789abcdef0123456789abcdef"
        let sessionRef = LiveHarnessControlBinding.sessionBinding(sessionID: "session-1", launchNonce: "nonce-1")
        let valid = try LiveHarnessShutdownRequest(
            shutdownNonce: nonce,
            shutdownBinding: LiveHarnessControlBinding.shutdownBinding(
                sessionBinding: sessionRef,
                shutdownNonce: nonce
            )
        )

        let malformedInputs: [Data?] = [
            Data("not-a-frame".utf8),
            try LiveHarnessShutdownRequest(
                shutdownNonce: nonce,
                shutdownBinding: "sd1_\(String(repeating: "0", count: 64))"
            ).framed(),
            try valid.framed() + valid.framed(),
            nil
        ]
        for input in malformedInputs {
            var descriptors: [Int32] = [0, 0]
            XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
            let connection = try LiveHarnessControlConnection(
                descriptor: descriptors[0],
                readTimeoutNanoseconds: 5_000_000,
                writeTimeoutNanoseconds: 500_000_000
            )
            defer {
                connection.close()
                if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
            }
            let command = try LiveHarnessSessionCommand(
                sessionID: "session-1",
                streamKey: harnessStreamKey,
                gateway: "ws://127.0.0.1",
                launchNonce: "nonce-1"
            )
            let commandWire = try command.framed()
            commandWire.withUnsafeBytes { raw in
                _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
            }
            XCTAssertEqual(try connection.receiveOneCommand(), command)

            if let input {
                input.withUnsafeBytes { raw in
                    _ = Darwin.send(descriptors[1], raw.baseAddress!, input.count, 0)
                }
                _ = Darwin.shutdown(descriptors[1], SHUT_WR)
            } else {
                _ = Darwin.close(descriptors[1])
                descriptors[1] = -1
            }
            XCTAssertThrowsError(try connection.waitForShutdownRequest())
            XCTAssertThrowsError(try connection.sendShutdownAcknowledgement(valid))
        }
    }

    func testDelayedDuplicateOrTrailingByteAfterShutdownRequestFailsWaiterAndRejectsAck() throws {
        let nonce = "sn1_0123456789abcdef0123456789abcdef"
        let sessionRef = LiveHarnessControlBinding.sessionBinding(sessionID: "session-1", launchNonce: "nonce-1")
        let request = try LiveHarnessShutdownRequest(
            shutdownNonce: nonce,
            shutdownBinding: LiveHarnessControlBinding.shutdownBinding(
                sessionBinding: sessionRef,
                shutdownNonce: nonce
            )
        )
        let requestWire = try request.framed()
        let trailingInputs: [Data] = [
            requestWire,
            Data([0x00]),
            Data([0x00, 0x00, 0x00, 0x05, 0x7b, 0x7d])
        ]
        for trailing in trailingInputs {
            var descriptors: [Int32] = [0, 0]
            XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
            let decodedHookSemaphore = DispatchSemaphore(value: 0)
            let connection = try LiveHarnessControlConnection(
                descriptor: descriptors[0],
                readTimeoutNanoseconds: 500_000_000,
                writeTimeoutNanoseconds: 500_000_000,
                beforeWriteAdmission: nil,
                beforeShutdownSyscall: nil,
                afterControlLossRetired: nil,
                afterShutdownRequestDecoded: {
                    decodedHookSemaphore.signal()
                }
            )
            defer {
                connection.close()
                if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
            }
            let command = try LiveHarnessSessionCommand(
                sessionID: "session-1",
                streamKey: harnessStreamKey,
                gateway: "ws://127.0.0.1",
                launchNonce: "nonce-1"
            )
            let commandWire = try command.framed()
            commandWire.withUnsafeBytes { raw in
                _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
            }
            XCTAssertEqual(try connection.receiveOneCommand(), command)

            let ready = DispatchSemaphore(value: 0)
            let finished = DispatchSemaphore(value: 0)
            let resultBox = RequestBox()
            DispatchQueue.global().async {
                do {
                    let req = try connection.waitForShutdownRequest(onReady: { ready.signal() })
                    resultBox.set(request: req)
                } catch {
                    resultBox.set(error: error)
                }
                finished.signal()
            }
            XCTAssertEqual(ready.wait(timeout: .now() + .seconds(1)), .success)
            requestWire.withUnsafeBytes { raw in
                _ = Darwin.send(descriptors[1], raw.baseAddress!, requestWire.count, 0)
            }
            XCTAssertEqual(decodedHookSemaphore.wait(timeout: .now() + .seconds(1)), .success)
            trailing.withUnsafeBytes { raw in
                _ = Darwin.send(descriptors[1], raw.baseAddress!, trailing.count, 0)
            }
            _ = Darwin.shutdown(descriptors[1], SHUT_WR)
            XCTAssertEqual(finished.wait(timeout: .now() + .seconds(1)), .success)
            XCTAssertNotNil(resultBox.error)
            XCTAssertNil(resultBox.request)
            XCTAssertThrowsError(try connection.sendShutdownAcknowledgement(request))
        }
    }

    func testMissingPeerWriteEOFReachesBoundedFailureAndRetiresAuthority() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 50_000_000,
            writeTimeoutNanoseconds: 500_000_000
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }
        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let nonce = "sn1_0123456789abcdef0123456789abcdef"
        let sessionRef = LiveHarnessControlBinding.sessionBinding(sessionID: "session-1", launchNonce: "nonce-1")
        let request = try LiveHarnessShutdownRequest(
            shutdownNonce: nonce,
            shutdownBinding: LiveHarnessControlBinding.shutdownBinding(
                sessionBinding: sessionRef,
                shutdownNonce: nonce
            )
        )
        let requestWire = try request.framed()
        requestWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, requestWire.count, 0)
        }
        XCTAssertThrowsError(try connection.waitForShutdownRequest()) { error in
            XCTAssertEqual(error as? LiveHarnessProtocolError, .timeout)
        }
        XCTAssertThrowsError(try connection.sendShutdownAcknowledgement(request))
    }

    func testInjectedPeerAndServerEUIDBoundaryAndOneSessionOwnership() throws {
        let expected = LiveHarnessPeerIdentity(euid: 501, pid: 4242, auditToken: "audit-1", executablePath: "/Applications/TarsCompanion.app/Contents/MacOS/TarsCompanionApp")
        let policy = LiveHarnessPeerPolicy(expectedClient: expected, expectedServerEUID: 501)
        let core = try LiveHarnessControlCore(peerPolicy: policy, launchNonce: "nonce-1")
        XCTAssertThrowsError(try core.authenticateClient(LiveHarnessPeerIdentity(euid: 502, pid: 4242, auditToken: "audit-1", executablePath: expected.executablePath)))
        XCTAssertThrowsError(try core.acceptServer(euid: 502))
        try core.authenticateClient(expected)
        let command = try LiveHarnessSessionCommand(sessionID: "session-1", streamKey: harnessStreamKey, gateway: "ws://127.0.0.1", launchNonce: "nonce-1")
        XCTAssertEqual(try core.consume(command: command), command)
        XCTAssertThrowsError(try core.consume(command: command))
        XCTAssertTrue(core.hasConsumedCommand)
    }

    func testConcreteSourcesExposeTypedEngineIdentity() throws {
        let config = try harnessConfiguration()
        if #available(macOS 14.2, *) {
            let tap = ProcessTapSystemAudioSource(configuration: config, hal: nil)
            XCTAssertEqual(tap.engineIdentity, .processTap)
        }
        let screen = ScreenCaptureKitSystemAudioSource(configuration: config, liveCaptureEnabled: false)
        XCTAssertEqual(screen.engineIdentity, .screenCaptureKit)
    }

    @MainActor
    func testNilConcreteIdentityFailsBeforeAnySystemAudioSideEffect() async throws {
        let nilSource = HarnessSource(configuration: try harnessConfiguration(), engineIdentity: nil)
        let transport = HarnessTransport()
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in transport },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in nilSource },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )
        await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
        XCTAssertEqual(nilSource.startCount, 0)
        XCTAssertEqual(transport.connectCount, 0)
        XCTAssertTrue(events.all.isEmpty)
        guard case .error(let message) = controller.state else { return XCTFail("nil identity must be terminal") }
        XCTAssertTrue(message.contains("identity missing"))
    }

    func testEventSchemaIsCanonicalAndNeverContainsStreamKey() throws {
        let activation = try LiveHarnessEvent(
            kind: .activation,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID,
            activeStreamKey: harnessStreamKey
        )
        let payload = try activation.canonicalPayload()
        XCTAssertEqual(try LiveHarnessEvent.decode(canonicalPayload: payload)["kind"] as? String, "activation")
        XCTAssertFalse(String(decoding: payload, as: UTF8.self).contains("stream_key"))
        XCTAssertFalse(String(decoding: payload, as: UTF8.self).contains(harnessStreamKey))
    }

    func testHealthSchemaEnumsMessagesAndDeviceIdentityAreExact() throws {
        let event = try LiveHarnessEvent(
            kind: .health,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID,
            status: .running(SourceHealth(permission: .unknown, route: .healthy, deviceIdentity: "ProcessTap.SystemAudio"))
        )
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: event.canonicalPayload()) as? [String: Any])
        let status = try XCTUnwrap(object["status"] as? [String: Any])
        func payload(_ replacement: [String: Any]) throws -> Data {
            var mutated = object
            mutated["status"] = replacement
            return try JSONSerialization.data(withJSONObject: mutated, options: [.sortedKeys, .withoutEscapingSlashes])
        }

        let invalidEnums: [(String, String)] = [
            ("permission", "pending"),
            ("route", "changed-by-hostile-peer"),
            ("interruption", "paused"),
            ("sleep", "unknown")
        ]
        for (field, value) in invalidEnums {
            var mutated = status
            mutated[field] = value
            XCTAssertThrowsError(try LiveHarnessEvent.decode(canonicalPayload: payload(mutated)), field)
        }

        var nonFailedCode = status
        nonFailedCode["failure_code"] = "capture-failed"
        XCTAssertThrowsError(try LiveHarnessEvent.decode(canonicalPayload: payload(nonFailedCode)))

        let failed: [String: Any] = [
            "kind": "failed",
            "permission": PermissionState.unknown.rawValue,
            "route": RouteState.unknown.rawValue,
            "interruption": InterruptionState.clear.rawValue,
            "sleep": SleepState.awake.rawValue,
            "overflowed": false,
            "failure_code": LiveHarnessFailureCode.captureFailed.rawValue
        ]
        XCTAssertNoThrow(try LiveHarnessEvent.decode(canonicalPayload: payload(failed)))
        var validDenied = failed
        validDenied["permission"] = PermissionState.denied.rawValue
        validDenied["failure_code"] = LiveHarnessFailureCode.permissionDenied.rawValue
        XCTAssertNoThrow(try LiveHarnessEvent.decode(canonicalPayload: payload(validDenied)))
        for (permission, failureCode) in [
            (PermissionState.unknown.rawValue, LiveHarnessFailureCode.permissionDenied.rawValue),
            (PermissionState.denied.rawValue, LiveHarnessFailureCode.captureFailed.rawValue),
            (PermissionState.granted.rawValue, LiveHarnessFailureCode.captureFailed.rawValue),
            (PermissionState.revoked.rawValue, LiveHarnessFailureCode.captureFailed.rawValue),
            (PermissionState.unknown.rawValue, "unknown")
        ] {
            var hostile = failed
            hostile["permission"] = permission
            hostile["failure_code"] = failureCode
            XCTAssertThrowsError(try LiveHarnessEvent.decode(canonicalPayload: payload(hostile))) {
                _ = $0
            }
        }
        var rawMessage = failed
        rawMessage["message"] = "raw diagnostic"
        XCTAssertThrowsError(try LiveHarnessEvent.decode(canonicalPayload: payload(rawMessage)))
        var missingCode = failed
        missingCode.removeValue(forKey: "failure_code")
        XCTAssertThrowsError(try LiveHarnessEvent.decode(canonicalPayload: payload(missingCode)))

        var invalidIdentity = status
        invalidIdentity["device_identity"] = String(repeating: "x", count: 129)
        XCTAssertThrowsError(try LiveHarnessEvent.decode(canonicalPayload: payload(invalidIdentity)))
        invalidIdentity["device_identity"] = "not valid"
        XCTAssertThrowsError(try LiveHarnessEvent.decode(canonicalPayload: payload(invalidIdentity)))

        let unknownEvent = try LiveHarnessEvent(
            kind: .health,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID,
            status: .failed("failure")
        )
        XCTAssertNoThrow(try unknownEvent.canonicalPayload())
        let deniedEvent = try LiveHarnessEvent(
            kind: .health,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID,
            status: .failed("local diagnostic must not enter the event"),
            failedPermission: .denied,
            failureCode: .permissionDenied
        )
        XCTAssertNoThrow(try deniedEvent.canonicalPayload())
        XCTAssertThrowsError(
            try LiveHarnessEvent(
                kind: .health,
                attemptID: harnessAttemptID,
                launchNonce: "nonce-1",
                sessionID: "session-1",
                generation: 1,
                requestedEngine: .processTap,
                resolvedEngine: .processTap,
                actualEngine: .processTap,
                sourceObjectID: harnessSourceObjectID,
                observerTokenID: harnessObserverID,
                status: .failed("local diagnostic must not enter the event"),
                failedPermission: .denied,
                failureCode: .captureFailed
            )
        )
    }

    func testEventEncoderAndDecoderRejectActivationStatusAndOversizedIdentities() throws {
        XCTAssertThrowsError(
            try LiveHarnessEvent(
                kind: .activation,
                attemptID: harnessAttemptID,
                launchNonce: "nonce-1",
                sessionID: "session-1",
                generation: 1,
                requestedEngine: .processTap,
                resolvedEngine: .processTap,
                actualEngine: .processTap,
                sourceObjectID: harnessSourceObjectID,
                observerTokenID: harnessObserverID,
                status: .running(SourceHealth(permission: .unknown, route: .healthy, deviceIdentity: "ProcessTap.SystemAudio"))
            )
        )

        let health = try LiveHarnessEvent(
            kind: .health,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID,
            status: .running(SourceHealth(permission: .unknown, route: .healthy, deviceIdentity: "ProcessTap.SystemAudio"))
        )
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: health.canonicalPayload()) as? [String: Any])
        func payload(_ field: String, _ value: Any) throws -> Data {
            var mutated = object
            mutated[field] = value
            return try JSONSerialization.data(withJSONObject: mutated, options: [.sortedKeys, .withoutEscapingSlashes])
        }
        XCTAssertThrowsError(try LiveHarnessEvent.decode(canonicalPayload: payload("source_object", String(repeating: "x", count: 257))))
        XCTAssertThrowsError(try LiveHarnessEvent.decode(canonicalPayload: payload("observer_token", String(repeating: "x", count: 257))))
        XCTAssertThrowsError(try LiveHarnessEvent.decode(canonicalPayload: payload("generation", 0)))
    }

    func testFinal18V2BindingsRejectRawSlotsAndUseCrossLanguageSessionBinding() throws {
        let key = harnessStreamKey
        let attempt = "01234567-89ab-cdef-0123-456789abcdef"
        let observer = "fedcba98-7654-3210-fedc-ba9876543210"
        let event = try LiveHarnessEvent(
            kind: .activation,
            attemptID: attempt,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: "ObjectIdentifier(0x1234)",
            observerTokenID: observer,
            activeStreamKey: key
        )
        let payload = try event.canonicalPayload(activeStreamKey: key)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: payload) as? [String: Any])
        XCTAssertEqual(object["version"] as? Int, 2)
        XCTAssertEqual(Set(object.keys), Set([
            "actual_engine", "attempt_id", "generation", "kind", "launch_nonce",
            "observer_binding", "requested_engine", "resolved_engine", "session_binding",
            "source_binding", "type", "version"
        ]))
        XCTAssertNil(object["session_id"])
        XCTAssertNil(object["source_object"])
        XCTAssertNil(object["observer_token"])
        XCTAssertNoThrow(try LiveHarnessEvent.decode(canonicalPayload: payload, activeStreamKey: key))

        for rawField in ["session_id", "source_object", "observer_token", "device_identity"] {
            var hostile = object
            hostile[rawField] = key
            let hostilePayload = try JSONSerialization.data(
                withJSONObject: hostile,
                options: [.sortedKeys, .withoutEscapingSlashes]
            )
            XCTAssertThrowsError(
                try LiveHarnessEvent.decode(canonicalPayload: hostilePayload, activeStreamKey: key),
                rawField
            )
        }

        XCTAssertThrowsError(try LiveHarnessSessionCommand(
            sessionID: key,
            streamKey: key,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        ))
        XCTAssertThrowsError(try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: key,
            gateway: "ws://127.0.0.1",
            launchNonce: key
        ))
        XCTAssertEqual(
            try LiveHarnessEvent.sessionBinding(for: "golden-session", launchNonce: "golden-nonce"),
            "sb1_289fe755b1e22b73070afca6057a38c71701e2bce38131c92291328aa62cebd8"
        )
    }

    func testFinal18FailedHealthHasOnlyCanonicalConstantsAndNoDeviceField() throws {
        let event = try LiveHarnessEvent(
            kind: .health,
            attemptID: "01234567-89ab-cdef-0123-456789abcdef",
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: "ObjectIdentifier(0x1234)",
            observerTokenID: "fedcba98-7654-3210-fedc-ba9876543210",
            status: .failed("local diagnostic"),
            failedPermission: .unknown,
            failureCode: .captureFailed
        )
        let payload = try event.canonicalPayload()
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: payload) as? [String: Any])
        let status = try XCTUnwrap(object["status"] as? [String: Any])
        XCTAssertNil(status["device_identity"])
        XCTAssertEqual(status["route"] as? String, RouteState.unknown.rawValue)
        XCTAssertEqual(status["interruption"] as? String, InterruptionState.clear.rawValue)
        XCTAssertEqual(status["sleep"] as? String, SleepState.awake.rawValue)
        XCTAssertEqual(status["overflowed"] as? Bool, false)
        XCTAssertEqual(status["permission"] as? String, PermissionState.unknown.rawValue)
        XCTAssertEqual(status["failure_code"] as? String, LiveHarnessFailureCode.captureFailed.rawValue)
        for field in ["route", "interruption", "sleep", "overflowed", "permission", "failure_code"] {
            var mutatedStatus = status
            mutatedStatus[field] = field == "overflowed" ? true : "invalid"
            var mutated = object
            mutated["status"] = mutatedStatus
            let mutatedPayload = try JSONSerialization.data(
                withJSONObject: mutated,
                options: [.sortedKeys, .withoutEscapingSlashes]
            )
            XCTAssertThrowsError(try LiveHarnessEvent.decode(canonicalPayload: mutatedPayload), field)
        }
    }

    @MainActor
    func testActivatedSourcePermissionFailureEmitsFencedDeniedBeforeCleanup() async throws {
        let source = HarnessSource(configuration: try harnessConfiguration(), engineIdentity: .processTap)
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in source },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )

        await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
        source.emit(.failed(SystemAudioCaptureMonitor.permissionDeniedMessage), generation: 1)
        for _ in 0..<100 { await Task.yield() }

        let observed = events.all
        guard let failedIndex = observed.firstIndex(where: { event in
            guard event.kind == .health, let status = event.status else { return false }
            if case .failed = status { return true }
            return false
        }) else {
            return XCTFail("fenced terminal failure was not emitted")
        }
        let failed = observed[failedIndex]
        XCTAssertTrue(observed[..<failedIndex].contains(where: { $0.kind == .activation }))
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: failed.canonicalPayload()) as? [String: Any])
        let status = try XCTUnwrap(object["status"] as? [String: Any])
        XCTAssertEqual(status["kind"] as? String, "failed")
        XCTAssertEqual(status["permission"] as? String, PermissionState.denied.rawValue)
        XCTAssertEqual(status["failure_code"] as? String, LiveHarnessFailureCode.permissionDenied.rawValue)
        XCTAssertFalse(failed.redactedFields.values.contains(where: { $0.contains("O macOS negou") }))
        XCTAssertEqual(controller.systemAudioHealth.permission, PermissionState.denied)
        XCTAssertEqual(source.stopCount, 1)
        guard case .error = controller.state else { return XCTFail("terminal failure must surface as error") }
        await controller.stop()
    }

    @MainActor
    func testActivatedSourceNonPermissionFailureSerializesUnknownAndCleansUp() async throws {
        let source = HarnessSource(configuration: try harnessConfiguration(), engineIdentity: .processTap)
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in source },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )

        await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
        source.emit(.failed("route failed"), generation: 1)
        for _ in 0..<100 { await Task.yield() }

        guard let failed = events.all.first(where: { event in
            guard event.kind == .health, let status = event.status else { return false }
            if case .failed = status { return true }
            return false
        }) else {
            return XCTFail("non-permission terminal failure was not emitted")
        }
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: failed.canonicalPayload()) as? [String: Any])
        let status = try XCTUnwrap(object["status"] as? [String: Any])
        XCTAssertEqual(status["kind"] as? String, "failed")
        XCTAssertEqual(status["permission"] as? String, PermissionState.unknown.rawValue)
        XCTAssertEqual(status["failure_code"] as? String, LiveHarnessFailureCode.captureFailed.rawValue)
        XCTAssertFalse(failed.redactedFields.keys.contains("message"))
        XCTAssertEqual(controller.systemAudioHealth.permission, PermissionState.unknown)
        XCTAssertEqual(source.stopCount, 1)
        await controller.stop()
    }

    @MainActor
    func testTypedPermissionDenialDuringStartEmitsOnlyFencedFailedHealth() async throws {
        let source = HarnessSource(
            configuration: try harnessConfiguration(),
            engineIdentity: .processTap,
            startError: SystemAudioCaptureFailure.denied
        )
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in source },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )

        await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
        for _ in 0..<100 { await Task.yield() }

        let observed = events.all
        XCTAssertEqual(observed.count, 1)
        guard let event = observed.first else { return XCTFail("typed startup denial was not emitted") }
        XCTAssertEqual(event.kind, .health)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: event.canonicalPayload()) as? [String: Any])
        let status = try XCTUnwrap(object["status"] as? [String: Any])
        XCTAssertEqual(status["kind"] as? String, "failed")
        XCTAssertEqual(status["permission"] as? String, PermissionState.denied.rawValue)
        XCTAssertEqual(status["failure_code"] as? String, LiveHarnessFailureCode.permissionDenied.rawValue)
        XCTAssertFalse(String(data: try event.canonicalPayload(), encoding: .utf8)?.contains("O macOS negou") ?? true)
        XCTAssertFalse(observed.contains(where: { $0.kind == .activation }))
        XCTAssertEqual(source.stopCount, 1)
        XCTAssertNil(controller.activeSessionID)
        XCTAssertEqual(controller.systemAudioHealth.permission, PermissionState.denied)
        guard case .error = controller.state else { return XCTFail("typed denial must remain an error") }
    }

    @MainActor
    func testControlLossStopsSuspendedStartBeforeGateReleaseAndFencesLateReturn() async throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 5_000_000,
            writeTimeoutNanoseconds: 500_000_000
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let gate = HarnessStartGate()
        let source = HarnessSource(
            configuration: try harnessConfiguration(),
            engineIdentity: .processTap,
            startGate: gate
        )
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in source },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )
        let coordinator = LiveHarnessControlCoordinator()
        let coordinatorFinished = HarnessFlag()
        let coordinatorTask = Task {
            await coordinator.run(
                start: {
                    await controller.start(
                        sessionID: command.sessionID,
                        streamKey: command.streamKey,
                        gatewayBase: command.gateway,
                        launchNonce: command.launchNonce
                    )
                },
                stop: {
                    await controller.stop()
                },
                waitForControlLoss: { ready in
                    do { try connection.waitForControlLoss(onReady: ready) } catch {}
                }
            )
            coordinatorFinished.set()
        }

        await gate.waitUntilEntered()
        _ = Darwin.close(descriptors[1])
        descriptors[1] = -1
        var stopObserved = false
        for _ in 0..<1_000 {
            if source.stopCount > 0 {
                stopObserved = true
                break
            }
            await Task.yield()
        }
        XCTAssertTrue(stopObserved, "control loss must stop a suspended source before release")
        XCTAssertEqual(controller.state, .idle)
        XCTAssertFalse(coordinatorFinished.value, "coordinator must join suspended start before returning")

        gate.release()
        await coordinatorTask.value
        XCTAssertTrue(coordinatorFinished.value)
        for _ in 0..<100 { await Task.yield() }
        XCTAssertTrue(events.all.isEmpty, "stale start return must not publish activation or health")
        XCTAssertNil(controller.activeSessionID)
    }

    @MainActor
    func testTrailingByteDuringSuspendedStartStopsBeforeReleaseAndJoinsStart() async throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 5_000_000,
            writeTimeoutNanoseconds: 500_000_000
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let gate = HarnessStartGate()
        let source = HarnessSource(configuration: try harnessConfiguration(), engineIdentity: .processTap, startGate: gate)
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in source },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )
        let coordinator = LiveHarnessControlCoordinator()
        let coordinatorFinished = HarnessFlag()
        let coordinatorTask = Task {
            await coordinator.run(
                start: {
                    await controller.start(
                        sessionID: command.sessionID,
                        streamKey: command.streamKey,
                        gatewayBase: command.gateway,
                        launchNonce: command.launchNonce
                    )
                },
                stop: { await controller.stop() },
                waitForControlLoss: { ready in
                    do { try connection.waitForControlLoss(onReady: ready) } catch {}
                }
            )
            coordinatorFinished.set()
        }

        await gate.waitUntilEntered()
        var trailing: UInt8 = 0x01
        XCTAssertEqual(Darwin.send(descriptors[1], &trailing, 1, 0), 1)
        var stopObserved = false
        for _ in 0..<1_000 {
            if source.stopCount > 0 { stopObserved = true; break }
            await Task.yield()
        }
        XCTAssertTrue(stopObserved)
        XCTAssertFalse(coordinatorFinished.value)
        gate.release()
        await coordinatorTask.value
        XCTAssertTrue(coordinatorFinished.value)
        XCTAssertTrue(events.all.isEmpty)
        XCTAssertNil(controller.activeSessionID)
    }

    @MainActor
    func testWriterRequestShutdownDuringSuspendedStartStopsBeforeRelease() async throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 5_000_000,
            writeTimeoutNanoseconds: 500_000_000
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let gate = HarnessStartGate()
        let source = HarnessSource(configuration: try harnessConfiguration(), engineIdentity: .processTap, startGate: gate)
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in source },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )
        let coordinator = LiveHarnessControlCoordinator()
        let coordinatorFinished = HarnessFlag()
        let coordinatorTask = Task {
            await coordinator.run(
                start: {
                    await controller.start(
                        sessionID: command.sessionID,
                        streamKey: command.streamKey,
                        gatewayBase: command.gateway,
                        launchNonce: command.launchNonce
                    )
                },
                stop: { await controller.stop() },
                waitForControlLoss: { ready in
                    do { try connection.waitForControlLoss(onReady: ready) } catch {}
                }
            )
            coordinatorFinished.set()
        }

        await gate.waitUntilEntered()
        // This is the event-writer failure edge: it requests shutdown without
        // closing/reusing the descriptor underneath the recv owner.
        connection.requestShutdown()
        var stopObserved = false
        for _ in 0..<1_000 {
            if source.stopCount > 0 { stopObserved = true; break }
            await Task.yield()
        }
        XCTAssertTrue(stopObserved)
        XCTAssertFalse(coordinatorFinished.value)
        gate.release()
        await coordinatorTask.value
        XCTAssertTrue(coordinatorFinished.value)
        XCTAssertTrue(events.all.isEmpty)
        XCTAssertNil(controller.activeSessionID)
    }

    @MainActor
    func testControlLossBeforeStartEntryWaitsForEntryThenJoinsWithoutStarting() async throws {
        let entryGate = HarnessStartGate()
        let entryBarrierReached = HarnessFlag()
        let startOwnerEntered = HarnessFlag()
        let lossReported = HarnessFlag()
        let startInvoked = HarnessFlag()
        let stopInvoked = HarnessFlag()
        let source = HarnessSource(configuration: try harnessConfiguration(), engineIdentity: .processTap)
        let transport = HarnessTransport()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in transport },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in source },
            harnessMode: true
        )
        let coordinator = LiveHarnessControlCoordinator()
        let coordinatorTask = Task {
            await coordinator.run(
                start: {
                    startInvoked.set()
                    await controller.start(
                        sessionID: "session-1",
                        streamKey: harnessStreamKey,
                        gatewayBase: "ws://127.0.0.1",
                        launchNonce: "nonce-1"
                    )
                },
                stop: {
                    stopInvoked.set()
                    await controller.stop()
                },
                waitForControlLoss: { ready in
                    ready()
                    lossReported.set()
                },
                beforeStartEntry: {
                    entryBarrierReached.set()
                    await entryGate.wait()
                },
                onStartEntered: {
                    startOwnerEntered.set()
                }
            )
        }

        for _ in 0..<1_000 {
            if entryBarrierReached.value && lossReported.value { break }
            await Task.yield()
        }
        XCTAssertTrue(entryBarrierReached.value, "start task must reach its pre-entry barrier")
        XCTAssertTrue(lossReported.value, "control loss must be reported before start entry")
        XCTAssertFalse(startOwnerEntered.value, "loss must precede the cancellation-aware start owner")
        XCTAssertFalse(startInvoked.value, "controller.start must not begin before the owner grants it")

        entryGate.release()
        await coordinatorTask.value

        XCTAssertTrue(startOwnerEntered.value, "the coordinator must establish the entry edge before completing loss")
        XCTAssertTrue(stopInvoked.value, "control loss must invoke stop after start ownership is established")
        XCTAssertFalse(startInvoked.value)
        XCTAssertEqual(source.startCount, 0, "source must never start")
        XCTAssertEqual(transport.connectCount, 0, "sink must never connect")
        XCTAssertNil(controller.activeSessionID)
        XCTAssertEqual(controller.state, .idle)
    }

    func testControlConnectionRejectsDuplicateWaiterAndClosesOnlyAfterJoin() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 5_000_000,
            writeTimeoutNanoseconds: 500_000_000
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        let ready = DispatchSemaphore(value: 0)
        let finished = DispatchSemaphore(value: 0)
        let result = HarnessLivenessResult()
        DispatchQueue.global().async {
            do {
                try connection.waitForControlLoss(onReady: { ready.signal() })
            } catch let error as LiveHarnessProtocolError {
                result.set(error)
            } catch {
                result.set(.controlLost)
            }
            finished.signal()
        }
        XCTAssertEqual(ready.wait(timeout: .now() + .seconds(1)), .success)
        XCTAssertThrowsError(try connection.waitForControlLoss()) { error in
            XCTAssertEqual(error as? LiveHarnessProtocolError, .duplicateWaiter)
        }
        // Calling close while recv owns the descriptor must request shutdown,
        // not close/reuse the numeric fd underneath the waiter.
        // The ready callback is intentionally immediately before the first
        // recv.  Allow one bounded read deadline to establish that boundary
        // before exercising the active-waiter close edge.
        Thread.sleep(forTimeInterval: 0.05)
        connection.close()
        // Close the peer only as the deterministic EOF completion edge.  The
        // assertion below still proves that the connection-side close request
        // did not close or reuse its numeric descriptor under the waiter.
        _ = Darwin.shutdown(descriptors[1], SHUT_RDWR)
        XCTAssertEqual(finished.wait(timeout: .now() + .seconds(1)), .success)
        XCTAssertEqual(result.result, .controlLost)

        let waiterDescriptor = descriptors[0]
        let flagsBeforeOwnerClose = fcntl(waiterDescriptor, F_GETFD)
        XCTAssertGreaterThanOrEqual(flagsBeforeOwnerClose, 0)
        // Churn local descriptors while the waiter fd is still owned.  None
        // may reuse its numeric value before the owner performs final close.
        for _ in 0..<256 {
            var temporary: [Int32] = [0, 0]
            XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &temporary), 0)
            XCTAssertNotEqual(temporary[0], waiterDescriptor)
            XCTAssertNotEqual(temporary[1], waiterDescriptor)
            _ = Darwin.close(temporary[0])
            _ = Darwin.close(temporary[1])
            XCTAssertGreaterThanOrEqual(fcntl(waiterDescriptor, F_GETFD), 0)
        }

        // Only the owner closes after the waiter has returned.  Repeated
        // close/shutdown calls must remain inert and must not touch a reused
        // descriptor number.
        connection.close()
        connection.close()
        connection.requestShutdown()
        let finalResult = fcntl(waiterDescriptor, F_GETFD)
        let finalErrno = errno
        XCTAssertEqual(finalResult, -1)
        XCTAssertEqual(finalErrno, EBADF)

        // Reuse the exact numeric descriptor after owner close.  Stale
        // connection operations must be inert and must not shut down or
        // write into the replacement channel.
        var replacement: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &replacement), 0)
        let replacementPeer: Int32
        if replacement[0] == waiterDescriptor {
            replacementPeer = replacement[1]
            replacement[1] = -1
        } else {
            XCTAssertEqual(Darwin.dup2(replacement[0], waiterDescriptor), waiterDescriptor)
            _ = Darwin.close(replacement[0])
            replacement[0] = -1
            replacementPeer = replacement[1]
            replacement[1] = -1
        }
        XCTAssertGreaterThanOrEqual(fcntl(waiterDescriptor, F_GETFD), 0)
        connection.close()
        connection.requestShutdown()
        let staleEvent = try LiveHarnessEvent(
            kind: .activation,
            attemptID: alternateHarnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID
        )
        XCTAssertThrowsError(try connection.send(event: staleEvent))
        var marker: UInt8 = 0x5a
        XCTAssertEqual(Darwin.send(replacementPeer, &marker, 1, 0), 1)
        var received: UInt8 = 0
        XCTAssertEqual(Darwin.recv(waiterDescriptor, &received, 1, 0), 1)
        XCTAssertEqual(received, marker)
        _ = Darwin.close(replacementPeer)
    }

    func testDurableBidirectionalConnectionWritesEventsAndRejectsControlEOF() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        defer {
            _ = Darwin.close(descriptors[1])
        }
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 500_000_000,
            writeTimeoutNanoseconds: 500_000_000
        )
        defer { connection.close() }

        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, 3, 0)
            _ = Darwin.send(descriptors[1], raw.baseAddress!.advanced(by: 3), commandWire.count - 3, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let event = try LiveHarnessEvent(
            kind: .activation,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID,
            activeStreamKey: harnessStreamKey
        )
        try connection.send(event: event)
        var received = [UInt8](repeating: 0, count: 4096)
        let count = Darwin.recv(descriptors[1], &received, received.count, 0)
        XCTAssertGreaterThan(count, 4)
        let wire = Data(received[0..<count])
        var decoder = LiveHarnessFrameDecoder()
        XCTAssertEqual(try decoder.append(wire), [try event.canonicalPayload()])

        _ = Darwin.close(descriptors[1])
        XCTAssertThrowsError(try connection.waitForControlLoss()) { error in
            XCTAssertEqual(error as? LiveHarnessProtocolError, .controlLost)
        }
        descriptors[1] = -1
    }

    func testControlLivenessTimeoutsRemainPollingUntilEOFWhileEventWrites() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 5_000_000,
            writeTimeoutNanoseconds: 500_000_000
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        // Exercise the production receive path first.  The same initialized
        // descriptor must then survive repeated quiet read deadlines while
        // the harness continues to receive events in the opposite direction.
        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let event = try LiveHarnessEvent(
            kind: .activation,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID
        )
        let finished = DispatchSemaphore(value: 0)
        let result = HarnessLivenessResult()
        DispatchQueue.global().async {
            do {
                try connection.waitForControlLoss()
            } catch let error as LiveHarnessProtocolError {
                result.set(error)
            } catch {
                result.set(.controlLost)
            }
            finished.signal()
        }

        // The 100 ms quiet interval spans many 5 ms receive deadlines.  A
        // timeout is only a polling interval; it must not retire the session.
        usleep(100_000)
        try connection.send(event: event)
        var received = [UInt8](repeating: 0, count: 4096)
        let count = Darwin.recv(descriptors[1], &received, received.count, 0)
        XCTAssertGreaterThan(count, 4)
        XCTAssertEqual(finished.wait(timeout: .now() + .milliseconds(1)), .timedOut)

        _ = Darwin.close(descriptors[1])
        descriptors[1] = -1
        XCTAssertEqual(finished.wait(timeout: .now() + .seconds(1)), .success)
        XCTAssertEqual(result.result, .controlLost)
    }

    func testInitialCommandUsesOneAbsoluteDeadlineAcrossSlowFragments() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 50_000_000,
            writeTimeoutNanoseconds: 500_000_000
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let wire = try command.framed()
        let slowPeer = descriptors[1]
        let senderFinished = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            for byte in wire.prefix(20) {
                var byte = byte
                _ = Darwin.send(slowPeer, &byte, 1, 0)
                usleep(5_000)
            }
            senderFinished.signal()
        }

        let started = DispatchTime.now().uptimeNanoseconds
        XCTAssertThrowsError(try connection.receiveOneCommand()) { error in
            XCTAssertEqual(error as? LiveHarnessProtocolError, .timeout)
        }
        let elapsed = DispatchTime.now().uptimeNanoseconds - started
        // Each fragment arrives well inside the old 50 ms per-call timeout,
        // but the transaction must still expire near its single 50 ms budget.
        XCTAssertLessThan(elapsed, 120_000_000)
        XCTAssertEqual(senderFinished.wait(timeout: .now() + .seconds(1)), .success)
    }

    func testEventWriteUsesOneAbsoluteDeadlineAcrossSlowDrain() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        defer {
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        var sendBuffer: Int32 = 1_024
        XCTAssertEqual(
            withUnsafePointer(to: &sendBuffer) {
                Darwin.setsockopt(
                    descriptors[0],
                    SOL_SOCKET,
                    SO_SNDBUF,
                    $0,
                    socklen_t(MemoryLayout<Int32>.size)
                )
            },
            0
        )
        let originalFlags = fcntl(descriptors[0], F_GETFL)
        XCTAssertGreaterThanOrEqual(originalFlags, 0)
        XCTAssertEqual(fcntl(descriptors[0], F_SETFL, originalFlags | O_NONBLOCK), 0)
        let filler = [UInt8](repeating: 0x7f, count: 4_096)
        var filled = 0
        while filled < 4 * 1024 * 1024 {
            let count = filler.withUnsafeBytes { raw in
                Darwin.send(descriptors[0], raw.baseAddress!, filler.count, 0)
            }
            if count > 0 {
                filled += count
                continue
            }
            XCTAssertTrue(errno == EAGAIN || errno == EWOULDBLOCK)
            break
        }
        XCTAssertGreaterThan(filled, 0)
        XCTAssertEqual(fcntl(descriptors[0], F_SETFL, originalFlags), 0)

        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 500_000_000,
            writeTimeoutNanoseconds: 60_000_000
        )
        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            XCTAssertEqual(
                Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0),
                commandWire.count
            )
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let drainFinished = DispatchSemaphore(value: 0)
        let drainPeer = descriptors[1]
        XCTAssertEqual(fcntl(drainPeer, F_SETFL, fcntl(drainPeer, F_GETFL) | O_NONBLOCK), 0)
        DispatchQueue.global().async {
            defer { drainFinished.signal() }
            let end = DispatchTime.now().uptimeNanoseconds + 200_000_000
            var byte: UInt8 = 0
            while DispatchTime.now().uptimeNanoseconds < end {
                let count = Darwin.recv(drainPeer, &byte, 1, 0)
                if count > 0 {
                    usleep(5_000)
                } else if count == 0 {
                    return
                } else if errno == EAGAIN || errno == EWOULDBLOCK {
                    usleep(1_000)
                } else {
                    return
                }
            }
        }

        let largeEvent = try LiveHarnessEvent(
            kind: .activation,
            attemptID: alternateHarnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID
        )
        let started = DispatchTime.now().uptimeNanoseconds
        XCTAssertThrowsError(try connection.send(event: largeEvent)) { error in
            XCTAssertEqual(error as? LiveHarnessProtocolError, .timeout)
        }
        let elapsed = DispatchTime.now().uptimeNanoseconds - started
        // The peer frees only one byte every 5 ms.  A per-write timeout would
        // keep resetting as the stream makes partial progress; the transaction
        // deadline must bound the whole framed event instead.
        XCTAssertLessThan(elapsed, 150_000_000)
        connection.close()
        XCTAssertEqual(drainFinished.wait(timeout: .now() + .seconds(1)), .success)
    }

    func testPeerCloseDuringRealWriteSurvivesAndReportsControlLostInsteadOfSIGPIPE() throws {
        var descriptors: [Int32] = [0, 0]
        var reportPipe: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        XCTAssertEqual(pipe(&reportPipe), 0)
        // Install SO_NOSIGPIPE in the parent before forking.  The child then
        // exercises the exact initialized descriptor after its peer closes;
        // this avoids invoking Foundation initialization in a forked process.
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 500_000_000,
            writeTimeoutNanoseconds: 500_000_000
        )
        defer { connection.close() }
        let event = try LiveHarnessEvent(
            kind: .activation,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID
        )
        _ = setenv("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES", 1)
        let wireBytes = [UInt8](try event.framed())

        // Close the peer before forking so the child cannot race the parent
        // while attempting the production-equivalent write.
        _ = Darwin.close(descriptors[1])
        descriptors[1] = -1
        let child = task11Fork()
        guard child >= 0 else { throw LiveHarnessProtocolError.controlLost }
        if child == 0 {
            _ = Darwin.close(reportPipe[0])
            var result: UInt8 = 3
            // Do not invoke Foundation/NSLock after fork (the XCTest host may
            // have an Objective-C class initializer in another thread).  This
            // is the same production descriptor and Darwin.send flags used by
            // writeAll; the initialized connection above installed
            // SO_NOSIGPIPE before this child performs the real write.
            wireBytes.withUnsafeBytes { raw in
                let count = Darwin.send(descriptors[0], raw.baseAddress!, wireBytes.count, 0)
                if count < 0, errno == EPIPE {
                    result = 1
                } else if count >= 0 {
                    result = 2 // A closed peer must not make a successful write.
                } else {
                    result = 4
                }
            }
            _ = Darwin.write(reportPipe[1], &result, 1)
            _exit(0)
        }

        XCTAssertGreaterThan(child, 0)
        _ = Darwin.close(reportPipe[1])
        var waitStatus: Int32 = 0
        XCTAssertEqual(waitpid(child, &waitStatus, 0), child)
        XCTAssertEqual(waitStatus & 0x7f, 0)
        XCTAssertEqual((waitStatus >> 8) & 0xff, 0)
        var result: UInt8 = 0
        XCTAssertEqual(Darwin.read(reportPipe[0], &result, 1), 1)
        XCTAssertEqual(result, 1)
        _ = Darwin.close(reportPipe[0])
    }

    @MainActor
    func testMismatchFailsBeforeSinkOrSourceStartAndBeforeActivation() async throws {
        weak var weakRejectedSource: HarnessSource?
        weak var weakTransport: HarnessTransport?
        var rejectedStartCount = 0
        do {
            let wrongSource = HarnessSource(configuration: try harnessConfiguration(), engineIdentity: .screenCaptureKit)
            weakRejectedSource = wrongSource
            let transport = HarnessTransport()
            weakTransport = transport
            let events = HarnessEvents()
            let controller = CompanionSessionController(
                transportFactory: { [weak transport] _, _ in
                    guard let transport else { fatalError("fixture transport deallocated before factory use") }
                    return transport
                },
                enginePreference: .processTap,
                operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
                engineSourceFactory: { [weak wrongSource] _, _, _ in
                    guard let wrongSource else { fatalError("fixture source deallocated before factory use") }
                    return wrongSource
                },
                harnessMode: true,
                harnessObserver: { events.append($0) }
            )
            await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
            rejectedStartCount = wrongSource.startCount
            XCTAssertEqual(rejectedStartCount, 0)
            XCTAssertEqual(transport.connectCount, 0)
            XCTAssertTrue(events.all.isEmpty)
            guard case .error(let message) = controller.state else { return XCTFail("identity mismatch must be terminal") }
            XCTAssertTrue(message.contains("engine mismatch"))
            let children = Dictionary(uniqueKeysWithValues: Mirror(reflecting: controller).children.compactMap { child in
                child.label.map { ($0, child.value) }
            })
            XCTAssertTrue(mirrorIsNilOptional(children["source"]))
            XCTAssertTrue(mirrorIsNilOptional(children["sink"]))
            await controller.stop()
        }
        for _ in 0..<20 { await Task.yield() }
        XCTAssertNil(weakRejectedSource)
        XCTAssertNil(weakTransport)
        XCTAssertEqual(rejectedStartCount, 0)
    }

    @MainActor
    func testMissingIdentitySourceIsNotRetainedAfterRejectedStart() async throws {
        weak var weakRejectedSource: HarnessSource?
        do {
            let nilSource = HarnessSource(configuration: try harnessConfiguration(), engineIdentity: nil)
            weakRejectedSource = nilSource
            let controller = CompanionSessionController(
                transportFactory: { _, _ in HarnessTransport() },
                enginePreference: .processTap,
                operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
                engineSourceFactory: { [weak nilSource] _, _, _ in
                    guard let nilSource else { fatalError("fixture source deallocated before factory use") }
                    return nilSource
                },
                harnessMode: true
            )
            await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
            XCTAssertEqual(nilSource.startCount, 0)
            let children = Dictionary(uniqueKeysWithValues: Mirror(reflecting: controller).children.compactMap { child in
                child.label.map { ($0, child.value) }
            })
            XCTAssertTrue(mirrorIsNilOptional(children["source"]))
            XCTAssertTrue(mirrorIsNilOptional(children["sink"]))
            await controller.stop()
        }
        for _ in 0..<20 { await Task.yield() }
        XCTAssertNil(weakRejectedSource)
    }

    @MainActor
    func testActivationPrecedesHealthForReplacementGeneration() async throws {
        let source = HarnessSource(configuration: try harnessConfiguration(), engineIdentity: .processTap)
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in source },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )
        await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
        for _ in 0..<10 { await Task.yield() }
        source.emit(.running(SourceHealth(permission: .unknown, route: .healthy)), generation: 2)
        for _ in 0..<10 { await Task.yield() }
        let generationTwo = events.all.filter { $0.generation == 2 }
        XCTAssertGreaterThanOrEqual(generationTwo.count, 2)
        XCTAssertEqual(generationTwo.first?.kind, .activation)
        XCTAssertEqual(generationTwo.dropFirst().first?.kind, .health)
        await controller.stop()
        let countAfterStop = events.all.count
        source.emit(.running(SourceHealth(permission: .granted, route: .healthy)), generation: 3)
        for _ in 0..<5 { await Task.yield() }
        XCTAssertEqual(events.all.count, countAfterStop)
    }

    @MainActor
    func testReplacementGenerationWhileInitialStartIsSuspendedNeverPublishesStaleActivation() async throws {
        let gate = HarnessStartGate()
        let source = HarnessSource(
            configuration: try harnessConfiguration(),
            engineIdentity: .processTap,
            startGate: gate
        )
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in source },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )

        let startTask = Task { await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1") }
        await gate.waitUntilEntered()
        source.emit(.running(SourceHealth(permission: .granted, route: .healthy)), generation: 2)
        for _ in 0..<100 {
            if events.all.contains(where: { $0.kind == .activation && $0.generation == 2 }) { break }
            await Task.yield()
        }
        XCTAssertTrue(events.all.contains(where: { $0.kind == .activation && $0.generation == 2 }))

        // Let the original source.start return only after generation 2 has
        // been accepted.  The post-start generation equality fence must then
        // suppress its stale generation-1 activation.
        gate.release()
        await startTask.value
        for _ in 0..<20 { await Task.yield() }
        let observed = events.all
        guard let generationTwoIndex = observed.firstIndex(where: { $0.kind == .activation && $0.generation == 2 }) else {
            return XCTFail("replacement generation activation was not observed")
        }
        XCTAssertFalse(observed.dropFirst(generationTwoIndex + 1).contains(where: { $0.kind == .activation && $0.generation == 1 }))
        await controller.stop()
    }

    @MainActor
    func testRestartUsesFreshAttemptAndNonceEvenWhenGenerationResets() async throws {
        let first = HarnessSource(configuration: try harnessConfiguration(), engineIdentity: .processTap)
        let second = HarnessSource(configuration: try harnessConfiguration(), engineIdentity: .processTap)
        var index = 0
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in defer { index += 1 }; return index == 0 ? first : second },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )
        await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1", launchNonce: "nonce-1")
        for _ in 0..<5 { await Task.yield() }
        await controller.stop()
        await controller.start(sessionID: "session-2", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1", launchNonce: "nonce-2")
        for _ in 0..<5 { await Task.yield() }
        let activations = events.all.filter { $0.kind == .activation }
        XCTAssertGreaterThanOrEqual(activations.count, 2)
        XCTAssertTrue(events.all.allSatisfy { !String(describing: $0).contains(harnessStreamKey) })
        XCTAssertNotEqual(activations[0].attemptID, activations[1].attemptID)
        XCTAssertNotEqual(activations[0].launchNonce, activations[1].launchNonce)
        XCTAssertEqual(activations[0].generation, 1)
        XCTAssertEqual(activations[1].generation, 1)
        await controller.stop()
    }

    func testEventInitializerScansRawAndDerivedValuesBeforeRetention() throws {
        let collisionKey = "01fac875f28cf65a23a9258a2549ee2eca6234b932a"
        XCTAssertEqual(collisionKey.count, 43)

        // The deterministic session-1/nonce-1 binding begins with the
        // collision key. The initializer must reject it before any event is
        // formed, so no observer/event object can retain the active key.
        XCTAssertThrowsError(try LiveHarnessEvent(
            kind: .activation,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID,
            activeStreamKey: collisionKey
        ))

        // The ordinary harness key remains a valid separate construction.
        let event = try LiveHarnessEvent(
            kind: .activation,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID,
            activeStreamKey: harnessStreamKey
        )
        XCTAssertTrue(event.sessionBinding.hasPrefix("sb1_"))
        XCTAssertEqual(event.sessionBinding.count, 68)
        XCTAssertFalse(String(decoding: try event.canonicalPayload(activeStreamKey: harnessStreamKey), as: UTF8.self).contains(harnessStreamKey))
    }

    func testShutdownBarrierRetiresKeyBeforeConcurrentEventSend() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let sendPaused = DispatchSemaphore(value: 0)
        let releaseSend = DispatchSemaphore(value: 0)
        let shutdownPaused = DispatchSemaphore(value: 0)
        let releaseShutdown = DispatchSemaphore(value: 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 5_000_000,
            writeTimeoutNanoseconds: 500_000_000,
            beforeWriteAdmission: {
                sendPaused.signal()
                _ = releaseSend.wait(timeout: .now() + .seconds(2))
            },
            beforeShutdownSyscall: {
                shutdownPaused.signal()
                _ = releaseShutdown.wait(timeout: .now() + .seconds(2))
            }
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let waiterReady = DispatchSemaphore(value: 0)
        let waiterFinished = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            do { try connection.waitForControlLoss(onReady: { waiterReady.signal() }) } catch {}
            waiterFinished.signal()
        }
        XCTAssertEqual(waiterReady.wait(timeout: .now() + .seconds(1)), .success)

        let event = try LiveHarnessEvent(
            kind: .activation,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID,
            activeStreamKey: harnessStreamKey
        )
        let sendFinished = DispatchSemaphore(value: 0)
        let sendResult = HarnessLivenessResult()
        DispatchQueue.global().async {
            do {
                try connection.send(event: event)
            } catch let error as LiveHarnessProtocolError {
                sendResult.set(error)
            } catch {
                sendResult.set(.invalidMessage("unexpected send error"))
            }
            sendFinished.signal()
        }
        XCTAssertEqual(sendPaused.wait(timeout: .now() + .seconds(1)), .success)

        let shutdownFinished = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            connection.requestShutdown()
            shutdownFinished.signal()
        }
        XCTAssertEqual(shutdownPaused.wait(timeout: .now() + .seconds(1)), .success)

        // Shutdown has retired the key, but its Darwin syscall is still held
        // behind the test hook.  Releasing the writer must therefore fail at
        // the in-lock admission guard without touching the socket.
        releaseSend.signal()
        XCTAssertEqual(sendFinished.wait(timeout: .now() + .seconds(1)), .success)
        guard let protocolError = sendResult.result else {
            return XCTFail("send did not report a protocol error")
        }
        XCTAssertEqual(protocolError, .controlLost)
        var received: UInt8 = 0
        let count = Darwin.recv(descriptors[1], &received, 1, Int32(MSG_DONTWAIT))
        let receiveError = errno
        XCTAssertEqual(count, -1)
        XCTAssertTrue(receiveError == EAGAIN || receiveError == EWOULDBLOCK)

        // Only now let the shutdown owner issue the kernel wake; the waiter
        // then returns and remains the sole owner allowed to complete close.
        releaseShutdown.signal()
        XCTAssertEqual(shutdownFinished.wait(timeout: .now() + .seconds(1)), .success)
        XCTAssertEqual(waiterFinished.wait(timeout: .now() + .seconds(1)), .success)
    }

    func testPeerControlLossRetiresKeyBeforePausedEventWrite() throws {
        var descriptors: [Int32] = [0, 0]
        XCTAssertEqual(socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors), 0)
        let sendPaused = DispatchSemaphore(value: 0)
        let releaseSend = DispatchSemaphore(value: 0)
        let controlLossRetired = DispatchSemaphore(value: 0)
        let releaseControlLoss = DispatchSemaphore(value: 0)
        let connection = try LiveHarnessControlConnection(
            descriptor: descriptors[0],
            readTimeoutNanoseconds: 5_000_000,
            writeTimeoutNanoseconds: 500_000_000,
            beforeWriteAdmission: {
                sendPaused.signal()
                _ = releaseSend.wait(timeout: .now() + .seconds(2))
            },
            beforeShutdownSyscall: nil,
            afterControlLossRetired: {
                controlLossRetired.signal()
                _ = releaseControlLoss.wait(timeout: .now() + .seconds(2))
            }
        )
        defer {
            connection.close()
            if descriptors[1] >= 0 { _ = Darwin.close(descriptors[1]) }
        }

        let command = try LiveHarnessSessionCommand(
            sessionID: "session-1",
            streamKey: harnessStreamKey,
            gateway: "ws://127.0.0.1",
            launchNonce: "nonce-1"
        )
        let commandWire = try command.framed()
        commandWire.withUnsafeBytes { raw in
            _ = Darwin.send(descriptors[1], raw.baseAddress!, commandWire.count, 0)
        }
        XCTAssertEqual(try connection.receiveOneCommand(), command)

        let waiterReady = DispatchSemaphore(value: 0)
        let waiterFinished = DispatchSemaphore(value: 0)
        let waiterResult = HarnessLivenessResult()
        DispatchQueue.global().async {
            do {
                try connection.waitForControlLoss(onReady: { waiterReady.signal() })
            } catch let error as LiveHarnessProtocolError {
                waiterResult.set(error)
            } catch {
                waiterResult.set(.controlLost)
            }
            waiterFinished.signal()
        }
        XCTAssertEqual(waiterReady.wait(timeout: .now() + .seconds(1)), .success)

        let event = try LiveHarnessEvent(
            kind: .activation,
            attemptID: harnessAttemptID,
            launchNonce: "nonce-1",
            sessionID: "session-1",
            generation: 1,
            requestedEngine: .processTap,
            resolvedEngine: .processTap,
            actualEngine: .processTap,
            sourceObjectID: harnessSourceObjectID,
            observerTokenID: harnessObserverID,
            activeStreamKey: harnessStreamKey
        )
        let sendFinished = DispatchSemaphore(value: 0)
        let sendResult = HarnessLivenessResult()
        DispatchQueue.global().async {
            do {
                try connection.send(event: event)
            } catch let error as LiveHarnessProtocolError {
                sendResult.set(error)
            } catch {
                sendResult.set(.invalidMessage("unexpected send error"))
            }
            sendFinished.signal()
        }
        XCTAssertEqual(sendPaused.wait(timeout: .now() + .seconds(1)), .success)

        // A post-command byte is a real peer-control loss.  The waiter
        // retires the key and pauses before unwinding, leaving the peer
        // socket open so the send's zero-byte boundary is observable.
        var trailing: UInt8 = 0x7f
        XCTAssertEqual(Darwin.send(descriptors[1], &trailing, 1, 0), 1)
        XCTAssertEqual(controlLossRetired.wait(timeout: .now() + .seconds(1)), .success)

        releaseSend.signal()
        XCTAssertEqual(sendFinished.wait(timeout: .now() + .seconds(1)), .success)
        XCTAssertEqual(sendResult.result, .controlLost)
        var received: UInt8 = 0
        let count = Darwin.recv(descriptors[1], &received, 1, Int32(MSG_DONTWAIT))
        let receiveError = errno
        XCTAssertEqual(count, -1)
        XCTAssertTrue(receiveError == EAGAIN || receiveError == EWOULDBLOCK)

        releaseControlLoss.signal()
        XCTAssertEqual(waiterFinished.wait(timeout: .now() + .seconds(1)), .success)
        XCTAssertEqual(waiterResult.result, .duplicateCommand)
    }

    @MainActor
    func testHarnessStreamKeyRetiredOnEngineMismatch() async throws {
        let wrongSource = HarnessSource(
            configuration: try harnessConfiguration(),
            engineIdentity: .screenCaptureKit
        )
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in wrongSource },
            harnessMode: true
        )
        defer {
            Task { @MainActor in await controller.stop() }
        }
        await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
        XCTAssertFalse(controller._testOnlyHasHarnessStreamKey)
        guard case .error(let message) = controller.state else {
            return XCTFail("identity mismatch must be terminal")
        }
        XCTAssertTrue(message.contains("engine mismatch"))
        await controller.stop()
    }

    @MainActor
    func testHarnessStreamKeyRetiredOnSourceStartFailure() async throws {
        struct MockFailure: Error {}
        let failingSource = HarnessSource(
            configuration: try harnessConfiguration(),
            engineIdentity: .processTap,
            startError: MockFailure()
        )
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in failingSource },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )
        defer {
            Task { @MainActor in await controller.stop() }
        }
        await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
        XCTAssertFalse(controller._testOnlyHasHarnessStreamKey)
        guard case .error = controller.state else {
            return XCTFail("thrown source start must produce error state")
        }
        await controller.stop()
    }

    @MainActor
    func testCanceledStartWhileSuspendedRetiresKeyStopsResourcesAndLeavesIdle() async throws {
        let gate = HarnessStartGate()
        let slowSource = HarnessSource(
            configuration: try harnessConfiguration(),
            engineIdentity: .processTap,
            startGate: gate
        )
        let transport = HarnessTransport()
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in transport },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in slowSource },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )

        let task1 = Task {
            await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
        }
        defer {
            gate.release()
            Task { @MainActor in await controller.stop() }
        }
        await gate.waitUntilEntered()

        XCTAssertTrue(controller._testOnlyHasHarnessStreamKey)

        // Cancel task1 while source is suspended on gate; DO NOT call controller.stop()
        task1.cancel()

        // Release gate so non-cooperative source completes
        gate.release()
        await task1.value

        XCTAssertEqual(controller.state, .idle, "canceled start must leave state in idle")
        XCTAssertNil(controller.activeSessionID, "active session ID must be cleared")
        XCTAssertFalse(controller._testOnlyHasHarnessStreamKey, "harness stream key must be retired")
        XCTAssertEqual(slowSource.startCount, 1, "source start must have been called once")
        XCTAssertEqual(slowSource.stopCount, 1, "source must be stopped exactly once")
        XCTAssertEqual(slowSource.observerCount, 0, "observer must be removed from source")
        XCTAssertGreaterThanOrEqual(transport.cancelCount, 1, "transport must be canceled when sink stops")
        let activationEvents = events.all.filter { $0.kind == .activation }
        XCTAssertTrue(activationEvents.isEmpty, "canceled start must never emit activation")
        let grantingHealth = events.all.filter { event in
            guard let status = event.status else { return false }
            switch status {
            case .idle:
                return false
            case .ready(let health), .running(let health), .stopped(let health):
                return health.permission == .granted
            case .failed(let permission, _):
                return permission == .granted
            }
        }
        XCTAssertTrue(grantingHealth.isEmpty, "canceled start must never emit granting health")
    }

    @MainActor
    func testCanceledStartAfterPriorCleanupAwaitIdlesAndRetiresKey() async throws {
        let stopGate = HarnessStartGate()
        let source = HarnessSource(
            configuration: try harnessConfiguration(),
            engineIdentity: .processTap,
            stopGate: stopGate
        )
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in source },
            harnessMode: true
        )
        defer {
            stopGate.release()
            Task { @MainActor in await controller.stop() }
        }

        await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
        for _ in 0..<10 { await Task.yield() }
        XCTAssertEqual(controller.state, .capturing)

        source.emit(.failed(SystemAudioCaptureMonitor.permissionDeniedMessage), generation: 1)
        await stopGate.waitUntilEntered()

        let task2 = Task {
            await controller.start(sessionID: "session-2", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
        }
        for _ in 0..<50 {
            if controller.state == .connecting { break }
            await Task.yield()
        }
        XCTAssertEqual(controller.state, .connecting, "second start must reach connecting while prior cleanup is suspended")

        task2.cancel()
        stopGate.release()
        await task2.value

        XCTAssertEqual(controller.state, .idle, "canceled start after cleanup await must leave idle")
        XCTAssertFalse(controller._testOnlyHasHarnessStreamKey, "harness stream key must be retired")
        XCTAssertNil(controller.activeSessionID)
    }

    @MainActor
    func testHarnessStreamKeyRetiredOnCancellationAndDoesNotRetireNewerAttempt() async throws {
        let gate = HarnessStartGate()
        let slowSource = HarnessSource(
            configuration: try harnessConfiguration(),
            engineIdentity: .processTap,
            startGate: gate
        )
        let fastSource = HarnessSource(
            configuration: try harnessConfiguration(),
            engineIdentity: .processTap
        )
        var sourceIndex = 0
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in
                defer { sourceIndex += 1 }
                return sourceIndex == 0 ? slowSource : fastSource
            },
            harnessMode: true
        )

        let task1 = Task {
            await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
        }
        defer {
            gate.release()
            Task { @MainActor in await controller.stop() }
        }
        await gate.waitUntilEntered()

        // Cancel suspended attempt 1, then reset state to idle to allow attempt 2
        task1.cancel()
        await controller.stop()
        XCTAssertEqual(controller.state, .idle)
        XCTAssertFalse(controller._testOnlyHasHarnessStreamKey)

        // Genuine successful attempt 2
        await controller.start(sessionID: "session-2", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
        for _ in 0..<10 { await Task.yield() }

        XCTAssertEqual(fastSource.startCount, 1)
        XCTAssertEqual(controller.state, .capturing)
        XCTAssertEqual(controller.activeSessionID, "session-2")
        XCTAssertTrue(controller._testOnlyHasHarnessStreamKey)

        // Release attempt 1 and ensure stale completion does not clear attempt 2's key, disrupt capture, or stop attempt 2's source
        gate.release()
        await task1.value

        XCTAssertEqual(controller.state, .capturing)
        XCTAssertEqual(controller.activeSessionID, "session-2")
        XCTAssertTrue(controller._testOnlyHasHarnessStreamKey)
        XCTAssertEqual(fastSource.stopCount, 0, "attempt 2 source must not be stopped by stale attempt 1")

        await controller.stop()
        XCTAssertEqual(controller.state, .idle)
        XCTAssertFalse(controller._testOnlyHasHarnessStreamKey)
    }

    @MainActor
    func testHarnessStreamKeyRetiredOnRuntimeTerminalFailure() async throws {
        let source = HarnessSource(
            configuration: try harnessConfiguration(),
            engineIdentity: .processTap
        )
        let events = HarnessEvents()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in source },
            harnessMode: true,
            harnessObserver: { events.append($0) }
        )
        defer {
            Task { @MainActor in await controller.stop() }
        }
        await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
        for _ in 0..<10 { await Task.yield() }
        XCTAssertTrue(controller._testOnlyHasHarnessStreamKey)

        source.emit(.failed(SystemAudioCaptureMonitor.permissionDeniedMessage), generation: 1)
        for _ in 0..<10 { await Task.yield() }

        XCTAssertFalse(controller._testOnlyHasHarnessStreamKey)
        guard case .error(let msg) = controller.state else {
            return XCTFail("terminal failure must set error state")
        }
        XCTAssertEqual(msg, SystemAudioCaptureMonitor.permissionDeniedMessage)
        let healthEvents = events.all.filter { $0.kind == .health }
        XCTAssertFalse(healthEvents.isEmpty)
        await controller.stop()
    }

    @MainActor
    func testHarnessStreamKeyRetainedDuringSuccessfulCaptureUntilStop() async throws {
        let source = HarnessSource(
            configuration: try harnessConfiguration(),
            engineIdentity: .processTap
        )
        let controller = CompanionSessionController(
            transportFactory: { _, _ in HarnessTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, _, _ in source },
            harnessMode: true
        )
        defer {
            Task { @MainActor in await controller.stop() }
        }
        await controller.start(sessionID: "session-1", streamKey: harnessStreamKey, gatewayBase: "ws://127.0.0.1")
        for _ in 0..<10 { await Task.yield() }
        XCTAssertTrue(controller._testOnlyHasHarnessStreamKey)
        await controller.stop()
        XCTAssertFalse(controller._testOnlyHasHarnessStreamKey)
    }
}
