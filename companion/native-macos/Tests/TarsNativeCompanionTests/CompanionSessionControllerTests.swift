import XCTest
@testable import TarsNativeCompanion

// MARK: - Fakes for isolated controller testing

private final class FakeTransport: AudioStreamTransport, @unchecked Sendable {
    private let shouldFailConnect: Bool
    private let lock = NSLock()
    private(set) var connectCount = 0
    private(set) var cancelCount = 0

    init(shouldFailConnect: Bool = false) {
        self.shouldFailConnect = shouldFailConnect
    }

    func connect() async throws {
        lock.withLock { connectCount += 1 }
        if shouldFailConnect {
            throw CompanionError.invalid("mock transport connect failure")
        }
    }

    func send(_ data: Data) async throws {}
    func sendText(_ text: String) async throws {}
    func cancel() {
        lock.withLock { cancelCount += 1 }
    }
}

private final class FakeCaptureSource: CaptureSource, @unchecked Sendable {
    let source: AudioSource = .systemAudio
    let configuration: CaptureSourceConfiguration
    var status: CaptureSourceStatus = .idle

    private let shouldFailStart: Bool
    private let lock = NSLock()
    private(set) var startCount = 0
    private(set) var stopCount = 0

    init(configuration: CaptureSourceConfiguration, shouldFailStart: Bool = false) {
        self.configuration = configuration
        self.shouldFailStart = shouldFailStart
    }

    func start() async throws {
        lock.withLock { startCount += 1 }
        if shouldFailStart {
            throw CompanionError.invalid("mock source start failure")
        }
        status = .running(SourceHealth(permission: .granted, route: .healthy))
    }

    func stop() async {
        lock.withLock { stopCount += 1 }
        status = .stopped(SourceHealth(permission: .granted, route: .unknown))
    }
}

private final class ControllableCaptureSource: CaptureSource, @unchecked Sendable {
    let source: AudioSource = .systemAudio
    let configuration: CaptureSourceConfiguration

    private let lock = NSLock()
    private let shouldThrowAfterRelease: Bool
    private var _status: CaptureSourceStatus = .idle
    private var releaseContinuations: [CheckedContinuation<Void, Never>] = []
    private var hasEnteredStart = false
    private var isReleased = false
    private var hasFinishedStart = false
    private var _startCount = 0
    private var _stopCount = 0
    private var _postStartStopCount = 0

    var startCount: Int {
        lock.withLock { _startCount }
    }

    var stopCount: Int {
        lock.withLock { _stopCount }
    }

    var postStartStopCount: Int {
        lock.withLock { _postStartStopCount }
    }

    var status: CaptureSourceStatus {
        lock.withLock { _status }
    }

    init(
        configuration: CaptureSourceConfiguration,
        shouldThrowAfterRelease: Bool = false
    ) {
        self.configuration = configuration
        self.shouldThrowAfterRelease = shouldThrowAfterRelease
    }

    func waitForStartToEnter(timeoutMs: Int = 1_000) async -> Bool {
        let deadline = Date().addingTimeInterval(Double(timeoutMs) / 1_000.0)
        while Date() < deadline {
            if lock.withLock({ hasEnteredStart }) {
                return true
            }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        return lock.withLock { hasEnteredStart }
    }

    func releaseStart() {
        let waiters = lock.withLock { () -> [CheckedContinuation<Void, Never>] in
            isReleased = true
            let current = releaseContinuations
            releaseContinuations.removeAll()
            return current
        }
        waiters.forEach { $0.resume() }
    }

    func start() async throws {
        lock.withLock {
            _startCount += 1
            hasEnteredStart = true
        }

        // Suspend until explicitly released, intentionally ignoring Task cancellation.
        await withCheckedContinuation { continuation in
            let released = lock.withLock { () -> Bool in
                if isReleased {
                    return true
                }
                releaseContinuations.append(continuation)
                return false
            }
            if released {
                continuation.resume()
            }
        }

        if shouldThrowAfterRelease {
            throw CompanionError.invalid("late mock source start failure")
        }

        // Intentionally ignores Task cancellation and becomes running only after release.
        lock.withLock {
            hasFinishedStart = true
            _status = .running(SourceHealth(permission: .granted, route: .healthy))
        }
    }

    func stop() async {
        lock.withLock {
            _stopCount += 1
            if hasFinishedStart {
                _postStartStopCount += 1
            }
            _status = .stopped(SourceHealth(permission: .granted, route: .unknown))
        }
    }
}

private final class AtomicFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var _value = false

    var value: Bool {
        get { lock.withLock { _value } }
        set { lock.withLock { _value = newValue } }
    }

    init(_ initial: Bool = false) {
        self._value = initial
    }
}

@MainActor
private func waitForState(
    controller: CompanionSessionController,
    condition: (CompanionState) -> Bool,
    timeoutMs: Int = 1000
) async -> Bool {
    let deadline = Date().addingTimeInterval(Double(timeoutMs) / 1000.0)
    while Date() < deadline {
        if condition(controller.state) {
            return true
        }
        try? await Task.sleep(nanoseconds: 10_000_000) // 10ms
    }
    return condition(controller.state)
}

private func dummyConfig() throws -> CaptureSourceConfiguration {
    let identity = try SourceIdentity(
        sessionID: "test-session",
        streamID: "system",
        captureGeneration: 1,
        source: .systemAudio,
        sampleRate: 16_000,
        channelCount: 1
    )
    return CaptureSourceConfiguration(identity: identity, deviceIdentity: "FakeSource")
}

@MainActor
final class CompanionSessionControllerTests: XCTestCase {

    // (a) start → state becomes .capturing, activeSessionID set, fake source started
    func testStartBecomesCapturing() async throws {
        let fakeSource = FakeCaptureSource(configuration: try dummyConfig())
        var sourceCreatedCount = 0
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            sourceFactory: { config, _ in
                sourceCreatedCount += 1
                return fakeSource
            }
        )

        await controller.start(sessionID: "sess-1", streamKey: "key-1", gatewayBase: "ws://127.0.0.1:8000/api")

        let reached = await waitForState(controller: controller, condition: { $0 == .capturing })
        XCTAssertTrue(reached)
        XCTAssertEqual(controller.state, .capturing)
        XCTAssertEqual(controller.activeSessionID, "sess-1")
        XCTAssertEqual(fakeSource.startCount, 1)
        XCTAssertEqual(sourceCreatedCount, 1)
    }

    // (b) transport that fails every connect → state .reconnecting (not error)
    func testFailingTransportBecomesReconnecting() async throws {
        let fakeSource = FakeCaptureSource(configuration: try dummyConfig())
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport(shouldFailConnect: true) },
            sourceFactory: { config, _ in fakeSource }
        )

        await controller.start(sessionID: "sess-2", streamKey: "key-2", gatewayBase: "ws://127.0.0.1:8000/api")

        let reached = await waitForState(controller: controller, condition: { $0 == .reconnecting })
        XCTAssertTrue(reached)
        XCTAssertEqual(controller.state, .reconnecting)
        XCTAssertEqual(controller.activeSessionID, "sess-2")
        XCTAssertEqual(fakeSource.startCount, 1)
    }

    // (c) source whose start() throws → state .error containing "Falha ao iniciar", sink stopped
    func testSourceStartThrowResultsInErrorState() async throws {
        let failingSource = FakeCaptureSource(configuration: try dummyConfig(), shouldFailStart: true)
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            sourceFactory: { config, _ in failingSource }
        )

        await controller.start(sessionID: "sess-3", streamKey: "key-3", gatewayBase: "ws://127.0.0.1:8000/api")

        let state = controller.state
        if case .error(let message) = state {
            XCTAssertTrue(message.contains("Falha ao iniciar"), "Expected message to contain 'Falha ao iniciar', got: \(message)")
        } else {
            XCTFail("Expected .error state, got: \(state)")
        }
        XCTAssertNil(controller.activeSessionID)
    }

    // (d) stop from capturing → .idle, source stop called
    func testStopFromCapturingReturnsToIdle() async throws {
        let fakeSource = FakeCaptureSource(configuration: try dummyConfig())
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            sourceFactory: { config, _ in fakeSource }
        )

        await controller.start(sessionID: "sess-4", streamKey: "key-4", gatewayBase: "ws://127.0.0.1:8000/api")
        _ = await waitForState(controller: controller, condition: { $0 == .capturing })

        await controller.stop()

        XCTAssertEqual(controller.state, .idle)
        XCTAssertNil(controller.activeSessionID)
        XCTAssertEqual(fakeSource.stopCount, 1)
    }

    // (e) start ignored while capturing (second start doesn't re-create source — fake factory call count stays 1)
    func testStartIgnoredWhileCapturing() async throws {
        var sourceFactoryCount = 0
        let fakeSource = FakeCaptureSource(configuration: try dummyConfig())
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            sourceFactory: { config, _ in
                sourceFactoryCount += 1
                return fakeSource
            }
        )

        await controller.start(sessionID: "sess-5", streamKey: "key-5", gatewayBase: "ws://127.0.0.1:8000/api")
        _ = await waitForState(controller: controller, condition: { $0 == .capturing })
        XCTAssertEqual(sourceFactoryCount, 1)

        // Second start attempt while already capturing
        await controller.start(sessionID: "sess-5-dup", streamKey: "key-5-dup", gatewayBase: "ws://127.0.0.1:8000/api")

        XCTAssertEqual(sourceFactoryCount, 1, "Factory should not be called again")
        XCTAssertEqual(controller.activeSessionID, "sess-5")
        XCTAssertEqual(controller.state, .capturing)
    }

    // (f) error state message is surfaced
    func testErrorStateMessageIsSurfaced() async throws {
        let failingSource = FakeCaptureSource(configuration: try dummyConfig(), shouldFailStart: true)
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            sourceFactory: { config, _ in failingSource }
        )

        await controller.start(sessionID: "sess-err", streamKey: "key-err", gatewayBase: "ws://127.0.0.1:8000/api")

        let state = controller.state
        guard case .error(let message) = state else {
            XCTFail("Expected .error state, got \(state)")
            return
        }
        XCTAssertFalse(message.isEmpty, "Error message should not be empty")
        XCTAssertTrue(message.contains("Falha ao iniciar"), "Expected message to contain 'Falha ao iniciar', got: \(message)")
    }

    // (g) controller passes key only as subprotocol and builds keyless URL
    func testControllerPassesKeyOnlyAsSubprotocol() async throws {
        let fakeSource = FakeCaptureSource(configuration: try dummyConfig())
        final class CapturedState: @unchecked Sendable {
            var url: URL?
            var protocols: [String]?
        }
        let captured = CapturedState()
        let controller = CompanionSessionController(
            transportFactory: { url, protocols in
                captured.url = url
                captured.protocols = protocols
                return FakeTransport()
            },
            sourceFactory: { config, _ in fakeSource }
        )

        await controller.start(
            sessionID: "sess-proto",
            streamKey: "safe_stream_key-123",
            gatewayBase: "ws://127.0.0.1:8000/api/stream/native"
        )

        let reached = await waitForState(controller: controller, condition: { $0 == .capturing })
        XCTAssertTrue(reached)
        XCTAssertEqual(captured.url?.absoluteString, "ws://127.0.0.1:8000/api/stream/native/sess-proto")
        XCTAssertNil(captured.url?.query)
        XCTAssertEqual(captured.protocols, ["tars-stream", "safe_stream_key-123"])
    }

    func testStopAwaitsInFlightStartupAndEnsuresLateSourceIsStopped() async throws {
        let fakeSource = ControllableCaptureSource(configuration: try dummyConfig())
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            sourceFactory: { _, _ in fakeSource }
        )

        let startTask = Task { @MainActor in
            await controller.start(
                sessionID: "race-sess",
                streamKey: "race-key",
                gatewayBase: "ws://127.0.0.1:8000/api"
            )
        }

        let startEntered = await fakeSource.waitForStartToEnter()
        XCTAssertTrue(startEntered)

        let stopCompleted = AtomicFlag(false)
        let stopTask = Task { @MainActor in
            await controller.stop()
            stopCompleted.value = true
        }

        let stopBegunDeadline = Date().addingTimeInterval(1.0)
        while Date() < stopBegunDeadline && fakeSource.stopCount == 0 {
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTAssertGreaterThanOrEqual(fakeSource.stopCount, 1)
        XCTAssertFalse(
            stopCompleted.value,
            "controller.stop must not complete before suspended start is released"
        )

        fakeSource.releaseStart()
        await startTask.value
        await stopTask.value

        XCTAssertTrue(stopCompleted.value)
        XCTAssertEqual(controller.state, .idle)
        XCTAssertNil(controller.activeSessionID)
        XCTAssertEqual(
            fakeSource.status,
            .stopped(SourceHealth(permission: .granted, route: .unknown))
        )
        XCTAssertGreaterThanOrEqual(
            fakeSource.postStartStopCount,
            1,
            "a stop call must occur after start settles"
        )
    }

    func testInvalidatedGenerationCannotPublishStateOrOverwriteNewSession() async throws {
        let staleSource = ControllableCaptureSource(
            configuration: try dummyConfig(),
            shouldThrowAfterRelease: true
        )
        let replacementSource = ControllableCaptureSource(configuration: try dummyConfig())
        var sources = [staleSource, replacementSource]

        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            sourceFactory: { _, _ in
                sources.removeFirst()
            }
        )

        let staleStart = Task { @MainActor in
            await controller.start(
                sessionID: "stale-session",
                streamKey: "stale-key",
                gatewayBase: "ws://127.0.0.1:8000/api"
            )
        }
        let staleStartEntered = await staleSource.waitForStartToEnter()
        XCTAssertTrue(staleStartEntered)

        let staleStop = Task { @MainActor in
            await controller.stop()
        }

        let stopBegunDeadline = Date().addingTimeInterval(1.0)
        while Date() < stopBegunDeadline && staleSource.stopCount == 0 {
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTAssertGreaterThanOrEqual(staleSource.stopCount, 1)
        XCTAssertEqual(controller.state, .idle)

        replacementSource.releaseStart()
        await controller.start(
            sessionID: "replacement-session",
            streamKey: "replacement-key",
            gatewayBase: "ws://127.0.0.1:8000/api"
        )
        XCTAssertEqual(controller.state, .capturing)
        XCTAssertEqual(controller.activeSessionID, "replacement-session")
        XCTAssertEqual(replacementSource.stopCount, 0)

        staleSource.releaseStart()
        await staleStart.value
        await staleStop.value

        XCTAssertEqual(controller.state, .capturing)
        XCTAssertEqual(controller.activeSessionID, "replacement-session")
        XCTAssertEqual(replacementSource.stopCount, 0)
        XCTAssertEqual(
            replacementSource.status,
            .running(SourceHealth(permission: .granted, route: .healthy))
        )
    }
}
