import Foundation
import XCTest
@testable import TarsNativeCompanion

// MARK: - Scripted gateway double

private enum MockGatewayError: Error {
    case connectFailed
    case sendFailed
    case cancelledWhileWaiting
}

/// Suspends callers until `open()`. Stands in for "this socket operation has
/// not come back yet" — the half-open-connection case — with the moment of
/// completion under the test's control instead of a wall clock's.
private final class Gate: @unchecked Sendable {
    private let lock = NSLock()
    private var isOpen = false
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private var entries = 0
    private var pending: [(target: Int, expectation: XCTestExpectation)] = []

    func open() {
        lock.lock()
        isOpen = true
        let resuming = waiters
        waiters.removeAll()
        lock.unlock()
        resuming.forEach { $0.resume() }
    }

    /// Resolves once `count` callers have *entered* the gate — i.e. the sink is
    /// provably parked inside the operation, not merely about to be.
    func expectEntries(_ count: Int) -> XCTestExpectation {
        let expectation = XCTestExpectation(description: "gate entered \(count) time(s)")
        lock.lock()
        let reached = entries >= count
        if !reached { pending.append((target: count, expectation: expectation)) }
        lock.unlock()
        if reached { expectation.fulfill() }
        return expectation
    }

    func wait() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            lock.lock()
            entries += 1
            let reached = entries
            let fulfilled = pending.filter { $0.target <= reached }.map(\.expectation)
            pending.removeAll { $0.target <= reached }
            let passThrough = isOpen
            if !passThrough { waiters.append(continuation) }
            lock.unlock()
            fulfilled.forEach { $0.fulfill() }
            if passThrough { continuation.resume() }
        }
    }
}

/// Shared script and recorder behind every `MockTransport` the sink builds.
/// The sink asks `transportFactory()` for a brand-new transport on every
/// (re)connect, so the failure script and the ordered record of what actually
/// reached the wire have to outlive any single transport instance.
private final class MockGateway: @unchecked Sendable {
    enum Event: Equatable {
        case data(Data)
        case text(String)
    }

    private let lock = NSLock()
    private var connectFailuresRemaining: Int
    private let failingSendCalls: Set<Int>
    private let gatedConnectCalls: Set<Int>
    private let gatedSendCalls: Set<Int>
    private let gate = Gate()
    private var sendCallCount = 0
    private var recorded: [Event] = []
    private var connectAttemptCount = 0
    private var cancelCallCount = 0
    private var pending: [(target: Int, expectation: XCTestExpectation)] = []

    /// - Parameters:
    ///   - connectFailures: how many leading `connect()` calls throw.
    ///   - failingSendCalls: 1-based indices (counting `send` and `sendText`
    ///     together) of the calls that throw instead of recording.
    ///   - gatedConnectCalls: connect attempts that hang until `openGate()`,
    ///     then fail — a connection that never completes.
    ///   - gatedSendCalls: send calls that hang until `openGate()` and then
    ///     succeed — a wedged socket whose write lands long after the sink gave
    ///     up on it.
    init(
        connectFailures: Int = 0,
        failingSendCalls: Set<Int> = [],
        gatedConnectCalls: Set<Int> = [],
        gatedSendCalls: Set<Int> = []
    ) {
        self.connectFailuresRemaining = connectFailures
        self.failingSendCalls = failingSendCalls
        self.gatedConnectCalls = gatedConnectCalls
        self.gatedSendCalls = gatedSendCalls
    }

    var events: [Event] { lock.withLock { recorded } }

    var dataEvents: [Data] {
        events.compactMap { event in
            guard case .data(let data) = event else { return nil }
            return data
        }
    }

    var textEvents: [String] {
        events.compactMap { event in
            guard case .text(let text) = event else { return nil }
            return text
        }
    }

    var connectAttempts: Int { lock.withLock { connectAttemptCount } }
    var cancelCount: Int { lock.withLock { cancelCallCount } }

    func makeTransport() -> AudioStreamTransport { MockTransport(gateway: self) }

    func openGate() { gate.open() }

    func expectGateEntries(_ count: Int) -> XCTestExpectation { gate.expectEntries(count) }

    /// Deterministic hook: resolves as soon as `count` events have landed,
    /// instead of guessing at wall-clock delays.
    func expectEvents(_ count: Int) -> XCTestExpectation {
        let expectation = XCTestExpectation(description: "gateway received \(count) event(s)")
        lock.lock()
        let reached = recorded.count >= count
        if !reached { pending.append((target: count, expectation: expectation)) }
        lock.unlock()
        if reached { expectation.fulfill() }
        return expectation
    }

    fileprivate func connect() async throws {
        let attempt: Int = lock.withLock {
            connectAttemptCount += 1
            return connectAttemptCount
        }
        if gatedConnectCalls.contains(attempt) {
            await gate.wait()
            throw MockGatewayError.cancelledWhileWaiting
        }
        let shouldFail: Bool = lock.withLock {
            guard connectFailuresRemaining > 0 else { return false }
            connectFailuresRemaining -= 1
            return true
        }
        if shouldFail { throw MockGatewayError.connectFailed }
    }

    fileprivate func record(_ event: Event) async throws {
        let call: Int = lock.withLock {
            sendCallCount += 1
            return sendCallCount
        }
        if failingSendCalls.contains(call) { throw MockGatewayError.sendFailed }
        if gatedSendCalls.contains(call) { await gate.wait() }

        let fulfilled: [XCTestExpectation] = lock.withLock {
            recorded.append(event)
            let count = recorded.count
            let matched = pending.filter { $0.target <= count }.map(\.expectation)
            pending.removeAll { $0.target <= count }
            return matched
        }
        fulfilled.forEach { $0.fulfill() }
    }

    fileprivate func noteCancel() { lock.withLock { cancelCallCount += 1 } }
}

private struct MockTransport: AudioStreamTransport {
    let gateway: MockGateway

    func connect() async throws { try await gateway.connect() }
    func send(_ data: Data) async throws { try await gateway.record(.data(data)) }
    func sendText(_ text: String) async throws { try await gateway.record(.text(text)) }
    func cancel() { gateway.noteCancel() }
}

/// A virtual clock standing in for the sink's injected `sleep`.
///
/// Durations are routed by value, which is what makes deadline behaviour
/// testable with no wall clock and no races:
/// - `parking`: never returns on its own, so that deadline arm can never win
///   (it unparks only when the sink cancels it, which is what happens when the
///   operation completes first).
/// - `deadline`: parks until the test calls `fireDeadlines()`, so a timeout
///   happens exactly when the test says so — never as a coin flip against an
///   operation that was about to succeed.
/// - anything else: a reconnect backoff, recorded and returned at once.
private final class TestClock: @unchecked Sendable {
    private let parking: Set<Double>
    private let deadline: Set<Double>
    private let lock = NSLock()
    private var delays: [Double] = []
    private var pending: [(target: Int, expectation: XCTestExpectation)] = []
    private var parked: [Int: (continuation: CheckedContinuation<Void, Never>, isDeadline: Bool)] = [:]
    private var cancelledParks: Set<Int> = []
    private var deadlineParks = 0
    private var deadlinePending: [(target: Int, expectation: XCTestExpectation)] = []
    private var nextParkID = 0

    init(parking: Set<Double> = [], deadline: Set<Double> = []) {
        self.parking = parking
        self.deadline = deadline
    }

    /// Reconnect backoff delays only — deadlines are routed, never recorded.
    var recorded: [Double] { lock.withLock { delays } }

    func expectDelays(_ count: Int) -> XCTestExpectation {
        let expectation = XCTestExpectation(description: "sink requested \(count) backoff delay(s)")
        lock.lock()
        let reached = delays.count >= count
        if !reached { pending.append((target: count, expectation: expectation)) }
        lock.unlock()
        if reached { expectation.fulfill() }
        return expectation
    }

    /// Resolves once `count` deadline arms are provably parked, which is the
    /// safe moment to fire them.
    func expectDeadlineParks(_ count: Int) -> XCTestExpectation {
        let expectation = XCTestExpectation(description: "\(count) deadline arm(s) armed")
        lock.lock()
        let reached = deadlineParks >= count
        if !reached { deadlinePending.append((target: count, expectation: expectation)) }
        lock.unlock()
        if reached { expectation.fulfill() }
        return expectation
    }

    /// One-shot: expires every deadline currently armed. Arms created later
    /// park again, so a single call cannot cascade into later operations.
    func fireDeadlines() {
        lock.lock()
        let firing = parked.filter { $0.value.isDeadline }
        for key in firing.keys { parked.removeValue(forKey: key) }
        lock.unlock()
        firing.values.forEach { $0.continuation.resume() }
    }

    func sleep(_ seconds: Double) async {
        if parking.contains(seconds) {
            await park(isDeadline: false)
            return
        }
        if deadline.contains(seconds) {
            await park(isDeadline: true)
            return
        }
        record(seconds)
    }

    private func record(_ delay: Double) {
        lock.lock()
        delays.append(delay)
        let count = delays.count
        let fulfilled = pending.filter { $0.target <= count }.map(\.expectation)
        pending.removeAll { $0.target <= count }
        lock.unlock()
        fulfilled.forEach { $0.fulfill() }
    }

    private func park(isDeadline: Bool) async {
        let id: Int = lock.withLock {
            nextParkID += 1
            return nextParkID
        }
        await withTaskCancellationHandler {
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                lock.lock()
                // Cancellation may already have arrived before the continuation
                // was parked; consuming the flag closes that race.
                let alreadyCancelled = cancelledParks.remove(id) != nil
                var fulfilled: [XCTestExpectation] = []
                if !alreadyCancelled {
                    parked[id] = (continuation, isDeadline)
                    if isDeadline {
                        deadlineParks += 1
                        let count = deadlineParks
                        fulfilled = deadlinePending.filter { $0.target <= count }.map(\.expectation)
                        deadlinePending.removeAll { $0.target <= count }
                    }
                }
                lock.unlock()
                fulfilled.forEach { $0.fulfill() }
                if alreadyCancelled { continuation.resume() }
            }
        } onCancel: {
            lock.lock()
            let entry = parked.removeValue(forKey: id)
            if entry == nil { cancelledParks.insert(id) }
            lock.unlock()
            entry?.continuation.resume()
        }
    }
}

// MARK: - Wire-format decoding (independent of the sink's encoder)

private struct DecodedPacket {
    let header: [String: Any]
    let payload: Data

    var sequence: UInt64? { (header["sequence"] as? NSNumber)?.uint64Value }
    var source: String? { header["source"] as? String }
    var sessionID: String? { header["session_id"] as? String }
}

private func decodePacket(_ data: Data) throws -> DecodedPacket {
    let bytes = [UInt8](data)
    guard bytes.count >= 4 else {
        throw MockGatewayError.sendFailed
    }
    let headerLength = Int(UInt32(bytes[0]) << 24 | UInt32(bytes[1]) << 16 | UInt32(bytes[2]) << 8 | UInt32(bytes[3]))
    guard bytes.count >= 4 + headerLength else {
        throw MockGatewayError.sendFailed
    }
    let headerData = Data(bytes[4..<(4 + headerLength)])
    let payload = Data(bytes[(4 + headerLength)...])
    let header = try JSONSerialization.jsonObject(with: headerData) as? [String: Any] ?? [:]
    return DecodedPacket(header: header, payload: payload)
}

private func decodeJSONObject(_ text: String) throws -> [String: Any] {
    let data = Data(text.utf8)
    return try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
}

// MARK: - Tests

final class ReconnectingAudioSinkTests: XCTestCase {
    private let sessionID = "sess-6"
    /// Deliberately distinct from each other and from the backoff ladder
    /// ([1,2,4,8,16,30]) so the virtual clock can tell the three kinds of sleep
    /// apart by duration alone.
    private let connectTimeout = 7.0
    private let sendTimeout = 9.0

    private func systemIdentity() throws -> SourceIdentity {
        try SourceIdentity(
            sessionID: sessionID,
            streamID: "system",
            captureGeneration: 1,
            source: .systemAudio,
            sampleRate: 16_000,
            channelCount: 1
        )
    }

    private func microphoneIdentity() throws -> SourceIdentity {
        try SourceIdentity(
            sessionID: sessionID,
            streamID: "mic",
            captureGeneration: 1,
            source: .microphone,
            sampleRate: 16_000,
            channelCount: 1
        )
    }

    /// 50 ms frames at 16 kHz mono — the same shape the live capture sources emit.
    private func makeFrames(identity: SourceIdentity, indices: ClosedRange<Int>) throws -> [AudioFrame] {
        let fixture = try GeneratedFixtureSource(identity: identity, frameDurationMs: 50)
        return try indices.map { try fixture.makeFrame(index: $0) }
    }

    private func makeSink(
        gateway: MockGateway,
        clock: TestClock,
        bufferCapacityFrames: Int = 600,
        intendedSources: [AudioSource] = []
    ) -> ReconnectingAudioSink {
        ReconnectingAudioSink(
            sessionID: sessionID,
            transportFactory: { gateway.makeTransport() },
            bufferCapacityFrames: bufferCapacityFrames,
            reconnectDelaysSeconds: [1, 2, 4, 8, 16, 30],
            connectTimeoutSeconds: connectTimeout,
            sendTimeoutSeconds: sendTimeout,
            sleep: { delay in await clock.sleep(delay) },
            intendedSources: intendedSources
        )
    }

    /// Both deadlines parked: they can never fire, so these tests observe the
    /// reconnect behaviour alone.
    private func steadyClock() -> TestClock {
        TestClock(parking: [connectTimeout, sendTimeout])
    }

    /// An outage that fits in the buffer must cost nothing: every frame is
    /// replayed, in capture order, and no gap is claimed.
    func testFramesBufferedDuringOutageAreReplayedInOrder() async throws {
        let gateway = MockGateway(failingSendCalls: [1, 2, 3])
        let clock = steadyClock()
        let sink = makeSink(gateway: gateway, clock: clock)

        let frames = try makeFrames(identity: systemIdentity(), indices: 1...5)
        for frame in frames { try await sink.receive(frame) }

        let received = gateway.expectEvents(5)
        sink.start()
        await fulfillment(of: [received], timeout: 5.0)
        await sink.stop()

        let sequences = try gateway.dataEvents.map { try decodePacket($0).sequence }
        XCTAssertEqual(sequences, [1, 2, 3, 4, 5], "buffered frames must replay in capture order with no loss")
        XCTAssertEqual(gateway.textEvents, [], "nothing was dropped, so the sink must not claim a gap")
        XCTAssertEqual(sink.framesSent(for: .systemAudio), 5)

        // Three failed sends means three reconnects, each behind its backoff step.
        XCTAssertEqual(clock.recorded, [1, 2, 4])
        XCTAssertGreaterThanOrEqual(gateway.cancelCount, 3, "each dead transport must be cancelled, not leaked")

        let first = try decodePacket(XCTUnwrap(gateway.dataEvents.first))
        XCTAssertEqual(first.sessionID, sessionID)
        XCTAssertEqual(first.source, "system_audio")
        XCTAssertEqual((first.header["sample_rate"] as? NSNumber)?.intValue, 16_000)
        XCTAssertEqual((first.header["channel_count"] as? NSNumber)?.intValue, 1)
        XCTAssertEqual((first.header["duration_ms"] as? NSNumber)?.uint64Value, 50)
        XCTAssertEqual(first.payload, frames[0].payload.copyData(), "PCM payload must survive the buffer verbatim")
    }

    /// An outage longer than the buffer is a real loss: the oldest frames go,
    /// and exactly one honest gap report follows the first successful send.
    func testOverflowDropsOldestAndEmitsSingleGapPerRun() async throws {
        let gateway = MockGateway()
        let clock = steadyClock()
        let sink = makeSink(gateway: gateway, clock: clock, bufferCapacityFrames: 3)

        // The sender is not running yet, so all five frames pile into the
        // bounded buffer exactly as they would during a gateway outage.
        let frames = try makeFrames(identity: systemIdentity(), indices: 1...5)
        for frame in frames { try await sink.receive(frame) }
        XCTAssertEqual(sink.bufferedItemCount, 3, "buffer must stay bounded at its capacity")

        let received = gateway.expectEvents(4)
        sink.start()
        await fulfillment(of: [received], timeout: 5.0)
        await sink.stop()

        let sequences = try gateway.dataEvents.map { try decodePacket($0).sequence }
        XCTAssertEqual(sequences, [3, 4, 5], "the oldest frames are the ones dropped")

        XCTAssertEqual(gateway.textEvents.count, 1, "one drop-run must produce exactly one gap report")
        let gap = try decodeJSONObject(XCTUnwrap(gateway.textEvents.first))
        XCTAssertEqual(gap["type"] as? String, "gap")
        XCTAssertEqual(gap["source"] as? String, "system_audio")
        XCTAssertEqual(gap["reason"] as? String, "buffer_exhaustion")
        XCTAssertEqual((gap["first_sample"] as? NSNumber)?.uint64Value, frames[0].firstSample)

        // The gap has to arrive right after the send that reopened the stream.
        guard gateway.events.count >= 2, case .data = gateway.events[0], case .text = gateway.events[1] else {
            return XCTFail("expected the gap report immediately after the first successful send")
        }
        XCTAssertEqual(sink.framesSent(for: .systemAudio), 3, "dropped frames were never sent")
    }

    /// Backoff escalates while the gateway stays down and resets once it comes
    /// back, so a later blip does not inherit a 30 s wait.
    func testBackoffSequenceAndResetOnSuccess() async throws {
        // First connect attempt is immediate; the next three fail and are
        // spaced 1, 2, 4 s apart. Send #2 then fails, forcing one more
        // reconnect whose delay must have reset to the first step.
        let gateway = MockGateway(connectFailures: 3, failingSendCalls: [2])
        let clock = steadyClock()
        let sink = makeSink(gateway: gateway, clock: clock)

        let frames = try makeFrames(identity: systemIdentity(), indices: 1...2)
        for frame in frames { try await sink.receive(frame) }

        let received = gateway.expectEvents(2)
        let delays = clock.expectDelays(4)
        sink.start()
        await fulfillment(of: [received, delays], timeout: 5.0)
        await sink.stop()

        XCTAssertEqual(clock.recorded, [1, 2, 4, 1], "backoff escalates while down and resets after a good connect")
        let sequences = try gateway.dataEvents.map { try decodePacket($0).sequence }
        XCTAssertEqual(sequences, [1, 2], "the frame in flight when the send failed must be retried, not lost")
        XCTAssertEqual(gateway.connectAttempts, 5, "3 failed connects + 1 good connect + 1 reconnect after the send failure")
    }

    /// The zero-frame advisory in the CLI reads these counters, so they have to
    /// be per source and count only what actually reached the gateway.
    func testFramesSentCounterPerSource() async throws {
        let gateway = MockGateway()
        let clock = steadyClock()
        let sink = makeSink(gateway: gateway, clock: clock)

        let systemFrames = try makeFrames(identity: systemIdentity(), indices: 1...2)
        let micFrames = try makeFrames(identity: microphoneIdentity(), indices: 1...1)

        XCTAssertEqual(sink.framesSent(for: .systemAudio), 0)

        for frame in systemFrames { try await sink.receive(frame) }
        for frame in micFrames { try await sink.receive(frame) }

        let received = gateway.expectEvents(3)
        sink.start()
        await fulfillment(of: [received], timeout: 5.0)
        await sink.stop()

        XCTAssertEqual(sink.framesSent(for: .systemAudio), 2)
        XCTAssertEqual(sink.framesSent(for: .microphone), 1)

        let sources = try gateway.dataEvents.map { try decodePacket($0).source }
        XCTAssertEqual(sources, ["system_audio", "system_audio", "microphone"])
    }

    /// Capture-side gaps (route loss, overflow in the capture layer) ride the
    /// same ordered queue as audio instead of racing past it.
    func testCaptureGapsAreForwardedAsTextInOrder() async throws {
        let gateway = MockGateway()
        let clock = steadyClock()
        let sink = makeSink(gateway: gateway, clock: clock)

        let identity = try systemIdentity()
        let frames = try makeFrames(identity: identity, indices: 1...1)
        try await sink.receive(frames[0])
        try await sink.receiveGap(CoverageGap(
            identity: identity,
            firstSample: 12_345,
            lastSampleExclusive: nil,
            reason: .routeLoss,
            firstSequence: 7,
            deviceID: "ScreenCaptureKit.SystemAudio",
            firstCapturedAtMonotonicNs: 1_000,
            firstCapturedAtWallClockMs: 2_000
        ))

        let received = gateway.expectEvents(2)
        sink.start()
        await fulfillment(of: [received], timeout: 5.0)
        await sink.stop()

        guard gateway.events.count == 2, case .data = gateway.events[0], case .text(let text) = gateway.events[1] else {
            return XCTFail("expected the capture gap to follow the frame it came after")
        }
        let gap = try decodeJSONObject(text)
        XCTAssertEqual(gap["type"] as? String, "gap")
        XCTAssertEqual(gap["source"] as? String, "system_audio")
        XCTAssertEqual(gap["reason"] as? String, "route_loss")
        XCTAssertEqual((gap["first_sample"] as? NSNumber)?.uint64Value, 12_345)
    }

    /// `stop()` must return even when the sink never managed to connect, i.e.
    /// while the sender task is parked in a reconnect backoff.
    func testStopReturnsWhileDisconnected() async throws {
        let gateway = MockGateway(connectFailures: Int.max)
        let clock = steadyClock()
        let sink = makeSink(gateway: gateway, clock: clock)

        let delays = clock.expectDelays(3)
        sink.start()
        await fulfillment(of: [delays], timeout: 5.0)
        await sink.stop()

        XCTAssertFalse(sink.isConnected)
        XCTAssertEqual(gateway.dataEvents.count, 0)
    }

    /// The dangerous outage is not a refused connection — it is a half-open one
    /// (laptop sleep, Wi-Fi hand-off) where the send simply never comes back.
    /// Without a deadline the sender parks forever and the buffer starves.
    func testSendThatNeverCompletesTimesOutAndIsRetriedWithoutDoubleCounting() async throws {
        let gateway = MockGateway(gatedSendCalls: [1])
        let clock = TestClock(parking: [connectTimeout], deadline: [sendTimeout])
        let sink = makeSink(gateway: gateway, clock: clock)

        let frames = try makeFrames(identity: systemIdentity(), indices: 1...1)
        try await sink.receive(frames[0])

        let wedged = gateway.expectGateEntries(1)
        let armed = clock.expectDeadlineParks(1)
        let retried = gateway.expectEvents(1)
        sink.start()

        // Send #1 is now genuinely stuck with its deadline armed — the
        // half-open-socket state. Expire it.
        await fulfillment(of: [wedged, armed], timeout: 5.0)
        clock.fireDeadlines()

        // The frame is re-queued and the reconnect delivers it over a fresh
        // transport, whose own deadline is armed but never fired.
        await fulfillment(of: [retried], timeout: 5.0)

        XCTAssertEqual(clock.recorded, [1], "a timeout must fall through to the normal reconnect backoff")
        XCTAssertEqual(gateway.connectAttempts, 2)
        XCTAssertGreaterThanOrEqual(gateway.cancelCount, 1, "the wedged transport must be cancelled")

        // Now let the abandoned write finally land, long after we gave up on it.
        gateway.openGate()
        let lateArrival = gateway.expectEvents(2)
        await fulfillment(of: [lateArrival], timeout: 5.0)
        await sink.stop()

        let sequences = try gateway.dataEvents.map { try decodePacket($0).sequence }
        XCTAssertEqual(sequences, [1, 1], "the retry and the late write both reach the gateway")
        XCTAssertEqual(
            sink.framesSent(for: .systemAudio),
            1,
            "a late completion must not be counted as a second delivery"
        )
    }

    /// Same failure mode on the way in: a connect that never resolves must not
    /// pin the sender forever.
    func testConnectThatNeverCompletesTimesOutAndBacksOff() async throws {
        let gateway = MockGateway(gatedConnectCalls: [1])
        let clock = TestClock(parking: [sendTimeout], deadline: [connectTimeout])
        let sink = makeSink(gateway: gateway, clock: clock)

        let frames = try makeFrames(identity: systemIdentity(), indices: 1...1)
        try await sink.receive(frames[0])

        let wedged = gateway.expectGateEntries(1)
        let armed = clock.expectDeadlineParks(1)
        let delivered = gateway.expectEvents(1)
        sink.start()

        // Connect #1 hangs with its deadline armed; expiring it must fall
        // through to the backoff rather than pin the sender.
        await fulfillment(of: [wedged, armed], timeout: 5.0)
        clock.fireDeadlines()

        await fulfillment(of: [delivered], timeout: 5.0)
        gateway.openGate()
        await sink.stop()

        XCTAssertEqual(clock.recorded, [1], "the timed-out connect is followed by the first backoff step")
        XCTAssertEqual(gateway.connectAttempts, 2)
        XCTAssertEqual(sink.framesSent(for: .systemAudio), 1)
        XCTAssertGreaterThanOrEqual(gateway.cancelCount, 1, "the wedged transport must be cancelled, not leaked")
    }

    /// The centerpiece of the pop-before-send design: a frame that is in flight
    /// is out of the buffer, so producers overflowing the buffer underneath it
    /// can never make the sink claim a gap for audio that was in fact delivered.
    func testConcurrentOverflowNeverGapsAnInFlightFrame() async throws {
        let gateway = MockGateway(gatedSendCalls: [1])
        let clock = steadyClock()
        let sink = makeSink(gateway: gateway, clock: clock, bufferCapacityFrames: 2)

        let frames = try makeFrames(identity: systemIdentity(), indices: 1...4)
        try await sink.receive(frames[0])
        try await sink.receive(frames[1])

        let sendStarted = gateway.expectGateEntries(1)
        sink.start()
        await fulfillment(of: [sendStarted], timeout: 5.0)

        // Frame 1 is now genuinely in flight. Overflow the buffer under it.
        try await sink.receive(frames[2])
        try await sink.receive(frames[3])

        let all = gateway.expectEvents(4)
        gateway.openGate()
        await fulfillment(of: [all], timeout: 5.0)
        await sink.stop()

        let sequences = try gateway.dataEvents.map { try decodePacket($0).sequence }
        XCTAssertEqual(sequences, [1, 3, 4], "the in-flight frame is delivered; only frame 2 was actually dropped")

        XCTAssertEqual(gateway.textEvents.count, 1)
        let gap = try decodeJSONObject(XCTUnwrap(gateway.textEvents.first))
        XCTAssertEqual(gap["reason"] as? String, "buffer_exhaustion")
        XCTAssertEqual(
            (gap["first_sample"] as? NSNumber)?.uint64Value,
            frames[1].firstSample,
            "the gap must point at frame 2 — never at the in-flight frame 1"
        )
        XCTAssertEqual(sink.framesSent(for: .systemAudio), 3)
    }

    func testHelloIsFirstApplicationMessageAndCanonicalizesSources() async throws {
        let gateway = MockGateway()
        let clock = steadyClock()
        let sink = makeSink(
            gateway: gateway,
            clock: clock,
            intendedSources: [.systemAudio, .microphone, .systemAudio]
        )

        let frames = try makeFrames(identity: systemIdentity(), indices: 1...1)
        try await sink.receive(frames[0])

        let delivered = gateway.expectEvents(2)
        sink.start()
        await fulfillment(of: [delivered], timeout: 5.0)
        await sink.stop()

        XCTAssertEqual(gateway.events.count, 2)
        guard case .text(let helloText) = gateway.events[0] else {
            XCTFail("Expected first event to be hello text")
            return
        }
        let helloJSON = try decodeJSONObject(helloText)
        XCTAssertEqual(helloJSON["type"] as? String, "hello")
        XCTAssertEqual(helloJSON["sources"] as? [String], ["microphone", "system_audio"])

        guard case .data(let frameData) = gateway.events[1] else {
            XCTFail("Expected second event to be frame data")
            return
        }
        let seq = try decodePacket(frameData).sequence
        XCTAssertEqual(seq, 1)
        XCTAssertEqual(sink.framesSent(for: .systemAudio), 1)
    }

    func testEveryReconnectSendsHelloBeforeRetriedFrame() async throws {
        // Call 1 is hello (succeeds), Call 2 is frame 1 (fails).
        // On reconnect, Call 3 is hello (succeeds), Call 4 is frame 1 (succeeds).
        let gateway = MockGateway(failingSendCalls: [2])
        let clock = steadyClock()
        let sink = makeSink(
            gateway: gateway,
            clock: clock,
            intendedSources: [.systemAudio]
        )

        let frames = try makeFrames(identity: systemIdentity(), indices: 1...1)
        try await sink.receive(frames[0])

        let delivered = gateway.expectEvents(3)
        sink.start()
        await fulfillment(of: [delivered], timeout: 5.0)
        await sink.stop()

        XCTAssertEqual(gateway.events.count, 3)
        guard case .text(let hello1Text) = gateway.events[0],
              case .text(let hello2Text) = gateway.events[1],
              case .data(let frameData) = gateway.events[2] else {
            XCTFail("Expected event order: hello, hello, data")
            return
        }
        let h1 = try decodeJSONObject(hello1Text)
        let h2 = try decodeJSONObject(hello2Text)
        XCTAssertEqual(h1["type"] as? String, "hello")
        XCTAssertEqual(h2["type"] as? String, "hello")
        XCTAssertEqual(try decodePacket(frameData).sequence, 1)
        XCTAssertEqual(gateway.connectAttempts, 2)
        XCTAssertEqual(clock.recorded, [1], "Standard first backoff should be used on frame failure")
        XCTAssertEqual(sink.framesSent(for: .systemAudio), 1)
    }

    func testFailedHelloRetriesWithoutDequeuingFrame() async throws {
        // Call 1 is hello (fails).
        // On reconnect, Call 2 is hello (succeeds), Call 3 is frame 1 (succeeds).
        let gateway = MockGateway(failingSendCalls: [1])
        let clock = steadyClock()
        let sink = makeSink(
            gateway: gateway,
            clock: clock,
            intendedSources: [.systemAudio]
        )

        let frames = try makeFrames(identity: systemIdentity(), indices: 1...1)
        try await sink.receive(frames[0])

        let delivered = gateway.expectEvents(2)
        sink.start()
        await fulfillment(of: [delivered], timeout: 5.0)
        await sink.stop()

        XCTAssertEqual(gateway.events.count, 2)
        guard case .text(let helloText) = gateway.events[0],
              case .data(let frameData) = gateway.events[1] else {
            XCTFail("Expected event order: hello, data")
            return
        }
        let h = try decodeJSONObject(helloText)
        XCTAssertEqual(h["type"] as? String, "hello")
        XCTAssertEqual(try decodePacket(frameData).sequence, 1)
        XCTAssertEqual(gateway.connectAttempts, 2)
        XCTAssertEqual(sink.framesSent(for: .systemAudio), 1)
    }
}
