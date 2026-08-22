import Foundation
import XCTest
@testable import TarsNativeCompanion

/// Records the order in which a sink is handed work. `receive` suspends before
/// recording: without a real suspension point, an unordered producer could pass
/// this test by accident.
private final class RecordingSink: CaptureFrameSink, @unchecked Sendable {
    enum Observation: Equatable {
        case frame(UInt64)
        case gap(UInt64)
    }

    private let lock = NSLock()
    private var recorded: [Observation] = []

    var observations: [Observation] { lock.withLock { recorded } }

    func receive(_ frame: AudioFrame) async throws {
        await Task.yield()
        lock.withLock { recorded.append(.frame(frame.sequence)) }
    }

    func receiveGap(_ gap: CoverageGap) async throws {
        await Task.yield()
        lock.withLock { recorded.append(.gap(gap.firstSequence ?? 0)) }
    }
}

final class OrderedFrameRelayTests: XCTestCase {
    private func identity() throws -> SourceIdentity {
        try SourceIdentity(
            sessionID: "sess-relay",
            streamID: "system",
            captureGeneration: 1,
            source: .systemAudio,
            sampleRate: 16_000,
            channelCount: 1
        )
    }

    /// 50 ms of mono 16 kHz PCM, the shape both capture sources emit.
    private func makeFrame(identity: SourceIdentity, sequence: UInt64) throws -> AudioFrame {
        let samplesPerFrame = identity.sampleRate * 50 / 1_000
        let payload = Data(
            repeating: UInt8(truncatingIfNeeded: sequence &+ 1),
            count: samplesPerFrame * identity.channelCount * 2
        )
        return try AudioFrame(
            identity: identity,
            sequence: sequence,
            firstSample: sequence * UInt64(samplesPerFrame),
            capturedAtMs: sequence * 50,
            payload: payload
        )
    }

    private func makeGap(identity: SourceIdentity, sequence: UInt64) throws -> CoverageGap {
        try CoverageGap(
            identity: identity,
            firstSample: sequence * 800,
            lastSampleExclusive: nil,
            reason: .routeLoss,
            firstSequence: sequence,
            deviceID: "ScreenCaptureKit.SystemAudio",
            firstCapturedAtMonotonicNs: 1_000,
            firstCapturedAtWallClockMs: 2_000
        )
    }

    /// The regression this type exists for: the previous `Task { await
    /// sink.receive(frame) }` per frame gave the runtime free rein to reorder
    /// 50 ms PCM chunks, which garbles transcription in a way no crash log ever
    /// reveals.
    func testRapidProductionIsDeliveredInYieldOrder() async throws {
        let identity = try identity()
        let sink = RecordingSink()
        let relay = OrderedFrameRelay(sink: sink)

        let frameCount = 200
        let frames = try (0..<frameCount).map { try makeFrame(identity: identity, sequence: UInt64($0)) }

        // A serial queue is how both capture sources actually produce frames:
        // ScreenCaptureKit's sample handler queue and AVAudioEngine's input tap.
        let producer = DispatchQueue(label: "test.relay.producer")
        for frame in frames {
            producer.async { relay.yield(frame) }
        }
        producer.sync {}
        await relay.finish()

        let sequences = sink.observations.map { observation -> UInt64 in
            switch observation {
            case .frame(let sequence), .gap(let sequence): return sequence
            }
        }
        XCTAssertEqual(sequences.count, frameCount, "no frame may be dropped on the way to the sink")
        XCTAssertEqual(sequences, Array(0..<UInt64(frameCount)), "delivery order must equal yield order")
    }

    /// A gap describes the hole between the frames around it, so it must not
    /// overtake them.
    func testGapsKeepTheirPositionBetweenFrames() async throws {
        let identity = try identity()
        let sink = RecordingSink()
        let relay = OrderedFrameRelay(sink: sink)

        relay.yield(try makeFrame(identity: identity, sequence: 0))
        relay.yieldGap(try makeGap(identity: identity, sequence: 1))
        relay.yield(try makeFrame(identity: identity, sequence: 2))
        relay.yield(try makeFrame(identity: identity, sequence: 3))
        relay.yieldGap(try makeGap(identity: identity, sequence: 4))
        await relay.finish()

        XCTAssertEqual(
            sink.observations,
            [.frame(0), .gap(1), .frame(2), .frame(3), .gap(4)]
        )
    }

    /// `finish()` is the drain point capture sources rely on in `stop()`.
    func testFinishDrainsEverythingAlreadyYielded() async throws {
        let identity = try identity()
        let sink = RecordingSink()
        let relay = OrderedFrameRelay(sink: sink)

        for sequence in 0..<50 {
            relay.yield(try makeFrame(identity: identity, sequence: UInt64(sequence)))
        }
        await relay.finish()

        XCTAssertEqual(sink.observations.count, 50, "finish() must not return before the buffer is drained")
        await relay.finish()
        XCTAssertEqual(sink.observations.count, 50, "finish() is idempotent")
    }
}
