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
            transportFactory: { _ in FakeTransport() },
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
            transportFactory: { _ in FakeTransport(shouldFailConnect: true) },
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
            transportFactory: { _ in FakeTransport() },
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
            transportFactory: { _ in FakeTransport() },
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
            transportFactory: { _ in FakeTransport() },
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
            transportFactory: { _ in FakeTransport() },
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
}
