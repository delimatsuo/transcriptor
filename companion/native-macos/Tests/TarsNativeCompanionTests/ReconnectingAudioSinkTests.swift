import Foundation
import XCTest
@testable import TarsNativeCompanion

// MARK: - Scripted gateway double

private enum MockGatewayError: Error {
    case connectFailed
    case sendFailed
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
    private var sendCallCount = 0
    private var recorded: [Event] = []
    private var connectAttemptCount = 0
    private var cancelCallCount = 0
    private var pending: [(target: Int, expectation: XCTestExpectation)] = []

    /// - Parameters:
    ///   - connectFailures: how many leading `connect()` calls throw.
    ///   - failingSendCalls: 1-based indices (counting `send` and `sendText`
    ///     together) of the calls that throw instead of recording.
    init(connectFailures: Int = 0, failingSendCalls: Set<Int> = []) {
        self.connectFailuresRemaining = connectFailures
        self.failingSendCalls = failingSendCalls
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

    fileprivate func connect() throws {
        let shouldFail: Bool = lock.withLock {
            connectAttemptCount += 1
            guard connectFailuresRemaining > 0 else { return false }
            connectFailuresRemaining -= 1
            return true
        }
        if shouldFail { throw MockGatewayError.connectFailed }
    }

    fileprivate func record(_ event: Event) throws {
        var fulfilled: [XCTestExpectation] = []
        var shouldFail = false
        lock.lock()
        sendCallCount += 1
        if failingSendCalls.contains(sendCallCount) {
            shouldFail = true
        } else {
            recorded.append(event)
            let count = recorded.count
            fulfilled = pending.filter { $0.target <= count }.map(\.expectation)
            pending.removeAll { $0.target <= count }
        }
        lock.unlock()
        fulfilled.forEach { $0.fulfill() }
        if shouldFail { throw MockGatewayError.sendFailed }
    }

    fileprivate func noteCancel() { lock.withLock { cancelCallCount += 1 } }
}

private struct MockTransport: AudioStreamTransport {
    let gateway: MockGateway

    func connect() async throws { try gateway.connect() }
    func send(_ data: Data) async throws { try gateway.record(.data(data)) }
    func sendText(_ text: String) async throws { try gateway.record(.text(text)) }
    func cancel() { gateway.noteCancel() }
}

/// Records every backoff delay the sink asks for and returns immediately, so
/// reconnect behaviour is asserted on the requested schedule rather than by
/// burning real seconds.
private final class SleepRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var delays: [Double] = []
    private var pending: [(target: Int, expectation: XCTestExpectation)] = []

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

    func record(_ delay: Double) {
        lock.lock()
        delays.append(delay)
        let count = delays.count
        let fulfilled = pending.filter { $0.target <= count }.map(\.expectation)
        pending.removeAll { $0.target <= count }
        lock.unlock()
        fulfilled.forEach { $0.fulfill() }
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
        sleeper: SleepRecorder,
        bufferCapacityFrames: Int = 600
    ) -> ReconnectingAudioSink {
        ReconnectingAudioSink(
            sessionID: sessionID,
            transportFactory: { gateway.makeTransport() },
            bufferCapacityFrames: bufferCapacityFrames,
            reconnectDelaysSeconds: [1, 2, 4, 8, 16, 30],
            sleep: { delay in sleeper.record(delay) }
        )
    }

    /// An outage that fits in the buffer must cost nothing: every frame is
    /// replayed, in capture order, and no gap is claimed.
    func testFramesBufferedDuringOutageAreReplayedInOrder() async throws {
        let gateway = MockGateway(failingSendCalls: [1, 2, 3])
        let sleeper = SleepRecorder()
        let sink = makeSink(gateway: gateway, sleeper: sleeper)

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
        XCTAssertEqual(sleeper.recorded, [1, 2, 4])
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
        let sleeper = SleepRecorder()
        let sink = makeSink(gateway: gateway, sleeper: sleeper, bufferCapacityFrames: 3)

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
        let sleeper = SleepRecorder()
        let sink = makeSink(gateway: gateway, sleeper: sleeper)

        let frames = try makeFrames(identity: systemIdentity(), indices: 1...2)
        for frame in frames { try await sink.receive(frame) }

        let received = gateway.expectEvents(2)
        let delays = sleeper.expectDelays(4)
        sink.start()
        await fulfillment(of: [received, delays], timeout: 5.0)
        await sink.stop()

        XCTAssertEqual(sleeper.recorded, [1, 2, 4, 1], "backoff escalates while down and resets after a good connect")
        let sequences = try gateway.dataEvents.map { try decodePacket($0).sequence }
        XCTAssertEqual(sequences, [1, 2], "the frame in flight when the send failed must be retried, not lost")
        XCTAssertEqual(gateway.connectAttempts, 5, "3 failed connects + 1 good connect + 1 reconnect after the send failure")
    }

    /// The zero-frame advisory in the CLI reads these counters, so they have to
    /// be per source and count only what actually reached the gateway.
    func testFramesSentCounterPerSource() async throws {
        let gateway = MockGateway()
        let sleeper = SleepRecorder()
        let sink = makeSink(gateway: gateway, sleeper: sleeper)

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
        let sleeper = SleepRecorder()
        let sink = makeSink(gateway: gateway, sleeper: sleeper)

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
        let sleeper = SleepRecorder()
        let sink = makeSink(gateway: gateway, sleeper: sleeper)

        let delays = sleeper.expectDelays(3)
        sink.start()
        await fulfillment(of: [delays], timeout: 5.0)
        await sink.stop()

        XCTAssertFalse(sink.isConnected)
        XCTAssertEqual(gateway.dataEvents.count, 0)
    }
}
