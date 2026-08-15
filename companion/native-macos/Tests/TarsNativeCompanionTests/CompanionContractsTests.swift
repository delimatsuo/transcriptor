import Foundation
import XCTest
@testable import TarsNativeCompanion

final class CompanionContractsTests: XCTestCase {
    func testIdentityAndFrameAreDeterministic() throws {
        let identity = try SourceIdentity(
            sessionID: "session",
            streamID: "mic",
            captureGeneration: 2,
            source: .microphone,
            sampleRate: 16_000,
            channelCount: 1
        )
        let frame = try AudioFrame(
            identity: identity,
            sequence: 0,
            firstSample: 0,
            capturedAtMs: 0,
            payload: Data(repeating: 7, count: 640)
        )
        XCTAssertEqual(frame.sampleCount, 320)
        XCTAssertEqual(frame.durationMs, 20)
        XCTAssertTrue(frame.eventID.hasPrefix("aevt_"))
        XCTAssertEqual(frame, frame)
    }

    func testRejectsInvalidIdentityAndUnalignedFrame() throws {
        XCTAssertThrowsError(try SourceIdentity(
            sessionID: "bad session",
            streamID: "mic",
            captureGeneration: 1,
            source: .microphone,
            sampleRate: 16_000,
            channelCount: 1
        ))
        let identity = try SourceIdentity(sessionID: "session", streamID: "mic", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        XCTAssertThrowsError(try AudioFrame(identity: identity, sequence: 0, firstSample: 0, capturedAtMs: 0, payload: Data(repeating: 1, count: 641)))
    }

    func testHealthIsContentFreeAndFailClosed() {
        XCTAssertFalse(SourceHealth(permission: .granted, route: .ambiguous).isHealthy)
        XCTAssertTrue(SourceHealth(permission: .granted, route: .healthy, deviceIdentity: "fixture").isHealthy)
        XCTAssertFalse(SourceHealth(permission: .granted, route: .healthy).isHealthy)
    }

    func testFrozenV2AudioFrameEncodingRoundTrips() throws {
        let identity = try SourceIdentity(sessionID: "session-v2", streamID: "stream-mic", captureGeneration: 4, source: .microphone, sampleRate: 8_000, channelCount: 1)
        let payload = Data((0..<320).map { UInt8(($0 * 17 + 3) % 256) })
        let frame = try AudioFrame(identity: identity, sequence: 0, firstSample: 0, capturedAtMs: 0, payload: payload)
        XCTAssertEqual(frame.eventID, "aevt_93876bd7ae88af5c4c875e668bae680ce508d9982fc7f0f8d8e009c234f6dca2")
        let metadata = try v2CanonicalAudioMetadata(frame)
        let encoded = try v2EncodeAudioFrame(frame)
        XCTAssertEqual(metadata.count, 472)
        XCTAssertEqual(encoded.count, 796)
        XCTAssertEqual(try v2ParseAudioFrame(encoded).frame, frame)
        let context = try CaptureEventContext(deviceID: "fixture-device", capturedAtMonotonicNs: 123_000_000, capturedAtWallClockMs: 123)
        let rebound = try v2ParseAudioFrame(encoded, eventContext: context).frame
        XCTAssertEqual(rebound.eventContext, context)
        let envelope = try v2CanonicalEventEnvelopeMetadata(rebound)
        XCTAssertEqual(String(decoding: envelope, as: UTF8.self), "{\"captureGeneration\":\"4\",\"capturedAtMonotonicNs\":\"123000000\",\"capturedAtWallClockMs\":\"123\",\"deviceId\":\"fixture-device\",\"eventId\":\"aevt_93876bd7ae88af5c4c875e668bae680ce508d9982fc7f0f8d8e009c234f6dca2\",\"eventType\":\"audio.chunk\",\"protocolVersion\":2,\"sessionId\":\"session-v2\",\"streamId\":\"stream-mic\"}")
        let commitment = try v2RetryCommitment(sessionKey: Data((0..<32).map(UInt8.init)), metadata: metadata, payload: payload)
        XCTAssertEqual(commitment.count, 32)
    }

    func testFrozenV2CoverageVectorsAndUnambiguousIdentityKey() throws {
        let identity = try SourceIdentity(
            sessionID: "session-v2",
            streamID: "stream-mic",
            captureGeneration: 4,
            source: .microphone,
            sampleRate: 8_000,
            channelCount: 1
        )
        let first = try CoverageRange(identity: identity, sequence: 0, firstSample: 0, lastSampleExclusive: 160)
        let second = try CoverageRange(identity: identity, sequence: 1, firstSample: 160, lastSampleExclusive: 320)
        XCTAssertEqual(first.coverageID, "acov_9646759fd911e57a6aa8eceb7101c1b86107b24b53fa0db200beb351b8ed6923")
        XCTAssertEqual(second.coverageID, "acov_7253b33653a2042851ea98d4b59a302b62594fba658f19925fd16c6646c90895")
        XCTAssertEqual(
            try v2TerminalCoverageID(identity: identity, ranges: [second, first]),
            "covr_b501309bf531e3b7dc293857fc50752387fa7de3b48650820fff33d4024bb939"
        )
        XCTAssertThrowsError(try v2TerminalCoverageID(identity: identity, ranges: [first, first]))
        let sameSequence = try CoverageRange(identity: identity, sequence: 0, firstSample: 320, lastSampleExclusive: 480)
        XCTAssertThrowsError(try v2TerminalCoverageID(identity: identity, ranges: [first, sameSequence]))

        let left = try SourceIdentity(sessionID: "a:b", streamID: "c", captureGeneration: 1, source: .microphone, sampleRate: 8_000, channelCount: 1)
        let right = try SourceIdentity(sessionID: "a", streamID: "b:c", captureGeneration: 1, source: .microphone, sampleRate: 8_000, channelCount: 1)
        XCTAssertNotEqual(left.key, right.key)
    }

    func testEventContextIsPresentWithoutChangingFrozenBody() throws {
        let context = try CaptureEventContext(deviceID: "fixture-device", capturedAtMonotonicNs: 123, capturedAtWallClockMs: 456)
        let identity = try SourceIdentity(sessionID: "session", streamID: "mic", captureGeneration: 1, source: .microphone, sampleRate: 8_000, channelCount: 1)
        let frame = try AudioFrame(identity: identity, sequence: 0, firstSample: 0, capturedAtMs: 0, eventContext: context, payload: Data(repeating: 1, count: 320))
        XCTAssertEqual(frame.eventContext, context)
        XCTAssertEqual(try v2CanonicalAudioMetadata(frame), try v2CanonicalAudioMetadata(AudioFrame(identity: identity, sequence: 0, firstSample: 0, capturedAtMs: 0, payload: Data(repeating: 1, count: 320))))
    }

    func testDiagnosticsAreBounded() {
        var diagnostics = Diagnostics()
        for index in 0..<300 {
            diagnostics.record(DiagnosticEvent(code: "event-\(index)"))
        }
        XCTAssertEqual(diagnostics.snapshot.count, 256)
        XCTAssertEqual(diagnostics.snapshot.first?.code, "event-44")
        diagnostics.record(DiagnosticEvent(code: String(repeating: "x", count: 257)))
        XCTAssertEqual(diagnostics.snapshot.count, 256)
    }
}
