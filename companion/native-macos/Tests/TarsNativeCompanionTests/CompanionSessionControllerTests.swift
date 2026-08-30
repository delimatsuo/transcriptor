import CoreAudio
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
    private let startError: Error?
    private let lock = NSLock()
    private(set) var startCount = 0
    private(set) var stopCount = 0
    private(set) var observerRemovalCount = 0
    private var observers: [CaptureSourceObserverToken: CaptureSourceHealthObserver] = [:]

    init(configuration: CaptureSourceConfiguration, shouldFailStart: Bool = false, startError: Error? = nil) {
        self.configuration = configuration
        self.shouldFailStart = shouldFailStart
        self.startError = startError
    }

    func start() async throws {
        lock.withLock { startCount += 1 }
        if let startError { throw startError }
        if shouldFailStart {
            throw CompanionError.invalid("mock source start failure")
        }
        status = .running(SourceHealth(permission: .granted, route: .healthy))
        notifyObservers()
    }

    func stop() async {
        lock.withLock { stopCount += 1 }
        status = .stopped(SourceHealth(permission: .granted, route: .unknown))
        notifyObservers()
    }

    func installHealthObserver(_ observer: @escaping CaptureSourceHealthObserver) -> CaptureSourceObserverToken {
        let token = CaptureSourceObserverToken()
        let update = lock.withLock { () -> CaptureSourceHealthUpdate in
            observers[token] = observer
            return CaptureSourceHealthUpdate(source: source, generation: configuration.identity.captureGeneration, status: status)
        }
        observer(update)
        return token
    }

    func removeHealthObserver(_ token: CaptureSourceObserverToken) {
        lock.withLock {
            if observers.removeValue(forKey: token) != nil { observerRemovalCount += 1 }
        }
    }

    func emit(_ status: CaptureSourceStatus, generation: UInt64? = nil) {
        self.status = status
        let update = CaptureSourceHealthUpdate(
            source: source,
            generation: generation ?? configuration.identity.captureGeneration,
            status: status
        )
        let callbacks = lock.withLock { Array(observers.values) }
        callbacks.forEach { $0(update) }
    }

    private func notifyObservers() {
        let update = CaptureSourceHealthUpdate(
            source: source,
            generation: configuration.identity.captureGeneration,
            status: status
        )
        let callbacks = lock.withLock { Array(observers.values) }
        callbacks.forEach { $0(update) }
    }
}

@available(macOS 14.2, *)
private final class ControllerProcessTapHAL: ProcessTapHALBoundary, @unchecked Sendable {
    let currentProcessID: Int32 = 4242

    private let lock = NSLock()
    private var nextHandle: UInt32 = 100
    private var nextToken: UInt64 = 1
    private var startupEvent: ProcessTapHALEvent?
    var rawPermissionErrorAtTapUID = false
    var rawDeviceAliveReadStatus: OSStatus?
    private var handlers: [UInt64: @Sendable (ProcessTapHALEvent) -> Void] = [:]
    private var handlerKinds: [UInt64: ProcessTapHALListenerKind] = [:]
    private(set) var startCount = 0
    private(set) var stopCount = 0

    init(startupEvent: ProcessTapHALEvent? = nil) {
        self.startupEvent = startupEvent
    }

    func translatePIDToProcessObject(pid: Int32) throws -> UInt32 { 77 }

    func createProcessTap(description: ProcessTapDescription) throws -> UInt32 {
        lock.withLock {
            let handle = nextHandle
            nextHandle += 1
            return handle
        }
    }

    func tapUID(tapID: UInt32) throws -> String {
        if rawPermissionErrorAtTapUID {
            throw CoreAudioProcessTapHAL.normalizedError(
                operation: "kAudioTapPropertyUID",
                status: kAudioDevicePermissionsError
            )
        }
        return "controller-tap-\(tapID)"
    }

    func readTapFormat(tapID: UInt32) throws -> ProcessTapPCMDescriptor {
        ProcessTapPCMDescriptor(
            sampleRate: 48_000,
            channels: 1,
            isFloat: true,
            isInterleaved: true,
            bitsPerChannel: 32,
            bytesPerFrame: 4
        )
    }

    func createAggregate(tapID: UInt32, tapUID: String, uid: String, name: String) throws -> UInt32 {
        lock.withLock {
            let handle = nextHandle
            nextHandle += 1
            return handle
        }
    }

    func createIOProc(aggregateID: UInt32, ringAddress: UInt64) throws -> UInt64 { 501 }

    func start(aggregateID: UInt32, ioProc: UInt64) throws {
        lock.withLock { startCount += 1 }
    }

    func stop(aggregateID: UInt32, ioProc: UInt64) throws {
        lock.withLock { stopCount += 1 }
    }

    func destroyIOProc(aggregateID: UInt32, ioProc: UInt64) throws {}
    func detachTap(tapID: UInt32, aggregateID: UInt32) throws {}
    func destroyAggregate(aggregateID: UInt32) throws {}
    func destroyProcessTap(tapID: UInt32) throws {}

    func installListener(
        kind: ProcessTapHALListenerKind,
        aggregateID: UInt32?,
        tapID: UInt32?,
        handler: @escaping @Sendable (ProcessTapHALEvent) -> Void
    ) throws -> UInt64 {
        let (token, eventToEmit): (UInt64, ProcessTapHALEvent?) = lock.withLock {
            let token = nextToken
            nextToken += 1
            handlers[token] = handler
            handlerKinds[token] = kind
            guard kind == .tapFormat, let startupEvent else { return (token, nil) }
            self.startupEvent = nil
            return (token, startupEvent)
        }
        if let eventToEmit { handler(eventToEmit) }
        return token
    }

    func removeListener(_ token: UInt64) throws {
        lock.withLock {
            handlers.removeValue(forKey: token)
            handlerKinds.removeValue(forKey: token)
        }
    }

    func emit(_ event: ProcessTapHALEvent) {
        let target: ProcessTapHALListenerKind
        switch event {
        case .sleep, .wake: target = .sleepWake
        case .serviceReset: target = .serviceReset
        case .tapListChanged: target = .tapList
        case .deviceAlive, .deviceAliveReadFailed: target = .deviceAlive
        case .tapFormatChanged: target = .tapFormat
        }
        let callbacks = lock.withLock {
            handlers.compactMap { token, handler in handlerKinds[token] == target ? handler : nil }
        }
        callbacks.forEach { $0(event) }
    }

    func emitLiveDeviceAliveRead(value: UInt32 = 1) {
        let status = rawDeviceAliveReadStatus ?? noErr
        emit(CoreAudioProcessTapHAL.deviceAliveEvent(status: status, value: value))
    }
}

@MainActor
private func waitForState(
    controller: CompanionSessionController,
    condition: (CompanionState) -> Bool,
    timeoutMs: Int = 1000
) async -> Bool {
    // This is a bounded scheduler-yield harness, not timing evidence.  The
    // caller's timeout remains a failure budget while no sleep is used to
    // establish a lifecycle ordering.
    for _ in 0..<max(1, timeoutMs) {
        if condition(controller.state) {
            return true
        }
        await Task.yield()
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

    func testDeniedSourceStartPreservesExactPermissionCopyInVisibleErrorState() async throws {
        let failingSource = FakeCaptureSource(
            configuration: try dummyConfig(),
            startError: SystemAudioCaptureFailure.denied
        )
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            sourceFactory: { _, _ in failingSource }
        )

        await controller.start(sessionID: "sess-denied-start", streamKey: "key", gatewayBase: "ws://127.0.0.1:8000/api")

        XCTAssertEqual(
            controller.systemAudioStatus,
            .failed(SystemAudioCaptureMonitor.permissionDeniedMessage)
        )
        XCTAssertEqual(controller.systemAudioHealth.permission, .denied)
        guard case .error(let message) = controller.state else {
            return XCTFail("a typed permission denial must be surfaced as an error")
        }
        XCTAssertEqual(
            message,
            SystemAudioCaptureMonitor.permissionDeniedMessage
        )
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

    func testInvalidLaunchArgumentStopsBeforeCreatingSource() async throws {
        var sourceFactoryCount = 0
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            sourceFactory: { config, _ in
                sourceFactoryCount += 1
                return FakeCaptureSource(configuration: config)
            },
            launchArgumentError: "Valor inválido para --system-audio-engine"
        )
        await controller.start(sessionID: "bad-arg", streamKey: "key", gatewayBase: "ws://127.0.0.1:8000/api")
        XCTAssertEqual(sourceFactoryCount, 0)
        guard case .error(let message) = controller.state else {
            return XCTFail("invalid launch arguments must be surfaced as an app error")
        }
        XCTAssertTrue(message.contains("system-audio-engine"))
    }

    func testControllerAcceptsMonotonicRebuildGenerationAndIgnoresLateOlderUpdate() async throws {
        let fakeSource = FakeCaptureSource(configuration: try dummyConfig())
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            sourceFactory: { _, _ in fakeSource }
        )
        await controller.start(sessionID: "health-session", streamKey: "key", gatewayBase: "ws://127.0.0.1:8000/api")
        _ = await waitForState(controller: controller, condition: { $0 == .capturing })
        fakeSource.emit(.running(SourceHealth(permission: .granted, route: .healthy)), generation: 2)
        await Task.yield()
        XCTAssertEqual(controller.systemAudioStatus, .running(SourceHealth(permission: .granted, route: .healthy)))
        XCTAssertEqual(fakeSource.configuration.identity.captureGeneration, 1)
        fakeSource.emit(.failed("late old graph"), generation: 1)
        await Task.yield()
        XCTAssertEqual(controller.systemAudioStatus, .running(SourceHealth(permission: .granted, route: .healthy)))
        await controller.stop()
        XCTAssertEqual(fakeSource.observerRemovalCount, 1)
    }

    func testInjectedScreenCaptureKitFactoryDoesNotInvokeCoreGraphicsPreflight() async throws {
        let fakeSource = FakeCaptureSource(configuration: try dummyConfig())
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            enginePreference: .screenCaptureKit,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 13, minorVersion: 0, patchVersion: 0),
            engineSourceFactory: { engine, _, _ in
                XCTAssertEqual(engine, .screenCaptureKit)
                return fakeSource
            }
        )
        await controller.start(sessionID: "injected-sck", streamKey: "key", gatewayBase: "ws://127.0.0.1:8000/api")
        XCTAssertEqual(controller.resolvedSystemAudioEngine, .screenCaptureKit)
        XCTAssertEqual(controller.state, .capturing)
        await controller.stop()
    }

    @available(macOS 14.2, *)
    func testProcessTapStartupHALEventDoesNotAdvertiseFalseActiveSession() async throws {
        let hal = ControllerProcessTapHAL(startupEvent: .tapFormatChanged)
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, configuration, sink in
                ProcessTapSystemAudioSource(configuration: configuration, sink: sink, hal: hal)
            }
        )

        await controller.start(
            sessionID: "startup-hal-event",
            streamKey: "key",
            gatewayBase: "ws://127.0.0.1:8000/api"
        )

        guard case .error = controller.state else {
            return XCTFail("a HAL event during startup must fail loudly instead of advertising capture")
        }
        XCTAssertNil(controller.activeSessionID)
        XCTAssertNotEqual(controller.state, .capturing)
        XCTAssertEqual(hal.startCount, 0, "AudioDeviceStart must not run after startup ownership was aborted")
        await controller.stop()
    }

    @available(macOS 14.2, *)
    func testProcessTapPreStartRawPermissionFailurePreservesExactControllerError() async throws {
        let hal = ControllerProcessTapHAL()
        hal.rawPermissionErrorAtTapUID = true
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, configuration, sink in
                ProcessTapSystemAudioSource(configuration: configuration, sink: sink, hal: hal)
            }
        )

        await controller.start(
            sessionID: "pre-start-permission",
            streamKey: "key",
            gatewayBase: "ws://127.0.0.1:8000/api"
        )

        XCTAssertNil(controller.activeSessionID)
        XCTAssertEqual(controller.systemAudioHealth.permission, .denied)
        XCTAssertEqual(
            controller.systemAudioStatus,
            .failed(SystemAudioCaptureMonitor.permissionDeniedMessage)
        )
        guard case .error(let message) = controller.state else {
            return XCTFail("a pre-start Core Audio denial must be visible as an error")
        }
        XCTAssertEqual(message, SystemAudioCaptureMonitor.permissionDeniedMessage)
        await controller.stop()
    }

    @available(macOS 14.2, *)
    func testProcessTapLiveDeviceAlivePermissionReadMapsToDeniedWithoutRebuild() async throws {
        let hal = ControllerProcessTapHAL()
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            enginePreference: .processTap,
            operatingSystemVersion: OperatingSystemVersion(majorVersion: 14, minorVersion: 4, patchVersion: 0),
            engineSourceFactory: { _, configuration, sink in
                ProcessTapSystemAudioSource(configuration: configuration, sink: sink, hal: hal)
            }
        )

        await controller.start(
            sessionID: "live-device-permission",
            streamKey: "key",
            gatewayBase: "ws://127.0.0.1:8000/api"
        )
        let reachedCapturing = await waitForState(controller: controller, condition: { $0 == .capturing })
        XCTAssertTrue(reachedCapturing)
        XCTAssertEqual(hal.startCount, 1)

        // Inject the raw status at the live device-alive listener boundary.
        // The source must publish the exact denial and tear down its one
        // graph, never entering the generic route-rebuild path.
        hal.rawDeviceAliveReadStatus = kAudioDevicePermissionsError
        hal.emitLiveDeviceAliveRead()

        let reachedError = await waitForState(controller: controller, condition: {
            if case .error = $0 { return true }
            return false
        })
        XCTAssertTrue(reachedError, "a live permission-bearing liveness read must become a visible error")
        XCTAssertNil(controller.activeSessionID)
        XCTAssertEqual(controller.systemAudioHealth.permission, .denied)
        XCTAssertEqual(
            controller.systemAudioStatus,
            .failed(SystemAudioCaptureMonitor.permissionDeniedMessage)
        )
        guard case .error(let message) = controller.state else {
            return XCTFail("expected the exact denial error state")
        }
        XCTAssertEqual(message, SystemAudioCaptureMonitor.permissionDeniedMessage)
        XCTAssertEqual(hal.startCount, 1, "permission denial must not consume a rebuild or fallback")

        await controller.stop()
        XCTAssertEqual(hal.stopCount, 1)
    }

    func testExplicitProcessTapPermissionFailurePublishesDeniedHealth() async throws {
        let fakeSource = FakeCaptureSource(configuration: try dummyConfig())
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            sourceFactory: { _, _ in fakeSource }
        )
        await controller.start(sessionID: "health-denied", streamKey: "key", gatewayBase: "ws://127.0.0.1:8000/api")
        _ = await waitForState(controller: controller, condition: { $0 == .capturing })
        fakeSource.emit(.failed(SystemAudioCaptureMonitor.permissionDeniedMessage))
        await Task.yield()
        XCTAssertEqual(controller.systemAudioHealth.permission, .denied)
        guard case .error(let message) = controller.state else {
            return XCTFail("an accepted terminal denial must leave Capturando")
        }
        XCTAssertEqual(message, SystemAudioCaptureMonitor.permissionDeniedMessage)

        await controller.stop()
        XCTAssertEqual(fakeSource.observerRemovalCount, 1)
    }

    func testNonPermissionFailureDoesNotFabricateDeniedHealth() async throws {
        let fakeSource = FakeCaptureSource(configuration: try dummyConfig())
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            sourceFactory: { _, _ in fakeSource }
        )
        await controller.start(sessionID: "health-route-failure", streamKey: "key", gatewayBase: "ws://127.0.0.1:8000/api")
        _ = await waitForState(controller: controller, condition: { $0 == .capturing })
        fakeSource.emit(.failed("Rota de áudio indisponível"))
        await Task.yield()
        XCTAssertNotEqual(controller.systemAudioHealth.permission, .denied)
        guard case .error(let message) = controller.state else {
            return XCTFail("an accepted non-permission terminal failure must leave Capturando")
        }
        XCTAssertEqual(message, "Rota de áudio indisponível")
        await controller.stop()
    }

    func testTerminalSourceFailureCleansOldOwnersBeforeImmediateRetry() async throws {
        let firstSource = FakeCaptureSource(configuration: try dummyConfig())
        let secondSource = FakeCaptureSource(configuration: try dummyConfig())
        var sourceIndex = 0
        let controller = CompanionSessionController(
            transportFactory: { _, _ in FakeTransport() },
            sourceFactory: { _, _ in
                defer { sourceIndex += 1 }
                return sourceIndex == 0 ? firstSource : secondSource
            }
        )

        await controller.start(sessionID: "old-session", streamKey: "old-key", gatewayBase: "ws://127.0.0.1:8000/api")
        let initiallyCapturing = await waitForState(controller: controller, condition: { $0 == .capturing })
        XCTAssertTrue(initiallyCapturing)

        firstSource.emit(.failed("falha terminal da rota"))
        let terminalFailureVisible = await waitForState(controller: controller, condition: {
            if case .error = $0 { return true }
            return false
        })
        XCTAssertTrue(terminalFailureVisible)

        // Starting immediately must join the terminal cleanup owner.  The
        // old observer/source/sink therefore cannot race the second graph.
        await controller.start(sessionID: "new-session", streamKey: "new-key", gatewayBase: "ws://127.0.0.1:8000/api")
        let retryCapturing = await waitForState(controller: controller, condition: { $0 == .capturing })
        XCTAssertTrue(retryCapturing)
        XCTAssertEqual(firstSource.stopCount, 1)
        XCTAssertEqual(firstSource.observerRemovalCount, 1)
        XCTAssertEqual(secondSource.startCount, 1)
        XCTAssertEqual(controller.activeSessionID, "new-session")

        firstSource.emit(.failed("late old failure"))
        await Task.yield()
        XCTAssertEqual(controller.state, .capturing, "late updates from the retired source must be fenced")

        await controller.stop()
        XCTAssertEqual(firstSource.stopCount, 1)
        XCTAssertEqual(firstSource.observerRemovalCount, 1)
        XCTAssertEqual(secondSource.stopCount, 1)
        XCTAssertEqual(secondSource.observerRemovalCount, 1)
    }
}
