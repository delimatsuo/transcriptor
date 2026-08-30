import Foundation
import CoreAudio
import TarsRealtimeAudioBridge
import XCTest
@testable import TarsNativeCompanion

@available(macOS 14.2, *)
private final class ProcessTapFakeHAL: ProcessTapHALBoundary, @unchecked Sendable {
    let currentProcessID: Int32 = 4242
    var translatedProcessObject: UInt32 = 77
    var tapFormat = ProcessTapPCMDescriptor(sampleRate: 48_000, channels: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
    var failOperation: String?
    var failOperations: Set<String> = []
    var failNextOperation: String?
    var rawPermissionErrorOperation: String?
    var rawDeviceAliveReadStatus: OSStatus?
    var emitEventAfterListenerInstallation: ProcessTapHALEvent?
    var startError: Error?
    private let lock = NSLock()
    private var blockOperationValue: String?
    var blockOperation: String? {
        get { lock.withLock { blockOperationValue } }
        set { lock.withLock { blockOperationValue = newValue } }
    }
    let blockEntered = DispatchSemaphore(value: 0)
    let blockRelease = DispatchSemaphore(value: 0)
    private(set) var operations: [String] = []
    private(set) var descriptions: [ProcessTapDescription] = []
    private(set) var aggregateUIDs: [String] = []
    private(set) var listenerKinds: [ProcessTapHALListenerKind] = []
    private(set) var removedTokens: [UInt64] = []
    private var startCountValue = 0
    private var stopCountValue = 0
    private var destroyIOProcCountValue = 0
    private var destroyAggregateCountValue = 0
    private var destroyTapCountValue = 0
    var startCount: Int { lock.withLock { startCountValue } }
    var stopCount: Int { lock.withLock { stopCountValue } }
    var destroyIOProcCount: Int { lock.withLock { destroyIOProcCountValue } }
    var destroyAggregateCount: Int { lock.withLock { destroyAggregateCountValue } }
    var destroyTapCount: Int { lock.withLock { destroyTapCountValue } }
    private(set) var destroyedAggregateIDs: [UInt32] = []
    private(set) var destroyedTapIDs: [UInt32] = []
    private var retiredDeviceAliveHandlers: [@Sendable (ProcessTapHALEvent) -> Void] = []
    private var nextHandle: UInt32 = 100
    private var nextToken: UInt64 = 1
    private var handlers: [UInt64: @Sendable (ProcessTapHALEvent) -> Void] = [:]
    private var handlerKinds: [UInt64: ProcessTapHALListenerKind] = [:]

    private func record(_ operation: String) throws {
        let outcome: (failureKind: Int, shouldBlock: Bool) = lock.withLock {
            operations.append(operation)
            if failOperation == operation || failOperations.contains(operation) { return (1, false) }
            if failNextOperation == operation {
                failNextOperation = nil
                return (1, false)
            }
            if rawPermissionErrorOperation == operation {
                rawPermissionErrorOperation = nil
                return (2, false)
            }
            return (0, blockOperationValue == operation)
        }
        if outcome.failureKind == 1 { throw CompanionError.invalid("fake HAL failure at \(operation)") }
        if outcome.failureKind == 2 {
            throw CoreAudioProcessTapHAL.normalizedError(
                operation: operation,
                status: kAudioDevicePermissionsError
            )
        }
        if outcome.shouldBlock {
            blockEntered.signal()
            blockRelease.wait()
            lock.withLock {
                if blockOperationValue == operation { blockOperationValue = nil }
            }
        }
    }

    func translatePIDToProcessObject(pid: Int32) throws -> UInt32 {
        try record("translate")
        return translatedProcessObject
    }

    func createProcessTap(description: ProcessTapDescription) throws -> UInt32 {
        try record("tap")
        lock.withLock { descriptions.append(description) }
        return nextHandle
    }

    func tapUID(tapID: UInt32) throws -> String {
        try record("tapUID")
        return "tap-uid-\(tapID)"
    }

    func readTapFormat(tapID: UInt32) throws -> ProcessTapPCMDescriptor {
        try record("format")
        return tapFormat
    }

    func createAggregate(tapID: UInt32, tapUID: String, uid: String, name: String) throws -> UInt32 {
        try record("aggregate")
        lock.withLock { aggregateUIDs.append(uid) }
        nextHandle += 1
        return nextHandle
    }

    func createIOProc(aggregateID: UInt32, ringAddress: UInt64) throws -> UInt64 {
        try record("ioProc")
        return 501
    }

    func start(aggregateID: UInt32, ioProc: UInt64) throws {
        try record("start")
        if let startError { throw startError }
        lock.withLock { startCountValue += 1 }
    }

    func stop(aggregateID: UInt32, ioProc: UInt64) throws {
        try record("stop")
        lock.withLock { stopCountValue += 1 }
    }

    func destroyIOProc(aggregateID: UInt32, ioProc: UInt64) throws {
        try record("destroyIOProc")
        lock.withLock { destroyIOProcCountValue += 1 }
    }

    func detachTap(tapID: UInt32, aggregateID: UInt32) throws { try record("detach") }

    func destroyAggregate(aggregateID: UInt32) throws {
        try record("destroyAggregate")
        lock.withLock {
            destroyAggregateCountValue += 1
            destroyedAggregateIDs.append(aggregateID)
        }
    }

    func destroyProcessTap(tapID: UInt32) throws {
        try record("destroyTap")
        lock.withLock {
            destroyTapCountValue += 1
            destroyedTapIDs.append(tapID)
        }
    }

    func installListener(kind: ProcessTapHALListenerKind, aggregateID: UInt32?, tapID: UInt32?, handler: @escaping @Sendable (ProcessTapHALEvent) -> Void) throws -> UInt64 {
        try record("listener-\(kind.rawValue)")
        let token = nextToken
        nextToken += 1
        let eventToEmit: ProcessTapHALEvent? = lock.withLock {
            listenerKinds.append(kind)
            handlers[token] = handler
            handlerKinds[token] = kind
            guard kind == .tapFormat, let event = emitEventAfterListenerInstallation else { return nil }
            emitEventAfterListenerInstallation = nil
            return event
        }
        if let eventToEmit { emit(eventToEmit) }
        return token
    }

    func removeListener(_ token: UInt64) throws {
        try record("removeListener")
        lock.withLock {
            removedTokens.append(token)
            if handlerKinds[token] == .deviceAlive, let handler = handlers[token] {
                // Core Audio may already have queued a property callback when
                // the listener is removed.  Retain one such callback so the
                // retired-operation fence can be exercised deterministically.
                retiredDeviceAliveHandlers.append(handler)
            }
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

    func emitRetiredDeviceAlivePermission() {
        let callbacks = lock.withLock { retiredDeviceAliveHandlers }
        callbacks.forEach { $0(.deviceAliveReadFailed(kAudioDevicePermissionsError)) }
    }
}

private final class ProcessTapRecordingSink: CaptureFrameSink, @unchecked Sendable {
    private let lock = NSLock()
    private var storedFrames: [AudioFrame] = []
    private var storedGaps: [CoverageGap] = []
    private let frameEntered = DispatchSemaphore(value: 0)
    var frames: [AudioFrame] { lock.withLock { storedFrames } }
    var gaps: [CoverageGap] { lock.withLock { storedGaps } }

    func receive(_ frame: AudioFrame) async throws {
        lock.withLock { storedFrames.append(frame) }
        frameEntered.signal()
    }
    func receiveGap(_ gap: CoverageGap) async throws { lock.withLock { storedGaps.append(gap) } }

    func waitForFrameCount(_ expected: Int, timeout: TimeInterval = 1) -> Bool {
        let deadline = DispatchTime.now() + timeout
        while frames.count < expected {
            guard frameEntered.wait(timeout: deadline) == .success else { return false }
        }
        return frames.count >= expected
    }
}

private final class ProcessTapOrderedSink: CaptureFrameSink, @unchecked Sendable {
    enum Event: Equatable {
        case frame(UInt64)
        case gap(GapReason)
    }

    private let lock = NSLock()
    private var storedEvents: [Event] = []
    private var storedGaps: [CoverageGap] = []
    private let eventEntered = DispatchSemaphore(value: 0)
    var events: [Event] { lock.withLock { storedEvents } }
    var gaps: [CoverageGap] { lock.withLock { storedGaps } }

    func receive(_ frame: AudioFrame) async throws {
        lock.withLock { storedEvents.append(.frame(frame.sequence)) }
        eventEntered.signal()
    }
    func receiveGap(_ gap: CoverageGap) async throws {
        lock.withLock {
            storedEvents.append(.gap(gap.reason))
            storedGaps.append(gap)
        }
        eventEntered.signal()
    }

    func waitForEventCount(_ expected: Int, timeout: TimeInterval = 1) -> Bool {
        let deadline = DispatchTime.now() + timeout
        while events.count < expected {
            guard eventEntered.wait(timeout: deadline) == .success else { return false }
        }
        return events.count >= expected
    }
}

private final class ProcessTapBlockingSink: CaptureFrameSink, @unchecked Sendable {
    private let lock = NSLock()
    private var releaseContinuation: CheckedContinuation<Void, Never>?
    private(set) var invocationCount = 0
    let entered = DispatchSemaphore(value: 0)
    let finished = DispatchSemaphore(value: 0)

    func receive(_ frame: AudioFrame) async throws {
        lock.withLock { invocationCount += 1 }
        entered.signal()
        await withCheckedContinuation { continuation in
            lock.withLock { releaseContinuation = continuation }
        }
        finished.signal()
    }

    func receiveGap(_ gap: CoverageGap) async throws {
        _ = gap
    }

    func release() {
        let continuation = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            let value = releaseContinuation
            releaseContinuation = nil
            return value
        }
        continuation?.resume()
    }
}

private final class ProcessTapCapacitySink: CaptureFrameSink, @unchecked Sendable {
    enum Event: Equatable {
        case frame(UInt64)
        case gap(GapReason, UInt64?, UInt64?)
    }

    private let lock = NSLock()
    private var storedEvents: [Event] = []
    private var releaseContinuation: CheckedContinuation<Void, Never>?
    private var releaseRequested = false
    private var blocksFirstFrame = true
    let entered = DispatchSemaphore(value: 0)
    private let eventEntered = DispatchSemaphore(value: 0)

    var events: [Event] { lock.withLock { storedEvents } }

    func receive(_ frame: AudioFrame) async throws {
        let shouldBlock = lock.withLock {
            storedEvents.append(.frame(frame.sequence))
            eventEntered.signal()
            if blocksFirstFrame {
                blocksFirstFrame = false
                return true
            }
            return false
        }
        if shouldBlock {
            await withCheckedContinuation { continuation in
                let resumeImmediately = lock.withLock {
                    if releaseRequested { return true }
                    releaseContinuation = continuation
                    return false
                }
                if resumeImmediately {
                    continuation.resume()
                } else {
                    // Signal only after the continuation is installed.  The
                    // caller may now enqueue the bounded pressure sequence
                    // and release this exact in-flight invocation safely.
                    entered.signal()
                }
            }
        }
    }

    func receiveGap(_ gap: CoverageGap) async throws {
        lock.withLock {
            storedEvents.append(.gap(gap.reason, gap.firstSequence, gap.firstSample))
            eventEntered.signal()
        }
    }

    func release() {
        let continuation = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            releaseRequested = true
            let value = releaseContinuation
            releaseContinuation = nil
            return value
        }
        continuation?.resume()
    }

    func waitForEventCount(_ expected: Int, timeout: TimeInterval = 1) -> Bool {
        let deadline = DispatchTime.now() + timeout
        while events.count < expected {
            guard eventEntered.wait(timeout: deadline) == .success else { return false }
        }
        return events.count >= expected
    }
}

private final class UpdateBox: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [CaptureSourceHealthUpdate] = []
    var count: Int { lock.withLock { values.count } }
    var all: [CaptureSourceHealthUpdate] { lock.withLock { values } }
    func append(_ value: CaptureSourceHealthUpdate) { lock.withLock { values.append(value) } }
}

private final class InvocationCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var stored = 0

    func increment() { lock.withLock { stored += 1 } }
    var value: Int { lock.withLock { stored } }
}

@available(macOS 14.2, *)
private final class RingTerminalScheduleProbe: @unchecked Sendable {
    let fenceClosed = DispatchSemaphore(value: 0)
    let callbackResumed = DispatchSemaphore(value: 0)
    let callbackFinished = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var ringValue: OpaquePointer?
    private var callbackResultValue: OSStatus?
    private var callbackPauseReadyValue = false
    private var noRetainedSlotValue = false

    func installRing(_ ring: OpaquePointer) {
        lock.withLock { ringValue = ring }
    }

    func recordProductionCallback(source: ProcessTapSystemAudioSource, buffer: ProcessTapPCMBuffer) {
        let result = source.invokeRealtimeIOProcForTesting(buffer)
        let ring = lock.withLock { ringValue }
        let pauseReady: Bool = ring.map {
            TarsRealtimeAudioRingPublicationPauseReadyForTesting($0)
        } ?? false
        let noRetainedSlot: Bool = ring.map {
            TarsRealtimeAudioRingRetainedSlots($0) == 0
        } ?? false
        lock.withLock {
            callbackResultValue = result
            callbackPauseReadyValue = pauseReady
            noRetainedSlotValue = noRetainedSlot
        }
        callbackFinished.signal()
    }

    func resumeHeldPublication() {
        let ring = lock.withLock { ringValue }
        if let ring {
            TarsRealtimeAudioRingResumeHeldPublicationForTesting(ring)
        }
        callbackResumed.signal()
    }

    var callbackResult: OSStatus? {
        lock.withLock { callbackResultValue }
    }

    var callbackPauseReady: Bool {
        lock.withLock { callbackPauseReadyValue }
    }

    func recordNoRetainedSlot(_ value: Bool) {
        lock.withLock { noRetainedSlotValue = value }
    }

    var noRetainedSlot: Bool {
        lock.withLock { noRetainedSlotValue }
    }
}

private final class TeardownOwnerRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: [UInt64] = []

    func append(_ owner: UInt64) { lock.withLock { stored.append(owner) } }
    var values: [UInt64] { lock.withLock { stored } }
}

/// A scheduler-independent test signal.  Unlike DispatchSemaphore.wait, the
/// waiter suspends its task and leaves the cooperative executor available for
/// the source owner and its concurrent callers.  The timeout is only a
/// bounded harness guard; causal ordering is established by signal delivery.
private final class AsyncSignal: @unchecked Sendable {
    private let lock = NSLock()
    private var permits = 0
    private var nextWaiterID: UInt64 = 0
    private var waiters: [UInt64: CheckedContinuation<Bool, Never>] = [:]

    func signal() {
        let continuation = lock.withLock { () -> CheckedContinuation<Bool, Never>? in
            if let waiterID = waiters.keys.sorted().first,
               let waiter = waiters.removeValue(forKey: waiterID) {
                return waiter
            }
            permits += 1
            return nil
        }
        continuation?.resume(returning: true)
    }

    func wait(timeout: TimeInterval = 1) async -> Bool {
        await withCheckedContinuation { continuation in
            let waiterID: UInt64? = lock.withLock {
                if permits > 0 {
                    permits -= 1
                    return nil
                }
                nextWaiterID &+= 1
                waiters[nextWaiterID] = continuation
                return nextWaiterID
            }
            guard let waiterID else {
                continuation.resume(returning: true)
                return
            }
            let timeoutNanoseconds = UInt64(max(0, timeout) * 1_000_000_000)
            DispatchQueue.global().asyncAfter(
                deadline: .now() + .nanoseconds(Int(timeoutNanoseconds))
            ) { [weak self] in
                self?.timeout(waiterID)
            }
        }
    }

    private func timeout(_ waiterID: UInt64) {
        let continuation = lock.withLock { waiters.removeValue(forKey: waiterID) }
        continuation?.resume(returning: false)
    }
}

private final class TeardownRaceFixture: @unchecked Sendable {
    private let lock = NSLock()
    private var firstOwner: UInt64?
    private var staleOwner: UInt64?
    let firstWaiterReachedClear = DispatchSemaphore(value: 0)
    let staleWaiterReachedClear = DispatchSemaphore(value: 0)
    let releaseStaleWaiter = DispatchSemaphore(value: 0)

    func observe(owner: UInt64) {
        let role: Int = lock.withLock {
            if firstOwner == nil {
                firstOwner = owner
                return 1
            }
            if owner == firstOwner && staleOwner == nil {
                staleOwner = owner
                return 2
            }
            return 0
        }
        switch role {
        case 1:
            firstWaiterReachedClear.signal()
        case 2:
            staleWaiterReachedClear.signal()
            releaseStaleWaiter.wait()
        default:
            break
        }
    }
}

@available(macOS 14.2, *)
final class ProcessTapSystemAudioSourceTests: XCTestCase {
    private func config(generation: UInt64 = 1) throws -> CaptureSourceConfiguration {
        let identity = try SourceIdentity(sessionID: "tap-test", streamID: "system", captureGeneration: generation, source: .systemAudio, sampleRate: 16_000, channelCount: 1)
        return CaptureSourceConfiguration(identity: identity, deviceIdentity: "ProcessTap.SystemAudio")
    }

    private func floatData(_ values: [Float]) -> Data {
        var data = Data(capacity: values.count * 4)
        for value in values {
            let bits = value.bitPattern
            data.append(UInt8(truncatingIfNeeded: bits))
            data.append(UInt8(truncatingIfNeeded: bits >> 8))
            data.append(UInt8(truncatingIfNeeded: bits >> 16))
            data.append(UInt8(truncatingIfNeeded: bits >> 24))
        }
        return data
    }

    func testAcquireOrderTapScopeAndListenerOwnership() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapRecordingSink()
        let source = ProcessTapSystemAudioSource(configuration: try config(), sink: sink, hal: hal, uuidFactory: { "uid-one" })
        try await source.start()
        XCTAssertEqual(hal.descriptions.single?.processes, [77])
        XCTAssertEqual(hal.descriptions.single?.isMono, true)
        XCTAssertEqual(hal.descriptions.single?.isExclusive, true)
        XCTAssertEqual(hal.descriptions.single?.isPrivate, true)
        XCTAssertEqual(hal.descriptions.single?.isMuted, false)
        XCTAssertEqual(hal.aggregateUIDs, ["uid-one"])
        XCTAssertEqual(Set(hal.listenerKinds), Set(ProcessTapHALListenerKind.allCases))
        if case .running(let health) = source.status {
            XCTAssertEqual(health.permission, .unknown)
        } else {
            XCTFail("expected running status")
        }
        await source.stop()
        await source.stop()
        XCTAssertEqual(hal.removedTokens.count, 5)
        XCTAssertEqual(hal.stopCount, 1)
        XCTAssertEqual(hal.destroyIOProcCount, 1)
        XCTAssertEqual(hal.destroyAggregateCount, 1)
        XCTAssertEqual(hal.destroyTapCount, 1)
    }

    func testUnknownProcessObjectFailsBeforeTapAndHasNoFallback() async throws {
        let hal = ProcessTapFakeHAL()
        hal.translatedProcessObject = UInt32(kAudioObjectUnknown)
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        do {
            try await source.start()
            XCTFail("expected unknown process object failure")
        } catch {
            XCTAssertFalse(hal.operations.contains("tap"))
            XCTAssertFalse(hal.operations.contains("start"))
        }
    }

    func testListenerEventBetweenInstallationAndHALStartAbortsStartingGraph() async throws {
        let hal = ProcessTapFakeHAL()
        hal.emitEventAfterListenerInstallation = .tapFormatChanged
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)

        // The fake emits synchronously from the final listener registration.
        // Startup must abort before HAL start can publish a running graph and
        // must fail loudly rather than looking like a user cancellation.
        do {
            try await source.start()
            XCTFail("a HAL startup event must fail the start operation")
        } catch {
            XCTAssertTrue(String(describing: error).contains("tap-format"))
        }

        XCTAssertEqual(hal.startCount, 0)
        XCTAssertFalse(source.hasRetainedRealtimeResourceForTesting)
        guard case .failed(let message) = source.status else {
            return XCTFail("a starting-state listener event must publish a terminal failure")
        }
        XCTAssertTrue(message.contains("tap-format"))
        await source.stop()
    }

    func testDeviceAlivePermissionReadDuringStartupPreservesExactDeniedCopy() async throws {
        let hal = ProcessTapFakeHAL()
        hal.emitEventAfterListenerInstallation = .deviceAliveReadFailed(kAudioDevicePermissionsError)
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)

        do {
            try await source.start()
            XCTFail("a permission-bearing startup liveness read must fail the acquisition")
        } catch let failure as SystemAudioCaptureFailure {
            XCTAssertEqual(failure, .denied)
        } catch {
            XCTFail("expected typed permission failure, got \(error)")
        }

        XCTAssertEqual(source.status, .failed(SystemAudioCaptureMonitor.permissionDeniedMessage))
        XCTAssertEqual(hal.startCount, 0, "startup denial must abort before AudioDeviceStart")
        XCTAssertEqual(hal.destroyIOProcCount, 1)
        XCTAssertEqual(hal.destroyAggregateCount, 1)
        XCTAssertEqual(hal.destroyTapCount, 1)
        await source.stop()
    }

    func testListenerEventDuringReplacementDoesNotLeaveTornDownGraphRunning() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal, uuidFactory: { "uid-replacement-startup" })
        try await source.start()
        hal.emitEventAfterListenerInstallation = .tapFormatChanged

        // The replacement's final listener emits before its HAL start.  The
        // replacement operation must fail and clean its partial graph rather
        // than returning to .running with a fenced ring.
        await source.triggerEventForTesting(.serviceReset)

        XCTAssertEqual(hal.startCount, 1)
        guard case .failed(let message) = source.status else {
            return XCTFail("a replacement startup event must fail loudly")
        }
        XCTAssertTrue(message.contains("tap-format"))
        XCTAssertFalse(source.hasRetainedRealtimeResourceForTesting)
        await source.stop()
    }

    func testDeliveryWorkerFenceBeforeStartRefusesLateTaskAndStatus() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapRecordingSink()
        let source = ProcessTapSystemAudioSource(configuration: try config(), sink: sink, hal: hal)
        source.deliveryWorkerStartHook = { worker in
            _ = worker.fenceAndDiscard()
        }
        defer { source.deliveryWorkerStartHook = nil }

        do {
            try await source.start()
            XCTFail("a worker fenced before installation must fail the owning start")
        } catch {
            XCTAssertTrue(String(describing: error).contains("delivery-worker-start"))
        }
        XCTAssertEqual(source.status, .failed("O evento HAL delivery-worker-start interrompeu a inicialização da captura de áudio do sistema; a captura não foi iniciada."))
        XCTAssertFalse(source.hasRetainedRealtimeResourceForTesting)
        XCTAssertEqual(hal.startCount, 1)
        await source.stop()
        XCTAssertEqual(hal.startCount, 1)
    }

    func testDeliveryWorkerActivationLeaseRejectsFenceBeforeRelease() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapRecordingSink()
        let source = ProcessTapSystemAudioSource(configuration: try config(), sink: sink, hal: hal)
        let activationEntered = DispatchSemaphore(value: 0)
        source.deliveryWorkerActivationHook = { worker in
            // This models a concurrent stop/recovery winning the worker
            // ownership edge after the graph is acquired but before the
            // suspended consumer is released.
            _ = worker.fenceAndDiscard()
            activationEntered.signal()
        }
        defer { source.deliveryWorkerActivationHook = nil }

        do {
            try await source.start()
            XCTFail("a fenced activation lease must fail the owning start")
        } catch {
            XCTAssertTrue(activationEntered.wait(timeout: .now() + 1) == .success)
        }
        if case .running = source.status {
            XCTFail("a stopped activation lease must not publish a running source")
        }
        XCTAssertFalse(source.hasRetainedRealtimeResourceForTesting)
        XCTAssertEqual(hal.startCount, 1)
        await source.stop()
        XCTAssertEqual(hal.stopCount, 1)
        XCTAssertEqual(hal.destroyIOProcCount, 1)
        XCTAssertEqual(hal.destroyAggregateCount, 1)
        XCTAssertEqual(hal.destroyTapCount, 1)
    }

    func testMalformedCallbackDuringBlockedHALStartIsUnwoundByStartOwner() async throws {
        let hal = ProcessTapFakeHAL()
        hal.blockOperation = "start"
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        let startTask = Task { () -> Error? in
            do {
                try await source.start()
                return nil
            } catch {
                return error
            }
        }
        XCTAssertEqual(hal.blockEntered.wait(timeout: .now() + 1), .success)

        let malformedFormat = ProcessTapPCMFormat(
            sampleRate: 44_100,
            channelCount: 1,
            isFloat: true,
            isInterleaved: true,
            bitsPerChannel: 32,
            bytesPerFrame: 4
        )
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        XCTAssertEqual(
            source.submitForTesting(ProcessTapPCMBuffer(format: malformedFormat, buffers: [body], generation: 1)),
            TARS_REALTIME_DESCRIPTOR_MALFORMED
        )

        // The callback-side monitor action may mark startup as aborted, but it
        // must not tear down anything while the owning HAL start is blocked.
        XCTAssertEqual(hal.stopCount, 0)
        XCTAssertEqual(hal.destroyIOProcCount, 0)
        XCTAssertEqual(hal.destroyAggregateCount, 0)
        XCTAssertEqual(hal.destroyTapCount, 0)

        hal.blockRelease.signal()
        let startError = await startTask.value
        XCTAssertNotNil(startError)
        XCTAssertEqual(hal.stopCount, 1)
        XCTAssertEqual(hal.destroyIOProcCount, 1)
        XCTAssertEqual(hal.destroyAggregateCount, 1)
        XCTAssertEqual(hal.destroyTapCount, 1)
        XCTAssertFalse(source.hasRetainedRealtimeResourceForTesting)
        guard case .failed(let message) = source.status else {
            return XCTFail("a startup callback failure must not publish a running graph")
        }
        XCTAssertTrue(message.contains("malformado"), message)

        await source.stop()
        XCTAssertEqual(hal.stopCount, 1, "duplicate cleanup must not stop the same graph twice")
        XCTAssertEqual(hal.destroyIOProcCount, 1)
        XCTAssertEqual(hal.destroyAggregateCount, 1)
        XCTAssertEqual(hal.destroyTapCount, 1)
    }

    func testTerminalPermissionDuringConversionSnapshotsCursorAfterDrainOwnership() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapOrderedSink()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            sink: sink,
            hal: hal
        )
        let conversionEntered = DispatchSemaphore(value: 0)
        let releaseConversion = DispatchSemaphore(value: 0)
        let terminalClaimed = DispatchSemaphore(value: 0)
        let failed = DispatchSemaphore(value: 0)
        source.drainPausedForTesting = true
        source.deliveryWorkerStartHook = { worker in worker.suspendForTesting() }
        source.conversionHook = {
            conversionEntered.signal()
            releaseConversion.wait()
        }
        source.terminalFailureClaimHook = { terminalClaimed.signal() }
        _ = source.installHealthObserver { update in
            if case .failed = update.status { failed.signal() }
        }
        defer {
            source.conversionHook = nil
            source.terminalFailureClaimHook = nil
            source.deliveryWorkerStartHook = nil
        }
        try await source.start()

        let format = ProcessTapPCMFormat(
            sampleRate: 48_000,
            channelCount: 1,
            isFloat: true,
            isInterleaved: true,
            bitsPerChannel: 32,
            bytesPerFrame: 4
        )
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        XCTAssertEqual(
            source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)),
            TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY
        )
        let drain = Task { source.drainOneForTesting() }
        XCTAssertEqual(conversionEntered.wait(timeout: .now() + 1), .success)

        // The listener claims terminal ownership while conversion still owns
        // ringUseLock.  The owner must wait for that conversion to finish,
        // then snapshot the advanced cursor before fencing/discarding the
        // worker item.
        let terminal = Task {
            hal.emit(.deviceAliveReadFailed(kAudioDevicePermissionsError))
        }
        XCTAssertEqual(terminalClaimed.wait(timeout: .now() + 1), .success)
        XCTAssertEqual(hal.stopCount, 0)
        releaseConversion.signal()
        _ = await drain.value
        await terminal.value
        XCTAssertEqual(failed.wait(timeout: .now() + 1), .success)

        XCTAssertEqual(source.status, .failed(SystemAudioCaptureMonitor.permissionDeniedMessage))
        XCTAssertEqual(sink.events, [.gap(.unknownEnd)])
        let gap = try XCTUnwrap(sink.gaps.first)
        XCTAssertEqual(gap.firstSequence, 0)
        XCTAssertEqual(gap.firstSample, 0)
        XCTAssertEqual(source.terminalEvidenceGapsForTesting.map(\.firstSequence), [0])
        await source.stop()
    }

    func testTerminalFailureCapturesRawRingBacklogBeforePurge() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapOrderedSink()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            sink: sink,
            hal: hal
        )
        let failed = DispatchSemaphore(value: 0)
        let retirementEntered = DispatchSemaphore(value: 0)
        let retirementRelease = DispatchSemaphore(value: 0)
        let terminalRetirementCount = InvocationCounter()
        let genericFenceCount = InvocationCounter()
        source.drainPausedForTesting = true
        source.deliveryWorkerStartHook = { worker in worker.suspendForTesting() }
        source.terminalRetirementHook = { _ in
            terminalRetirementCount.increment()
            retirementEntered.signal()
            _ = retirementRelease.wait(timeout: .now() + 10)
        }
        source.ringFenceHook = { genericFenceCount.increment() }
        let observerToken = source.installHealthObserver { update in
            if case .failed = update.status { failed.signal() }
        }
        defer {
            source.removeHealthObserver(observerToken)
            source.deliveryWorkerStartHook = nil
            source.terminalRetirementHook = nil
            source.ringFenceHook = nil
            retirementRelease.signal()
        }
        try await source.start()

        let format = ProcessTapPCMFormat(
            sampleRate: 48_000,
            channelCount: 1,
            isFloat: true,
            isInterleaved: true,
            bitsPerChannel: 32,
            bytesPerFrame: 4
        )
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        XCTAssertEqual(
            source.deliveryQueueCountForTesting,
            0,
            "the terminal race begins with no converted or raw backlog"
        )

        // The listener's permission path claims terminal ownership directly.
        // Pause immediately before the C close-admission edge, then inject a
        // real production IOProc callback.  The retirement snapshot must
        // include that admitted raw slot before purge, rather than relying on
        // a retained-slot read taken before the callback could publish.
        let terminal = Task.detached {
            hal.emit(.deviceAliveReadFailed(kAudioDevicePermissionsError))
        }
        XCTAssertEqual(retirementEntered.wait(timeout: .now() + 1), .success)
        XCTAssertEqual(
            source.invokeRealtimeIOProcForTesting(
                ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)
            ),
            noErr
        )
        retirementRelease.signal()
        await terminal.value
        XCTAssertEqual(failed.wait(timeout: .now() + 1), .success)
        XCTAssertEqual(source.status, .failed(SystemAudioCaptureMonitor.permissionDeniedMessage))
        XCTAssertEqual(sink.events, [.gap(.unknownEnd)])
        let gap = try XCTUnwrap(sink.gaps.first)
        XCTAssertEqual(gap.firstSequence, 0)
        XCTAssertEqual(gap.firstSample, 0)
        XCTAssertEqual(source.terminalEvidenceGapsForTesting.map(\.firstSequence), [0])
        XCTAssertEqual(hal.startCount, 1)
        XCTAssertEqual(terminalRetirementCount.value, 1)
        XCTAssertEqual(genericFenceCount.value, 0, "terminal retirement must not be followed by a second generic SetGeneration(0)")
        await source.stop()
    }

    func testTerminalFailureDeliversExternallyHeldAdmittedLossWithoutRetainedSlot() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapOrderedSink()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            sink: sink,
            hal: hal
        )
        let schedule = RingTerminalScheduleProbe()
        let terminalFinished = DispatchSemaphore(value: 0)
        let failed = DispatchSemaphore(value: 0)
        let fenceHook: TarsRealtimePublicationFenceHook = { context in
            guard let context else { return }
            Unmanaged<RingTerminalScheduleProbe>
                .fromOpaque(context)
                .takeUnretainedValue()
                .fenceClosed
                .signal()
        }
        let format = ProcessTapPCMFormat(
            sampleRate: 48_000,
            channelCount: 1,
            isFloat: true,
            isInterleaved: true,
            bitsPerChannel: 32,
            bytesPerFrame: 4
        )
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        source.drainPausedForTesting = true
        source.deliveryWorkerStartHook = { worker in worker.suspendForTesting() }
        source.terminalRetirementHook = { ring in
            // This is the externally controlled post-copy/pre-final-check
            // edge.  The production callback returns while its admission is
            // still held; the non-realtime retirement below closes the gate
            // and waits.  Only the test control actor resumes the held edge.
            schedule.installRing(ring)
            TarsRealtimeAudioRingSetPublicationFenceHookForTesting(
                ring,
                fenceHook,
                Unmanaged.passUnretained(schedule).toOpaque()
            )
            TarsRealtimeAudioRingHoldNextFinalPublicationForTesting(ring)
            DispatchQueue.global().async {
                schedule.recordProductionCallback(
                    source: source,
                    buffer: ProcessTapPCMBuffer(
                        format: format,
                        buffers: [body],
                        generation: 1
                    )
                )
            }
            // The bounded wait is only a fixture guard.  The callback's
            // completion and PauseReady observation establish the causal
            // edge before this hook returns and C closes admission.
            _ = schedule.callbackFinished.wait(timeout: .now() + 1)
        }
        let observerToken = source.installHealthObserver { update in
            if case .failed = update.status { failed.signal() }
        }
        defer {
            source.removeHealthObserver(observerToken)
            source.deliveryWorkerStartHook = nil
            source.terminalRetirementHook = nil
        }

        try await source.start()
        XCTAssertEqual(source.deliveryQueueCountForTesting, 0)

        // The terminal listener owns the retirement edge.  Its C publication
        // hook proves that close/activeGeneration=0 happened while the
        // admitted callback is still held, and the immediate completion
        // check proves retirement cannot return early.
        let terminal = Task.detached {
            hal.emit(.deviceAliveReadFailed(kAudioDevicePermissionsError))
            terminalFinished.signal()
        }
        XCTAssertEqual(schedule.fenceClosed.wait(timeout: .now() + 1), .success)
        XCTAssertTrue(schedule.callbackPauseReady)
        XCTAssertEqual(schedule.callbackResult, noErr)
        XCTAssertTrue(schedule.noRetainedSlot)
        XCTAssertEqual(
            terminalFinished.wait(timeout: .now()),
            .timedOut,
            "terminal retirement must wait for the externally resumed admitted callback"
        )

        schedule.resumeHeldPublication()
        XCTAssertEqual(schedule.callbackResumed.wait(timeout: .now() + 1), .success)
        await terminal.value
        XCTAssertEqual(failed.wait(timeout: .now() + 1), .success)

        XCTAssertEqual(source.status, .failed(SystemAudioCaptureMonitor.permissionDeniedMessage))
        XCTAssertEqual(sink.events, [.gap(.unknownEnd)])
        let gap = try XCTUnwrap(sink.gaps.first)
        XCTAssertEqual(gap.firstSequence, 0)
        XCTAssertEqual(gap.firstSample, 0)
        XCTAssertEqual(source.terminalEvidenceGapsForTesting.map(\.firstSequence), [0])

        // The dynamic no-retained-slot/had-admitted-loss schedule is the
        // mutation-effective proof for this source predicate: deleting
        // `|| rawRetirement.hadAdmittedLoss` leaves no boundary to emit.
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let sourceText = try String(
            contentsOf: packageRoot.appendingPathComponent(
                "Sources/TarsNativeCompanion/ProcessTapSystemAudioSource.swift"
            )
        )
        XCTAssertTrue(
            sourceText.contains("rawRetirement.hadRetainedSlots || rawRetirement.hadAdmittedLoss"),
            "terminal source must consume an admitted-loss result even when no slot was retained"
        )
        await source.stop()
    }

    func testExpiredStopDeadlineRefusesQueuedSinkAdmissionAndAllowsFreshRestart() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapRecordingSink()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            sink: sink,
            hal: hal,
            stopDeadlineNanoseconds: 0
        )
        source.deliveryWorkerStartHook = { worker in worker.suspendForTesting() }
        defer { source.deliveryWorkerStartHook = nil }
        try await source.start()

        let format = ProcessTapPCMFormat(
            sampleRate: 48_000,
            channelCount: 1,
            isFloat: true,
            isInterleaved: true,
            bitsPerChannel: 32,
            bytesPerFrame: 4
        )
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        XCTAssertEqual(
            source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)),
            TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY
        )
        await source.drainForTesting()
        XCTAssertEqual(source.deliveryQueueCountForTesting, 1)

        await source.stop()
        XCTAssertTrue(sink.frames.isEmpty, "an already-expired stop must not start a queued sink call")

        // The expired queue was retired with its worker. A subsequent start
        // owns a fresh worker and can deliver normally once the test-only
        // suspension hook is removed.
        source.deliveryWorkerStartHook = nil
        try await source.start()
        XCTAssertEqual(
            source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)),
            TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY
        )
        await source.drainForTesting()
        XCTAssertTrue(sink.waitForFrameCount(1))
        await source.stop()
    }

    func testWatchdogExpiryDuringBlockedHALStartIsReevaluatedAfterActivation() async throws {
        let hal = ProcessTapFakeHAL()
        hal.blockOperation = "start"
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            hal: hal,
            monotonicClock: { 0 },
            stopDeadlineNanoseconds: 1
        )
        let startTask = Task { try? await source.start() }
        XCTAssertEqual(hal.blockEntered.wait(timeout: .now() + 1), .success)

        // This explicit trigger is the deterministic equivalent of the
        // one-shot watchdog deadline firing while AudioDeviceStart is still
        // blocked.  It must be remembered, not discarded by the .starting
        // lifecycle guard.
        await source.triggerWatchdogForTesting(nowNanoseconds: UInt64.max)
        XCTAssertEqual(hal.stopCount, 0)
        hal.blockRelease.signal()
        await startTask.value

        XCTAssertEqual(hal.startCount, 2, "the expired startup deadline must cause exactly one bounded recovery")
        XCTAssertEqual(source.activeLifecycleGeneration, 2)
        guard case .running = source.status else {
            return XCTFail("a recovered watchdog must leave the source running")
        }
        await source.stop()
    }

    func testPreStartRawPermissionStatusUsesExactDeniedCopyAndCleansGraph() async throws {
        let hal = ProcessTapFakeHAL()
        hal.rawPermissionErrorOperation = "tapUID"
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        do {
            try await source.start()
            XCTFail("raw Core Audio permission status must fail acquisition")
        } catch let failure as SystemAudioCaptureFailure {
            XCTAssertEqual(failure, .denied)
        } catch {
            XCTFail("expected typed permission failure, got \(error)")
        }
        XCTAssertEqual(source.status, .failed(SystemAudioCaptureMonitor.permissionDeniedMessage))
        XCTAssertFalse(source.hasRetainedRealtimeResourceForTesting)
        XCTAssertEqual(hal.operations, ["translate", "tap", "tapUID", "destroyTap"])
        await source.stop()
    }

    func testStopDuringStartWaitsForTheAcquisitionOwnerAndCleansEachEdgeOnce() async throws {
        let hal = ProcessTapFakeHAL()
        hal.blockOperation = "translate"
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        let startTask = Task { try? await source.start() }
        XCTAssertEqual(hal.blockEntered.wait(timeout: .now() + 1), .success)
        let stopTask = Task { await source.stop() }
        hal.blockRelease.signal()
        _ = await startTask.value
        await stopTask.value
        XCTAssertEqual(hal.operations.filter { $0 == "translate" }.count, 1)
        XCTAssertEqual(hal.stopCount, 0)
        XCTAssertEqual(hal.destroyIOProcCount, 0)
        XCTAssertEqual(hal.destroyAggregateCount, 0)
        XCTAssertEqual(hal.destroyTapCount, 0)
        XCTAssertEqual(source.status, .stopped(SourceHealth(permission: .unknown, route: .unknown, deviceIdentity: "ProcessTap.SystemAudio")))
    }

    func testConcurrentBlockedStartAndDuplicateStopsSerializeGraphOwnership() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        let acquisitionEntered = AsyncSignal()
        let acquisitionRelease = AsyncSignal()
        let stopClaims = AsyncSignal()
        source.acquisitionPauseHook = {
            acquisitionEntered.signal()
            _ = await acquisitionRelease.wait(timeout: 10)
        }
        source.stopClaimHook = { stopClaims.signal() }
        defer {
            source.acquisitionPauseHook = nil
            source.teardownPauseHook = nil
            source.stopClaimHook = nil
            source.stopOwnerPauseHook = nil
            source.deliveryWorkerStartHook = nil
            source.teardownCompletionHook = nil
            acquisitionRelease.signal()
        }

        // Suspend the acquisition owner at an explicit async fixture edge,
        // after its first HAL operation.  This keeps the test's executor
        // unblocked while two independent stop callers join the same starting
        // epoch; no semaphore wait is used as a scheduling mechanism.
        let startTask = Task.detached { try? await source.start() }
        let acquisitionWasEntered = await acquisitionEntered.wait()
        XCTAssertTrue(acquisitionWasEntered)

        // Both callers observe the same starting ownership epoch.  Neither
        // may invoke HAL cleanup until the acquisition owner has returned from
        // its paused acquisition edge, and the shared teardown task must
        // execute each edge at most once.
        let firstStop = Task.detached { await source.stop() }
        let secondStop = Task.detached { await source.stop() }
        let firstStopClaimed = await stopClaims.wait()
        let secondStopClaimed = await stopClaims.wait()
        XCTAssertTrue(firstStopClaimed)
        XCTAssertTrue(secondStopClaimed)
        acquisitionRelease.signal()
        _ = await startTask.value
        await firstStop.value
        await secondStop.value

        XCTAssertEqual(hal.operations.filter { $0 == "translate" }.count, 1)
        XCTAssertEqual(hal.stopCount, 0)
        XCTAssertEqual(hal.destroyIOProcCount, 0)
        XCTAssertEqual(hal.destroyAggregateCount, 0)
        XCTAssertEqual(hal.destroyTapCount, 0)
        XCTAssertEqual(source.status, .stopped(SourceHealth(permission: .unknown, route: .unknown, deviceIdentity: "ProcessTap.SystemAudio")))

        // Repeat the duplicate-stop race against a fully owned graph.  The
        // shared stop owner pauses asynchronously before it creates the
        // lower-level teardown task.  Both callers must claim that same owner
        // before any cleanup task exists; neither may return until its one
        // cleanup edge has completed.
        source.acquisitionPauseHook = nil
        source.stopClaimHook = nil
        let ownedStopClaims = AsyncSignal()
        source.stopClaimHook = { ownedStopClaims.signal() }
        let graphStarted = AsyncSignal()
        source.deliveryWorkerStartHook = { _ in graphStarted.signal() }
        let stopOwnerEntered = AsyncSignal()
        let stopOwnerRelease = AsyncSignal()
        source.stopOwnerPauseHook = {
            stopOwnerEntered.signal()
            _ = await stopOwnerRelease.wait(timeout: 10)
        }
        let teardownEntered = AsyncSignal()
        let teardownRelease = AsyncSignal()
        let teardownCompleted = AsyncSignal()
        let teardownOwners = TeardownOwnerRecorder()
        source.teardownCompletionHook = { owner in
            teardownOwners.append(owner)
            teardownCompleted.signal()
        }
        source.teardownPauseHook = {
            teardownEntered.signal()
            _ = await teardownRelease.wait(timeout: 10)
        }
        try await source.start()
        let graphWasStarted = await graphStarted.wait()
        XCTAssertTrue(
            graphWasStarted,
            "the second start must establish a fully owned graph before duplicate-stop assertions"
        )
        XCTAssertEqual(hal.startCount, 1, "the fully-owned subcase must actually execute HAL start")
        let ownedFirstStop = Task.detached { await source.stop() }
        let stopOwnerWasEntered = await stopOwnerEntered.wait()
        XCTAssertTrue(stopOwnerWasEntered, "the first stop must install the shared owner")
        let ownedSecondStop = Task.detached { await source.stop() }
        let ownedFirstClaimed = await ownedStopClaims.wait()
        let ownedSecondClaimed = await ownedStopClaims.wait()
        XCTAssertTrue(ownedFirstClaimed)
        XCTAssertTrue(ownedSecondClaimed)
        XCTAssertNil(source.teardownOwnerForTesting, "both stop callers must claim before lower-level teardown exists")
        stopOwnerRelease.signal()
        let teardownWasEntered = await teardownEntered.wait()
        XCTAssertTrue(teardownWasEntered)
        let sharedTeardownOwner = source.teardownOwnerForTesting
        XCTAssertNotNil(sharedTeardownOwner, "the shared stop owner must create one owned teardown task")
        teardownRelease.signal()
        let teardownDidComplete = await teardownCompleted.wait()
        XCTAssertTrue(teardownDidComplete, "the shared teardown owner must complete its HAL cleanup before either stop returns")
        await ownedFirstStop.value
        await ownedSecondStop.value

        let completedOwners = teardownOwners.values
        XCTAssertEqual(completedOwners.count, 1)
        XCTAssertTrue(
            completedOwners.allSatisfy { $0 == sharedTeardownOwner },
            "duplicate stops must report one exact shared teardown owner"
        )
        XCTAssertEqual(hal.stopCount, 1)
        XCTAssertEqual(hal.destroyIOProcCount, 1)
        XCTAssertEqual(hal.destroyAggregateCount, 1)
        XCTAssertEqual(hal.destroyTapCount, 1)
        XCTAssertTrue(source.isIdleForTesting)
    }

    func testFunctionalAndSilentPCMHealthEvidence() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapRecordingSink()
        let source = ProcessTapSystemAudioSource(configuration: try config(), sink: sink, hal: hal, uuidFactory: { "uid-health" })
        try await source.start()
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let silent = ProcessTapPCMBuffer(format: format, buffers: [floatData(Array(repeating: 0, count: 2_400))], generation: 1)
        XCTAssertEqual(source.submitForTesting(silent), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        await source.drainForTesting()
        XCTAssertTrue(sink.waitForFrameCount(1), "silent PCM must be delivered before health assertions")
        if case .running(let health) = source.status { XCTAssertEqual(health.permission, .unknown) } else { XCTFail("expected running") }
        let signal = ProcessTapPCMBuffer(format: format, buffers: [floatData(Array(repeating: 0.2, count: 2_400))], generation: 1)
        _ = source.submitForTesting(signal)
        await source.drainForTesting()
        XCTAssertTrue(sink.waitForFrameCount(2), "functional PCM must be delivered before frame-order assertions")
        if case .running(let health) = source.status { XCTAssertEqual(health.permission, .granted) } else { XCTFail("expected running") }
        XCTAssertEqual(sink.frames.first?.sequence, 0)
        await source.stop()
    }

    func testMalformedOrCapacityRejectedInputFailsWithoutPermissionDenial() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        try await source.start()
        let wrongFormat = ProcessTapPCMFormat(sampleRate: 44_100, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let input = ProcessTapPCMBuffer(
            format: wrongFormat,
            buffers: [floatData([0.25])],
            generation: 1
        )
        XCTAssertEqual(source.submitForTesting(input), TARS_REALTIME_DESCRIPTOR_MALFORMED)
        await source.drainForTesting()
        if case .failed(let message) = source.status {
            XCTAssertFalse(message == SystemAudioCaptureMonitor.permissionDeniedMessage)
            XCTAssertFalse(message == SystemAudioCaptureMonitor.ambiguousCaptureMessage)
        } else {
            XCTFail("malformed input must fail loudly without being described as denial")
        }
        await source.stop()
    }

    func testMalformedCallbackAfterValidWatchdogCancellationTearsDownExactlyOnce() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapRecordingSink()
        let source = ProcessTapSystemAudioSource(configuration: try config(), sink: sink, hal: hal)
        try await source.start()
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        XCTAssertEqual(source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        await source.drainForTesting()
        XCTAssertTrue(sink.waitForFrameCount(1), "the valid frame must complete before the malformed callback")
        let deliveredBeforeFailure = sink.frames.count

        let malformedFormat = ProcessTapPCMFormat(sampleRate: 44_100, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        XCTAssertEqual(source.submitForTesting(ProcessTapPCMBuffer(format: malformedFormat, buffers: [body], generation: 1)), TARS_REALTIME_DESCRIPTOR_MALFORMED)
        await source.drainForTesting()
        guard case .failed = source.status else { return XCTFail("malformed input after a valid callback must terminate the graph") }
        XCTAssertEqual(hal.stopCount, 1)
        XCTAssertEqual(hal.destroyIOProcCount, 1)
        XCTAssertEqual(hal.destroyAggregateCount, 1)
        XCTAssertEqual(hal.destroyTapCount, 1)

        _ = source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1))
        await source.drainForTesting()
        XCTAssertEqual(sink.frames.count, deliveredBeforeFailure, "no frame may be delivered after terminal teardown")
        await source.stop()
    }

    func testTerminalFailureEmitsQueuedDiscardGapBeforeFinalFailureStatus() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapCapacitySink()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            sink: sink,
            hal: hal,
            stopDeadlineNanoseconds: 1_000_000_000
        )
        let updates = UpdateBox()
        _ = source.installHealthObserver { updates.append($0) }
        try await source.start()

        let format = ProcessTapPCMFormat(
            sampleRate: 48_000,
            channelCount: 1,
            isFloat: true,
            isInterleaved: true,
            bitsPerChannel: 32,
            bytesPerFrame: 4
        )
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        let makeFrame = {
            ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)
        }
        _ = source.submitForTesting(makeFrame())
        await source.drainForTesting()
        XCTAssertEqual(sink.entered.wait(timeout: .now() + 1), .success)
        source.drainPausedForTesting = true
        for _ in 0..<3 {
            _ = source.submitForTesting(makeFrame())
            XCTAssertTrue(source.drainOneForTesting(), "each queued frame must be explicitly converted")
        }
        XCTAssertEqual(source.deliveryQueueCountForTesting, 3, "the terminal fixture must establish three queued items before fencing")

        let malformed = ProcessTapPCMFormat(
            sampleRate: 44_100,
            channelCount: 1,
            isFloat: true,
            isInterleaved: true,
            bitsPerChannel: 32,
            bytesPerFrame: 4
        )
        let deliveryFence = DispatchSemaphore(value: 0)
        source.deliveryFenceHook = { deliveryFence.signal() }
        XCTAssertEqual(
            source.submitForTesting(ProcessTapPCMBuffer(format: malformed, buffers: [body], generation: 1)),
            TARS_REALTIME_DESCRIPTOR_MALFORMED
        )
        XCTAssertEqual(deliveryFence.wait(timeout: .now() + 1), .success, "terminal failure must fence delivery before releasing the blocked sink")
        // The first invocation is still the ordered owner of the sink.  The
        // terminal gap must not overtake it or become visible while it is
        // blocked.
        XCTAssertEqual(sink.events, [.frame(0)])
        XCTAssertFalse(updates.all.contains { if case .failed = $0.status { return true }; return false })

        sink.release()
        XCTAssertTrue(sink.waitForEventCount(2), "queued discard evidence must be emitted")
        await source.drainForTesting()
        XCTAssertEqual(sink.events, [.frame(0), .gap(.unknownEnd, 1, 800)])
        XCTAssertEqual(source.terminalEvidenceGapsForTesting.map(\.firstSequence), [1])
        guard case .failed = source.status else { return XCTFail("terminal status must follow queued evidence") }
        XCTAssertTrue(updates.all.last.map { if case .failed = $0.status { return true }; return false } ?? false)
        await source.stop()
    }

    func testFenceAfterWorkerDequeueCompletesAdmittedItemExactlyOnce() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapRecordingSink()
        let source = ProcessTapSystemAudioSource(configuration: try config(), sink: sink, hal: hal)
        source.drainPausedForTesting = true
        let dequeueEntered = DispatchSemaphore(value: 0)
        let releaseAdmission = DispatchSemaphore(value: 0)
        source.deliveryAdmissionHook = {
            dequeueEntered.signal()
            releaseAdmission.wait()
        }
        defer {
            source.deliveryAdmissionHook = nil
            releaseAdmission.signal()
        }
        try await source.start()

        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        XCTAssertEqual(
            source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)),
            TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY
        )
        XCTAssertTrue(source.drainOneForTesting())
        XCTAssertEqual(dequeueEntered.wait(timeout: .now() + 1), .success)

        // Fence while the worker owns the dequeued item.  The lock-owned
        // admission edge makes the invocation visible as in-flight before the
        // fence can remove queued items; releasing it must deliver once, not
        // silently discard a removed-not-in-flight frame.
        let stopTask = Task { await source.stop() }
        releaseAdmission.signal()
        await stopTask.value
        XCTAssertTrue(sink.waitForFrameCount(1))
        XCTAssertEqual(sink.frames.count, 1)
        XCTAssertEqual(hal.stopCount, 1)
        XCTAssertEqual(hal.destroyIOProcCount, 1)
        XCTAssertEqual(hal.destroyAggregateCount, 1)
        XCTAssertEqual(hal.destroyTapCount, 1)
    }

    func testStaleGenerationIsCountedSeparatelyFromMalformedInput() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        try await source.start()
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let stale = ProcessTapPCMBuffer(format: format, buffers: [floatData([0.25])], generation: 99)
        XCTAssertEqual(source.submitForTesting(stale), TARS_REALTIME_DESCRIPTOR_STALE_GENERATION)
        if case .running(let health) = source.status {
            XCTAssertEqual(health.permission, .unknown)
        } else {
            XCTFail("stale input must not become a malformed/permission failure")
        }
        await source.stop()
    }

    func testExplicitCoreAudioPermissionErrorUsesOnlyDeniedCopy() async throws {
        let hal = ProcessTapFakeHAL()
        hal.startError = SystemAudioCaptureFailure.denied
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        do {
            try await source.start()
            XCTFail("permission error must fail start")
        } catch {
            XCTAssertEqual(error as? SystemAudioCaptureFailure, .denied)
        }
        if case .failed(let message) = source.status {
            XCTAssertEqual(message, SystemAudioCaptureMonitor.permissionDeniedMessage)
        } else {
            XCTFail("permission error must publish denied copy")
        }
        await source.stop()
    }

    func testLiveDeviceAlivePermissionReadFailsTerminallyWithoutRecovery() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        let updates = UpdateBox()
        _ = source.installHealthObserver { updates.append($0) }
        try await source.start()
        XCTAssertEqual(hal.startCount, 1)

        // The fake performs the same raw-status-to-event conversion as the
        // live listener property read.  The permission status must take the
        // terminal path before the generic route-recovery path can claim its
        // one rebuild budget.
        hal.rawDeviceAliveReadStatus = kAudioDevicePermissionsError
        hal.emitLiveDeviceAliveRead()
        // Terminal publication follows the bounded queued-evidence drain and
        // teardown task.  Join that explicit owner rather than sampling a
        // status while the failure task is still in flight.
        await source.drainForTesting()

        XCTAssertEqual(
            source.status,
            .failed(SystemAudioCaptureMonitor.permissionDeniedMessage)
        )
        XCTAssertEqual(
            updates.all.last?.status,
            .failed(SystemAudioCaptureMonitor.permissionDeniedMessage)
        )
        XCTAssertEqual(source.activeLifecycleGeneration, 1)
        XCTAssertEqual(hal.startCount, 1, "a permission-bearing liveness read must not rebuild or fall back")
        XCTAssertEqual(hal.aggregateUIDs.count, 1)

        await source.stop()
        XCTAssertEqual(hal.stopCount, 1)
        XCTAssertEqual(hal.destroyIOProcCount, 1)
        XCTAssertEqual(hal.destroyAggregateCount, 1)
        XCTAssertEqual(hal.destroyTapCount, 1)
        XCTAssertTrue(source.isIdleForTesting)
    }

    func testFailureAfterIOProcUnwindsPriorEdgesExactlyOnce() async throws {
        for failingOperation in ["listener-sleepWake", "listener-serviceReset", "listener-tapList", "listener-deviceAlive", "listener-tapFormat", "start"] {
            let hal = ProcessTapFakeHAL()
            hal.failOperation = failingOperation
            let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal, uuidFactory: { "uid-fail-\(failingOperation)" })
            do { try await source.start() } catch { }
            let listenerNames = [
                "listener-sleepWake", "listener-serviceReset", "listener-tapList",
                "listener-deviceAlive", "listener-tapFormat"
            ]
            let installed = listenerNames.firstIndex(of: failingOperation) ?? listenerNames.count
            let expected = [
                "translate", "tap", "tapUID", "format", "aggregate", "ioProc"
            ] + Array(listenerNames.prefix(installed)) + [failingOperation] +
                Array(repeating: "removeListener", count: installed) +
                ["destroyIOProc", "detach", "destroyAggregate", "destroyTap"]
            XCTAssertEqual(hal.operations, expected, failingOperation)
            XCTAssertEqual(hal.stopCount, 0, failingOperation)
            XCTAssertEqual(hal.destroyIOProcCount, 1, failingOperation)
            XCTAssertEqual(hal.destroyAggregateCount, 1, failingOperation)
            XCTAssertEqual(hal.destroyTapCount, 1, failingOperation)
        }
    }

    func testEveryAcquisitionEdgeRollsBackExactlyTheOwnedEdges() async throws {
        let failures = [
            "translate", "tap", "tapUID", "format", "aggregate", "ioProc",
            "listener-sleepWake", "listener-serviceReset", "listener-tapList",
            "listener-deviceAlive", "listener-tapFormat", "start"
        ]
        let listenerNames = ["listener-sleepWake", "listener-serviceReset", "listener-tapList", "listener-deviceAlive", "listener-tapFormat"]
        for failingOperation in failures {
            let hal = ProcessTapFakeHAL()
            hal.failOperation = failingOperation
            let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal, uuidFactory: { "uid-edge-\(failingOperation)" })
            do { try await source.start() } catch { }
            let setup = ["translate", "tap", "tapUID", "format"]
            let expected: [String]
            switch failingOperation {
            case "translate":
                expected = ["translate"]
            case "tap":
                expected = ["translate", "tap"]
            case "tapUID":
                expected = ["translate", "tap", "tapUID", "destroyTap"]
            case "format":
                expected = setup + ["destroyTap"]
            case "aggregate":
                expected = setup + ["aggregate", "destroyTap"]
            case "ioProc":
                expected = setup + ["aggregate", "ioProc", "detach", "destroyAggregate", "destroyTap"]
            case let listener where listenerNames.contains(listener):
                let installed = listenerNames.firstIndex(of: listener) ?? 0
                var expectedOperations = setup + ["aggregate", "ioProc"]
                expectedOperations.append(contentsOf: listenerNames.prefix(installed))
                expectedOperations.append(listener)
                expectedOperations.append(contentsOf: Array(repeating: "removeListener", count: installed))
                expectedOperations.append(contentsOf: ["destroyIOProc", "detach", "destroyAggregate", "destroyTap"])
                expected = expectedOperations
            case "start":
                var expectedOperations = setup + ["aggregate", "ioProc"]
                expectedOperations.append(contentsOf: listenerNames)
                expectedOperations.append("start")
                expectedOperations.append(contentsOf: Array(repeating: "removeListener", count: listenerNames.count))
                expectedOperations.append(contentsOf: ["destroyIOProc", "detach", "destroyAggregate", "destroyTap"])
                expected = expectedOperations
            default:
                XCTFail("unhandled acquisition failure \(failingOperation)")
                continue
            }
            XCTAssertEqual(hal.operations, expected, failingOperation)
        }

        let invalidRingHAL = ProcessTapFakeHAL()
        let invalidRing = ProcessTapSystemAudioSource(
            configuration: try config(),
            hal: invalidRingHAL,
            ringSlotCount: 0
        )
        do { try await invalidRing.start() } catch { }
        XCTAssertEqual(invalidRingHAL.operations.filter { $0 == "destroyTap" }.count, 1)
        XCTAssertFalse(invalidRingHAL.operations.contains("aggregate"))

        let invalidCapacityHAL = ProcessTapFakeHAL()
        let invalidCapacity = ProcessTapSystemAudioSource(
            configuration: try config(),
            hal: invalidCapacityHAL,
            ringSlotCapacity: 0
        )
        do { try await invalidCapacity.start() } catch { }
        XCTAssertEqual(invalidCapacityHAL.operations.filter { $0 == "destroyTap" }.count, 1)
        XCTAssertFalse(invalidCapacityHAL.operations.contains("aggregate"))
    }

    func testCleanupContinuesAfterListenerFailureAndBlocksAutomaticRestart() async throws {
        let hal = ProcessTapFakeHAL()
        hal.failOperation = "removeListener"
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        try await source.start()
        await source.stop()
        XCTAssertNotNil(source.cleanupFailureDiagnostic)
        XCTAssertTrue(hal.operations.contains("destroyIOProc"))
        XCTAssertFalse(hal.operations.contains("destroyAggregate"), "a retained listener token keeps its aggregate identifier owned")
        XCTAssertFalse(hal.operations.contains("destroyTap"), "a retained listener token keeps its tap identifier owned")
        XCTAssertEqual(hal.destroyedAggregateIDs, [])
        XCTAssertEqual(hal.destroyedTapIDs, [])
        do {
            try await source.start()
            XCTFail("persistent cleanup failure must block automatic restart")
        } catch {
            XCTAssertTrue(String(describing: error).contains("liberar"))
        }
    }

    func testCleanupFailureInjectionRetainsOwnershipForRetryWithoutDoubleSuccess() async throws {
        for failingOperation in ["removeListener", "stop", "destroyIOProc", "destroyAggregate", "destroyTap"] {
            let hal = ProcessTapFakeHAL()
            hal.failNextOperation = failingOperation
            let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal, uuidFactory: { "uid-cleanup-\(failingOperation)" })
            try await source.start()
            await source.stop()
            XCTAssertNotNil(source.cleanupFailureDiagnostic, failingOperation)

            await source.stop()

            XCTAssertNil(source.cleanupFailureDiagnostic, failingOperation)
            XCTAssertEqual(hal.removedTokens.count, 5, failingOperation)
            XCTAssertEqual(hal.stopCount, 1, failingOperation)
            XCTAssertEqual(hal.destroyIOProcCount, 1, failingOperation)
            XCTAssertEqual(hal.destroyAggregateCount, 1, failingOperation)
            XCTAssertEqual(hal.destroyTapCount, 1, failingOperation)
            XCTAssertEqual(hal.destroyedAggregateIDs, [101], failingOperation)
            XCTAssertEqual(hal.destroyedTapIDs, [100], failingOperation)

            let removeAttempts = hal.operations.filter { $0 == "removeListener" }.count
            let ioAttempts = hal.operations.filter { $0 == "destroyIOProc" }.count
            let aggregateAttempts = hal.operations.filter { $0 == "destroyAggregate" }.count
            let tapAttempts = hal.operations.filter { $0 == "destroyTap" }.count
            XCTAssertEqual(removeAttempts, failingOperation == "removeListener" ? 6 : 5, failingOperation)
            XCTAssertEqual(ioAttempts, failingOperation == "destroyIOProc" ? 2 : 1, failingOperation)
            XCTAssertEqual(aggregateAttempts, failingOperation == "destroyAggregate" ? 2 : 1, failingOperation)
            XCTAssertEqual(tapAttempts, failingOperation == "destroyTap" ? 2 : 1, failingOperation)
        }
    }

    func testSimultaneousIOProcAndAggregateDestroyFailuresRetainFencedRingAndBlockRestart() async throws {
        let hal = ProcessTapFakeHAL()
        hal.failOperations = ["destroyIOProc", "destroyAggregate"]
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        try await source.start()

        await source.stop()
        XCTAssertNotNil(source.cleanupFailureDiagnostic)
        XCTAssertTrue(source.hasRetainedRealtimeResourceForTesting)
        XCTAssertEqual(hal.destroyIOProcCount, 0)
        XCTAssertEqual(hal.destroyAggregateCount, 0)
        XCTAssertEqual(hal.destroyTapCount, 0)

        do {
            try await source.start()
            XCTFail("a fenced retained ring must block restart")
        } catch {
            XCTAssertEqual(hal.startCount, 1)
        }

        hal.failOperations = []
        await source.stop()
        XCTAssertNil(source.cleanupFailureDiagnostic)
        XCTAssertFalse(source.hasRetainedRealtimeResourceForTesting)
        XCTAssertEqual(hal.destroyIOProcCount, 1)
        XCTAssertEqual(hal.destroyAggregateCount, 1)
        XCTAssertEqual(hal.destroyTapCount, 1)
    }

    func testWatchdogRebuildsOnceAndThenFailsWithoutFallback() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal, uuidFactory: { "uid-watch" }, stopDeadlineNanoseconds: 1_000_000)
        try await source.start()
        await source.triggerWatchdogForTesting(nowNanoseconds: UInt64.max)
        XCTAssertEqual(hal.startCount, 2)
        await source.triggerWatchdogForTesting(nowNanoseconds: UInt64.max)
        if case .failed(let message) = source.status {
            XCTAssertEqual(message, SystemAudioCaptureMonitor.ambiguousCaptureMessage)
        } else {
            XCTFail("persistent no-buffer condition must fail loudly")
        }
        await source.stop()
    }

    func testWatchdogSnapshotsCursorBeforeTearingDownQueuedBuffer() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapOrderedSink()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            sink: sink,
            hal: hal,
            uuidFactory: { "uid-watchdog-cursor" },
            stopDeadlineNanoseconds: 1
        )
        try await source.start()

        let snapshotEntered = DispatchSemaphore(value: 0)
        let releaseSnapshot = DispatchSemaphore(value: 0)
        source.watchdogSnapshotHook = {
            snapshotEntered.signal()
            releaseSnapshot.wait()
        }
        let watchdog = Task { await source.triggerWatchdogForTesting(nowNanoseconds: UInt64.max) }
        XCTAssertEqual(snapshotEntered.wait(timeout: .now() + 1), .success)

        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        XCTAssertEqual(
            source.submitForTestingWithoutMonitorObservation(
                ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)
            ),
            TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY
        )

        releaseSnapshot.signal()
        await watchdog.value
        source.watchdogSnapshotHook = nil
        source.drainPausedForTesting = false

        XCTAssertEqual(hal.startCount, 2)
        XCTAssertTrue(sink.waitForEventCount(1), "the replacement graph must publish the pre-teardown cursor gap")
        XCTAssertEqual(sink.events, [.gap(.unknownEnd)])
        let gap = try XCTUnwrap(sink.gaps.first)
        XCTAssertEqual(gap.firstSequence, 0)
        XCTAssertEqual(gap.firstSample, 0)
        XCTAssertEqual(sink.events.filter { if case .frame = $0 { return true }; return false }.count, 0)
        await source.stop()
    }

    func testRebuildGapAnchorsAtEarliestDiscardedQueuedFrame() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapCapacitySink()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            sink: sink,
            hal: hal,
            stopDeadlineNanoseconds: 1_000_000_000
        )
        try await source.start()
        let fenced = DispatchSemaphore(value: 0)
        source.deliveryFenceHook = { fenced.signal() }
        defer {
            source.deliveryFenceHook = nil
            sink.release()
        }

        let format = ProcessTapPCMFormat(
            sampleRate: 48_000,
            channelCount: 1,
            isFloat: true,
            isInterleaved: true,
            bitsPerChannel: 32,
            bytesPerFrame: 4
        )
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        let buffer = { ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1) }
        for _ in 0..<4 {
            XCTAssertEqual(source.submitForTesting(buffer()), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
            await source.drainForTesting()
        }
        XCTAssertEqual(sink.entered.wait(timeout: .now() + 1), .success)

        let rebuild = Task { await source.triggerEventForTesting(.serviceReset) }
        XCTAssertEqual(fenced.wait(timeout: .now() + 1), .success)
        // The first frame remains in flight, while frames 1-3 have already
        // been converted and queued.  Release only after the worker reports
        // that its queued items were discarded, so the replacement gap must
        // use sequence/sample 1/800 rather than the converter's next 4/3200.
        sink.release()
        await rebuild.value
        XCTAssertTrue(sink.waitForEventCount(2))
        XCTAssertEqual(sink.events, [.frame(0), .gap(.routeLoss, 1, 800)])
        await source.stop()
    }

    func testRebuildAlwaysUsesFreshAggregateUID() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal, uuidFactory: { "uid-reused-by-fixture" })
        try await source.start()
        await source.triggerEventForTesting(.serviceReset)
        XCTAssertEqual(hal.aggregateUIDs.count, 2)
        XCTAssertNotEqual(hal.aggregateUIDs[0], hal.aggregateUIDs[1])
        await source.stop()
    }

    func testAutomaticRebuildBudgetIsSharedByTwoHALEvents() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal, uuidFactory: { "uid-event-budget" })
        try await source.start()
        await source.triggerEventForTesting(.serviceReset)
        XCTAssertEqual(hal.startCount, 2)
        await source.triggerEventForTesting(.tapListChanged)
        XCTAssertEqual(hal.startCount, 2, "the second automatic event must fail instead of starting a third graph")
        guard case .failed(let message) = source.status else {
            return XCTFail("the second automatic event must be a terminal failure")
        }
        XCTAssertTrue(message.contains("recuperação automática"))
        await source.stop()
    }

    func testAutomaticRebuildBudgetIsSharedByEventThenWatchdog() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal, uuidFactory: { "uid-event-watchdog" })
        try await source.start()
        await source.triggerEventForTesting(.serviceReset)
        XCTAssertEqual(hal.startCount, 2)
        await source.triggerWatchdogForTesting(nowNanoseconds: UInt64.max)
        XCTAssertEqual(hal.startCount, 2, "a watchdog after an event rebuild must not start a third graph")
        guard case .failed(let message) = source.status else {
            return XCTFail("event followed by watchdog must terminate after the shared budget is exhausted")
        }
        XCTAssertEqual(message, SystemAudioCaptureMonitor.ambiguousCaptureMessage)
        await source.stop()
    }

    func testConcurrentRecoveryCausesCoalesceWhileOneRebuildIsInFlight() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            hal: hal,
            uuidFactory: { "uid-concurrent-recovery" }
        )
        try await source.start()
        // Keep the first replacement graph in its acquisition phase while the
        // second cause arrives.  The recovery task is already installed, so
        // the second cause must join it rather than consume the budget.
        hal.blockOperation = "translate"
        let first = Task { await source.triggerEventForTesting(.serviceReset) }
        XCTAssertEqual(hal.blockEntered.wait(timeout: .now() + 1), .success)
        let second = Task { await source.triggerEventForTesting(.tapListChanged) }
        hal.blockRelease.signal()
        await first.value
        await second.value

        XCTAssertEqual(hal.startCount, 2, "concurrent causes must share one replacement graph")
        XCTAssertEqual(source.activeLifecycleGeneration, 2)
        if case .failed(let message) = source.status {
            XCTFail("a concurrent cause must not terminal-fail an in-flight rebuild: \(message)")
        }
        await source.stop()
    }

    func testConcurrentTerminalCausesHaveOneEvidenceOwner() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        let updates = UpdateBox()
        _ = source.installHealthObserver { updates.append($0) }
        let fenceCount = InvocationCounter()
        source.deliveryFenceHook = { fenceCount.increment() }
        defer { source.deliveryFenceHook = nil }
        try await source.start()

        let first = Task {
            await source.triggerEventForTesting(.deviceAliveReadFailed(kAudioDevicePermissionsError))
        }
        let second = Task {
            await source.triggerEventForTesting(.deviceAliveReadFailed(kAudioDevicePermissionsError))
        }
        await first.value
        await second.value
        await source.drainForTesting()

        XCTAssertEqual(fenceCount.value, 1, "only the terminal owner may fence and record evidence")
        XCTAssertEqual(
            source.status,
            .failed(SystemAudioCaptureMonitor.permissionDeniedMessage)
        )
        XCTAssertEqual(
            updates.all.filter {
                if case .failed = $0.status { return true }
                return false
            }.count,
            1,
            "losing terminal causes must not publish a second final status"
        )
        XCTAssertEqual(hal.startCount, 1)
        await source.stop()
    }

    func testTeardownOwnerPreventsStaleWaiterFromClearingReplacementTask() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        let race = TeardownRaceFixture()
        let stopClaims = AsyncSignal()
        let ownerEntered = AsyncSignal()
        let ownerRelease = AsyncSignal()
        source.teardownCompletionHook = { owner in race.observe(owner: owner) }
        source.stopClaimHook = { stopClaims.signal() }
        source.stopOwnerPauseHook = {
            ownerEntered.signal()
            _ = await ownerRelease.wait(timeout: 10)
        }
        defer {
            source.teardownCompletionHook = nil
            source.stopClaimHook = nil
            source.stopOwnerPauseHook = nil
        }
        try await source.start()

        // Both callers claim the one shared stop owner before that owner is
        // released to create the lower-level teardown task.  A stale waiter
        // cannot clear or recreate that task after the owner completes.
        let firstStop = Task.detached { await source.stop() }
        let firstOwnerEntered = await ownerEntered.wait()
        XCTAssertEqual(firstOwnerEntered, true)
        let secondStop = Task.detached { await source.stop() }
        let firstClaim = await stopClaims.wait()
        let secondClaim = await stopClaims.wait()
        XCTAssertEqual(firstClaim, true)
        XCTAssertEqual(secondClaim, true)
        XCTAssertNil(source.teardownOwnerForTesting)
        ownerRelease.signal()
        XCTAssertEqual(race.firstWaiterReachedClear.wait(timeout: .now() + 1), .success)
        await firstStop.value
        await secondStop.value
        XCTAssertTrue(source.isIdleForTesting)
        XCTAssertEqual(hal.stopCount, 1)
        XCTAssertEqual(hal.destroyIOProcCount, 1)
        XCTAssertEqual(hal.destroyAggregateCount, 1)
        XCTAssertEqual(hal.destroyTapCount, 1)

        // A replacement start must remain unaffected by any waiter that was
        // attached to the prior owner.  Its subsequent stop owns a distinct
        // teardown token and performs one fresh cleanup set.
        source.stopOwnerPauseHook = nil
        try await source.start()
        let replacementStop = Task.detached { await source.stop() }
        await replacementStop.value
        XCTAssertNil(source.teardownOwnerForTesting)
        XCTAssertEqual(hal.stopCount, 2)
        XCTAssertEqual(hal.destroyIOProcCount, 2)
        XCTAssertEqual(hal.destroyAggregateCount, 2)
        XCTAssertEqual(hal.destroyTapCount, 2)

        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let sourceText = try String(
            contentsOf: packageRoot.appendingPathComponent(
                "Sources/TarsNativeCompanion/ProcessTapSystemAudioSource.swift"
            )
        )
        XCTAssertTrue(sourceText.contains("guard teardownTaskOwner == taskAndOwner.1"))
        let tokenlessMutation = sourceText.replacingOccurrences(
            of: "guard teardownTaskOwner == taskAndOwner.1 else { return }",
            with: "if false { return }"
        )
        XCTAssertFalse(
            tokenlessMutation.contains("guard teardownTaskOwner == taskAndOwner.1 else { return }")
        )
    }

    func testTimestampGapIsDeliveredBeforeFollowingFrame() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapOrderedSink()
        let source = ProcessTapSystemAudioSource(configuration: try config(), sink: sink, hal: hal)
        try await source.start()
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        _ = source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], sampleTime: 0, generation: 1))
        await source.drainForTesting()
        XCTAssertTrue(sink.waitForEventCount(1), "the first converted frame must be observed before the timestamp jump")
        _ = source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], sampleTime: 2_500, generation: 1))
        await source.drainForTesting()
        XCTAssertTrue(sink.waitForEventCount(3), "the timestamp gap and following frame must be observed")
        XCTAssertEqual(sink.events, [.frame(0), .gap(.unknownEnd), .frame(1)])
        await source.stop()
    }

    func testTimestampGapUsesPreConvertCursorBeforeFollowingFrame() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapOrderedSink()
        let source = ProcessTapSystemAudioSource(configuration: try config(), sink: sink, hal: hal)
        try await source.start()
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        _ = source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], sampleTime: 0, generation: 1))
        await source.drainForTesting()
        XCTAssertTrue(sink.waitForEventCount(1), "the first converted frame must be observed before the timestamp jump")
        _ = source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], sampleTime: 2_500, generation: 1))
        await source.drainForTesting()
        XCTAssertTrue(sink.waitForEventCount(3), "the timestamp gap and following frame must be observed")
        XCTAssertEqual(sink.events, [.frame(0), .gap(.unknownEnd), .frame(1)])
        let gap = try XCTUnwrap(sink.gaps.first)
        XCTAssertEqual(gap.firstSequence, 1)
        XCTAssertEqual(gap.firstSample, 800)
        await source.stop()
    }

    func testTapFormatEventFencesQueuedOldGenerationBeforeAsyncRebuild() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapRecordingSink()
        let source = ProcessTapSystemAudioSource(configuration: try config(), sink: sink, hal: hal, uuidFactory: { "uid-format-fence" })
        let replacementRunning = DispatchSemaphore(value: 0)
        let observerToken = source.installHealthObserver { update in
            guard update.generation == 2 else { return }
            if case .running = update.status { replacementRunning.signal() }
        }
        defer { source.removeHealthObserver(observerToken) }
        try await source.start()
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        XCTAssertEqual(source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)

        // The fake invokes the tap-format registration synchronously.  The
        // old slot is therefore fenced before this immediate drain, even
        // though the Swift rebuild is scheduled asynchronously.
        hal.emit(.tapFormatChanged)
        await source.drainForTesting()
        XCTAssertTrue(sink.frames.isEmpty, "a queued old-format slot must be discarded after the synchronous fence")
        XCTAssertEqual(replacementRunning.wait(timeout: .now() + 1), .success)
        XCTAssertEqual(hal.startCount, 2)
        XCTAssertEqual(source.activeLifecycleGeneration, 2)
        await source.stop()
    }

    func testDrainReusesOnePreallocatedScratchBufferAcrossEmptyPolls() async throws {
        final class AllocationBox: @unchecked Sendable {
            let lock = NSLock()
            var count = 0
            func increment() { lock.withLock { count += 1 } }
            var value: Int { lock.withLock { count } }
        }
        let allocations = AllocationBox()
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            hal: hal,
            ringSlotCapacity: 1_048_576,
            drainScratchFactory: { size in
                allocations.increment()
                return Data(count: size)
            }
        )
        XCTAssertEqual(allocations.value, 0, "idle sources must not allocate the 1 MiB drain scratch")
        try await source.start()
        for _ in 0..<8 { await source.drainForTesting() }
        XCTAssertEqual(allocations.value, 1)
        await source.stop()
        XCTAssertFalse(source.hasRetainedRealtimeResourceForTesting)
    }

    func testRingOverflowProducesCausalOverflowGapWithoutWatchdogRebuild() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapOrderedSink()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            liveCaptureEnabled: true,
            sink: sink,
            hal: hal,
            ringSlotCount: 2,
            ringSlotCapacity: 10_000
        )
        try await source.start()
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        XCTAssertEqual(source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertEqual(source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertEqual(source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        await source.drainForTesting()
        XCTAssertTrue(sink.waitForEventCount(3))
        XCTAssertEqual(sink.events, [.frame(0), .frame(1), .gap(.overflow)])
        let gap = try XCTUnwrap(sink.gaps.first)
        XCTAssertEqual(gap.firstSequence, 2)
        XCTAssertEqual(gap.firstSample, 1_600)

        // Audio arriving after the dropped callback belongs after the causal
        // marker, even if it reaches the ring before the next drain turn.
        XCTAssertEqual(source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        await source.drainForTesting()
        XCTAssertTrue(sink.waitForEventCount(4))
        XCTAssertEqual(sink.events, [.frame(0), .frame(1), .gap(.overflow), .frame(2)])
        XCTAssertFalse(sink.events.contains(.gap(.unknownEnd)))
        await source.stop()
    }

    func testMultipleRingOverflowEpisodesKeepOrderedCausalBoundaries() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapOrderedSink()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            sink: sink,
            hal: hal,
            ringSlotCount: 2,
            ringSlotCapacity: 10_000
        )
        try await source.start()
        source.drainPausedForTesting = true
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        let buffer = { ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1) }

        // Episode A drops frame 2 while frames 0 and 1 remain.  Drain only
        // frame 0, then prove that a successful enqueue (frame 3) can occur
        // before episode B drops frame 4.  The two retained-slot boundaries
        // must remain FIFO, so each marker follows its own retained audio.
        XCTAssertEqual(source.submitForTesting(buffer()), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertEqual(source.submitForTesting(buffer()), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertEqual(source.submitForTesting(buffer()), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertTrue(source.drainOneForTesting())
        XCTAssertTrue(sink.waitForEventCount(1))
        XCTAssertEqual(sink.events, [.frame(0)])

        XCTAssertEqual(source.submitForTesting(buffer()), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertEqual(source.submitForTesting(buffer()), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        source.drainPausedForTesting = false
        await source.drainForTesting()

        XCTAssertTrue(sink.waitForEventCount(5))
        XCTAssertEqual(sink.events, [.frame(0), .frame(1), .gap(.overflow), .frame(2), .gap(.overflow)])
        XCTAssertEqual(sink.gaps.map(\.firstSequence), [2, 3])
        XCTAssertEqual(sink.gaps.map(\.firstSample), [1_600, 2_400])
        await source.stop()
    }

    func testSleepThenWakeCoalescesDuplicateRecoveryAndKeepsOneBudget() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            hal: hal,
            uuidFactory: { "uid-sleep-wake" }
        )
        try await source.start()
        XCTAssertEqual(hal.startCount, 1)

        await source.triggerEventForTesting(.sleep)
        guard case .running(let sleepingHealth) = source.status else {
            return XCTFail("sleep must preserve the running lifecycle while marking the health sleeping")
        }
        XCTAssertEqual(sleepingHealth.sleep, .sleeping)

        // Hold the replacement HAL start so the duplicate wake deterministically
        // arrives while the first recovery owns the single rebuild budget.
        hal.blockOperation = "start"
        let firstWake = Task { await source.triggerEventForTesting(.wake) }
        XCTAssertEqual(hal.blockEntered.wait(timeout: .now() + 1), .success)
        let duplicateWake = Task { await source.triggerEventForTesting(.wake) }
        await duplicateWake.value
        hal.blockRelease.signal()
        await firstWake.value

        XCTAssertEqual(hal.startCount, 2)
        XCTAssertEqual(hal.listenerKinds.count, 10, "sleep/wake must replace one graph and re-register every listener once")
        guard case .running(let awakeHealth) = source.status else {
            return XCTFail("a successful wake recovery must return to running")
        }
        XCTAssertEqual(awakeHealth.sleep, .awake)
        await source.triggerEventForTesting(.wake)
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        XCTAssertEqual(
            source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 2)),
            TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY,
            "a duplicate wake must not fence the replacement ring"
        )
        XCTAssertEqual(hal.startCount, 2)
        await source.stop()
    }

    func testSleepWakeListenerQueuePreservesSleepBeforeDelayedWake() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        let sleepEntered = DispatchSemaphore(value: 0)
        let releaseSleep = DispatchSemaphore(value: 0)
        let wakeDispatched = DispatchSemaphore(value: 0)
        let replacementRunning = DispatchSemaphore(value: 0)
        source.sleepWakeDispatchHook = { event in
            if case .sleep = event {
                sleepEntered.signal()
                releaseSleep.wait()
            } else if case .wake = event {
                wakeDispatched.signal()
            }
        }
        let observerToken = source.installHealthObserver { update in
            guard update.generation == 2,
                  case .running(let health) = update.status,
                  health.sleep == .awake else { return }
            replacementRunning.signal()
        }
        defer { source.removeHealthObserver(observerToken) }
        defer { source.sleepWakeDispatchHook = nil }
        try await source.start()

        // The sleep callback's task is deliberately held before it claims
        // sleepingGeneration. The wake callback is then enqueued immediately;
        // FIFO ownership must process sleep first instead of dropping wake.
        hal.emit(.sleep)
        XCTAssertEqual(sleepEntered.wait(timeout: .now() + 1), .success)
        hal.emit(.wake)
        releaseSleep.signal()
        XCTAssertEqual(wakeDispatched.wait(timeout: .now() + 1), .success)
        XCTAssertEqual(replacementRunning.wait(timeout: .now() + 2), .success)
        XCTAssertEqual(hal.startCount, 2)
        guard case .running(let health) = source.status else {
            return XCTFail("ordered sleep/wake delivery must leave the replacement running")
        }
        XCTAssertEqual(health.sleep, .awake)
        await source.stop()
    }

    func testDeliveryOverflowMarksEarliestEvictedFrameAtItsCausalPosition() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapCapacitySink()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            sink: sink,
            hal: hal,
            deliveryQueueCapacity: 3
        )
        try await source.start()
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        defer { sink.release() }

        // Establish the first delivery as a recorded, blocked in-flight
        // invocation before filling the bounded queue.  This is a causal
        // barrier, not a timing guess.
        _ = source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1))
        await source.drainForTesting()
        XCTAssertEqual(sink.entered.wait(timeout: .now() + 1), .success)

        for _ in 0..<4 {
            _ = source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1))
            await source.drainForTesting()
        }
        sink.release()
        XCTAssertTrue(sink.waitForEventCount(4), "the released worker must deliver the exact bounded event sequence")
        XCTAssertEqual(
            sink.events,
            [.frame(0), .gap(.overflow, 1, 800), .frame(3), .frame(4)]
        )
        await source.stop()
    }

    func testFullDeliveryQueuePreservesOverflowBeforeIncomingTimestampGap() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapCapacitySink()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            sink: sink,
            hal: hal,
            deliveryQueueCapacity: 3
        )
        source.drainPausedForTesting = true
        try await source.start()
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        defer { sink.release() }

        func submit(_ sequence: UInt64, sampleTime: Double) {
            XCTAssertEqual(
                source.submitForTesting(
                    ProcessTapPCMBuffer(format: format, buffers: [body], sampleTime: sampleTime, generation: 1)
                ),
                TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY,
                "the bounded queue pressure fixture must accept frame \(sequence)"
            )
        }

        // Prove the first frame has been recorded and is blocked in-flight;
        // all later queue pressure is therefore causally ordered behind it.
        submit(0, sampleTime: 0)
        XCTAssertTrue(source.drainOneForTesting())
        XCTAssertEqual(sink.entered.wait(timeout: .now() + 1), .success)

        // Retain three ordinary frames, then submit a timestamp discontinuity
        // whose converter emits an unknown-end gap before its frame.  The
        // queue is full at the exact point where that incoming gap arrives.
        submit(1, sampleTime: 2_400)
        XCTAssertTrue(source.drainOneForTesting())
        submit(2, sampleTime: 4_800)
        XCTAssertTrue(source.drainOneForTesting())
        submit(3, sampleTime: 7_200)
        XCTAssertTrue(source.drainOneForTesting())
        submit(4, sampleTime: 12_000)
        XCTAssertTrue(source.drainOneForTesting())

        sink.release()
        XCTAssertTrue(sink.waitForEventCount(4), "the released worker must deliver every retained causal boundary")
        XCTAssertEqual(
            sink.events,
            [
                .frame(0),
                .gap(.overflow, 1, 800),
                .gap(.unknownEnd, 4, 3_200),
                .frame(4)
            ],
            "an incoming timestamp gap must follow the overflow marker anchored at the earliest evicted frame"
        )
        await source.stop()
    }

    func testAllGapDeliverySaturationPreservesEarliestEvictedBoundary() throws {
        let identity = try config().identity
        let queue = ProcessTapDeliveryQueue(capacity: 2)
        func makeGap(_ sequence: UInt64, _ sample: UInt64, _ reason: GapReason = .unknownEnd) throws -> CoverageGap {
            try CoverageGap(
                identity: identity,
                firstSample: sample,
                lastSampleExclusive: nil,
                reason: reason,
                firstSequence: sequence,
                deviceID: "ProcessTap.SystemAudio",
                firstCapturedAtMonotonicNs: sequence + 1,
                firstCapturedAtWallClockMs: sequence + 1,
                boundary: .unknownEnd
            )
        }

        queue.enqueue(.gap(try makeGap(0, 0)))
        queue.enqueue(.gap(try makeGap(1, 800)))
        queue.enqueue(.gap(try makeGap(2, 1_600)))

        guard case .gap(let overflow)? = queue.dequeue() else {
            return XCTFail("all-gap saturation must retain a causal overflow marker")
        }
        XCTAssertEqual(overflow.reason, .overflow)
        XCTAssertEqual(overflow.firstSequence, 0)
        XCTAssertEqual(overflow.firstSample, 0)
        guard case .gap(let incoming)? = queue.dequeue() else {
            return XCTFail("the incoming gap must remain after its causal marker")
        }
        XCTAssertEqual(incoming.reason, .unknownEnd)
        XCTAssertEqual(incoming.firstSequence, 2)
        XCTAssertEqual(incoming.firstSample, 1_600)
        XCTAssertNil(queue.dequeue())
    }

    func testAllGapDeliverySaturationPreservesMultipleIdentityBoundaries() throws {
        let identityA = try config(generation: 1).identity
        let identityB = try config(generation: 2).identity
        let identityC = try config(generation: 3).identity
        let queue = ProcessTapDeliveryQueue(capacity: 2)
        func makeGap(_ identity: SourceIdentity, _ sequence: UInt64, _ sample: UInt64) throws -> CoverageGap {
            try CoverageGap(
                identity: identity,
                firstSample: sample,
                lastSampleExclusive: nil,
                reason: .unknownEnd,
                firstSequence: sequence,
                deviceID: "ProcessTap.SystemAudio",
                firstCapturedAtMonotonicNs: sequence + 1,
                firstCapturedAtWallClockMs: sequence + 1,
                boundary: .unknownEnd
            )
        }

        queue.enqueue(.gap(try makeGap(identityA, 100, 80_000)))
        queue.enqueue(.gap(try makeGap(identityB, 0, 0)))
        queue.enqueue(.gap(try makeGap(identityC, 1, 800)))
        XCTAssertLessThanOrEqual(queue.count, 2)

        guard case .gap(let markerA)? = queue.dequeue(),
              case .gap(let markerB)? = queue.dequeue(),
              case .gap(let incomingC)? = queue.dequeue() else {
            return XCTFail("each evicted all-gap identity must retain an ordered boundary")
        }
        XCTAssertEqual(markerA.reason, .overflow)
        XCTAssertEqual(markerA.identity, identityA)
        XCTAssertEqual(markerA.firstSequence, 100)
        XCTAssertEqual(markerB.reason, .overflow)
        XCTAssertEqual(markerB.identity, identityB)
        XCTAssertEqual(markerB.firstSequence, 0)
        XCTAssertEqual(incomingC.identity, identityC)
        XCTAssertEqual(incomingC.reason, .unknownEnd)
        XCTAssertEqual(incomingC.firstSequence, 1)
        XCTAssertNil(queue.dequeue())
    }

    func testCrossGenerationDeliveryQueuePreservesEachIdentityBoundaryWithinCapacity() throws {
        let identityA = try config(generation: 1).identity
        let identityB = try config(generation: 2).identity
        let identityC = try config(generation: 3).identity
        let queue = ProcessTapDeliveryQueue(capacity: 2)
        func makeGap(_ identity: SourceIdentity, _ sequence: UInt64, _ sample: UInt64) throws -> CoverageGap {
            try CoverageGap(
                identity: identity,
                firstSample: sample,
                lastSampleExclusive: nil,
                reason: .unknownEnd,
                firstSequence: sequence,
                deviceID: "ProcessTap.SystemAudio",
                firstCapturedAtMonotonicNs: sequence + 1,
                firstCapturedAtWallClockMs: sequence + 1,
                boundary: .unknownEnd
            )
        }

        // With two physical slots, the third identity cannot receive a
        // third in-queue marker.  The bounded per-identity sidecar must retain
        // the evicted A and B boundaries without comparing their cursors.
        queue.enqueue(.gap(try makeGap(identityA, 100, 80_000)))
        XCTAssertLessThanOrEqual(queue.count, 2)
        queue.enqueue(.gap(try makeGap(identityB, 0, 0)))
        XCTAssertLessThanOrEqual(queue.count, 2)
        queue.enqueue(.gap(try makeGap(identityC, 1, 800)))
        XCTAssertLessThanOrEqual(queue.count, 2)

        guard case .gap(let markerA)? = queue.dequeue(),
              case .gap(let markerB)? = queue.dequeue(),
              case .gap(let incomingC)? = queue.dequeue() else {
            return XCTFail("all cross-generation boundaries must remain deliverable")
        }
        XCTAssertEqual(markerA.reason, .overflow)
        XCTAssertEqual(markerA.identity, identityA)
        XCTAssertEqual(markerA.firstSequence, 100)
        XCTAssertEqual(markerB.reason, .overflow)
        XCTAssertEqual(markerB.identity, identityB)
        XCTAssertEqual(markerB.firstSequence, 0)
        XCTAssertEqual(incomingC.identity, identityC)
        XCTAssertEqual(incomingC.reason, .unknownEnd)
        XCTAssertEqual(incomingC.firstSequence, 1)
        XCTAssertNil(queue.dequeue())
    }

    func testHealthObserverRemovalAndLateGenerationAreFenced() async throws {
        let hal = ProcessTapFakeHAL()
        let source = ProcessTapSystemAudioSource(configuration: try config(), hal: hal)
        let updates = UpdateBox()
        let token = source.installHealthObserver { update in updates.append(update) }
        XCTAssertEqual(updates.count, 1)
        source.removeHealthObserver(token)
        try await source.start()
        let countAfterRemoval = updates.count
        await source.stop()
        XCTAssertEqual(updates.count, countAfterRemoval)
    }

    func testRetiredListenerPermissionCannotFenceReplacementWithReusedGeneration() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapRecordingSink()
        let source = ProcessTapSystemAudioSource(configuration: try config(), sink: sink, hal: hal)
        try await source.start()
        await source.triggerEventForTesting(.serviceReset)
        XCTAssertEqual(source.activeLifecycleGeneration, 2)
        XCTAssertEqual(hal.startCount, 2)

        // A user stop/start reuses configured capture generation 1.  The
        // retired listener's operation token must still prevent it from
        // fencing the healthy replacement ring or poisoning its monitor.
        await source.stop()
        try await source.start()
        XCTAssertEqual(source.activeLifecycleGeneration, 1)
        XCTAssertEqual(hal.startCount, 3)
        hal.emitRetiredDeviceAlivePermission()
        await Task.yield()

        guard case .running = source.status else {
            return XCTFail("a permission event from a retired operation must not terminalize the replacement")
        }
        let format = ProcessTapPCMFormat(
            sampleRate: 48_000,
            channelCount: 1,
            isFloat: true,
            isInterleaved: true,
            bitsPerChannel: 32,
            bytesPerFrame: 4
        )
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        XCTAssertEqual(
            source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)),
            TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY,
            "a retired listener must not fence a replacement ring that reuses generation 1"
        )
        await source.stop()
    }

    func testNonCooperativeSinkIsQuarantinedAndBlocksRestartUntilReleased() async throws {
        let hal = ProcessTapFakeHAL()
        let sink = ProcessTapBlockingSink()
        let source = ProcessTapSystemAudioSource(
            configuration: try config(),
            sink: sink,
            hal: hal,
            uuidFactory: { "uid-blocked" },
            stopDeadlineNanoseconds: 5_000_000
        )
        let quarantineEmpty = DispatchSemaphore(value: 0)
        source.quarantineEmptyHook = { quarantineEmpty.signal() }
        defer { source.quarantineEmptyHook = nil }
        try await source.start()
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let body = floatData(Array(repeating: 0.5, count: 2_400))
        _ = source.submitForTesting(ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1))
        await source.drainForTesting()
        XCTAssertEqual(sink.entered.wait(timeout: .now() + 1), .success)
        await source.stop()
        XCTAssertEqual(sink.invocationCount, 1)

        do {
            try await source.start()
            XCTFail("restart must remain fenced while the sink invocation is quarantined")
        } catch {
            XCTAssertEqual(hal.startCount, 1, "quarantined restart must not acquire a second HAL graph")
        }
        sink.release()
        XCTAssertEqual(sink.finished.wait(timeout: .now() + 1), .success)
        XCTAssertEqual(quarantineEmpty.wait(timeout: .now() + 1), .success)
        try await source.start()
        await source.stop()
        XCTAssertEqual(sink.invocationCount, 1, "releasing the old invocation must not replay it")
    }
}

private extension Array {
    var single: Element? { count == 1 ? first : nil }
}
