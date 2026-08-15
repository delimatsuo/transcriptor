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
        let commitment = try v2RetryCommitment(sessionKey: Data((0..<32).map(UInt8.init)), metadata: metadata, payload: payload)
        XCTAssertEqual(commitment.count, 32)
    }
}
