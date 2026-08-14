import CryptoKit
import XCTest
@testable import TarsPhase1A

final class ProtocolV2ModelTests: XCTestCase {
    func testCanonicalV2Vectors() throws {
        let key = try V2StreamKey(sessionId: "session-v2", streamId: "stream-mic", captureGeneration: 4, source: .microphone)
        let first = try V2AtomicCoverage(key: key, sequence: 0, firstSample: 0, lastSampleExclusive: 160)
        let second = try V2AtomicCoverage(key: key, sequence: 1, firstSample: 160, lastSampleExclusive: 320)
        XCTAssertEqual(try first.coverageId, "acov_9646759fd911e57a6aa8eceb7101c1b86107b24b53fa0db200beb351b8ed6923")
        XCTAssertEqual(try second.coverageId, "acov_7253b33653a2042851ea98d4b59a302b62594fba658f19925fd16c6646c90895")
        XCTAssertEqual(try v2TerminalCoverageId(key: key, atomic: [second, first]), "covr_b501309bf531e3b7dc293857fc50752387fa7de3b48650820fff33d4024bb939")
        XCTAssertEqual(try v2TranscriptSegmentId(key: key, atomic: [first], textFirstSample: 20, textLastSampleExclusive: 120, providerResultOrdinal: 0, providerName: "fixture", providerResultId: "result-1", sttAttemptGeneration: 2), "seg_5bff65fae2a94b2ed887183957588ed3d650bcf1c87696f9d089f5a95282a50f")
    }

    func testRejectsNonNFCIdentityStrings() throws {
        let key = try V2StreamKey(sessionId: "session-v2", streamId: "stream-mic", captureGeneration: 4, source: .microphone)
        let coverage = try V2AtomicCoverage(key: key, sequence: 0, firstSample: 0, lastSampleExclusive: 160)
        XCTAssertThrowsError(try v2TranscriptSegmentId(key: key, atomic: [coverage], textFirstSample: 0, textLastSampleExclusive: 1, providerResultOrdinal: 0, providerName: "fixture", providerResultId: "result-e\u{301}", sttAttemptGeneration: nil))
    }

    func testRejectsDuplicateOverlapAndInvalidBounds() throws {
        let key = try V2StreamKey(sessionId: "session-v2", streamId: "stream-mic", captureGeneration: 4, source: .microphone)
        let first = try V2AtomicCoverage(key: key, sequence: 0, firstSample: 0, lastSampleExclusive: 160)
        let overlap = try V2AtomicCoverage(key: key, sequence: 1, firstSample: 80, lastSampleExclusive: 240)
        let middle = try V2AtomicCoverage(key: key, sequence: 1, firstSample: 800, lastSampleExclusive: 960)
        let nonadjacentOverlap = try V2AtomicCoverage(key: key, sequence: 2, firstSample: 80, lastSampleExclusive: 120)
        XCTAssertThrowsError(try v2TerminalCoverageId(key: key, atomic: [first, first]))
        XCTAssertThrowsError(try v2TerminalCoverageId(key: key, atomic: [first, overlap]))
        XCTAssertThrowsError(try v2TerminalCoverageId(key: key, atomic: [first, middle, nonadjacentOverlap]))
        XCTAssertThrowsError(try v2TranscriptSegmentId(key: key, atomic: [first], textFirstSample: 10, textLastSampleExclusive: 10, providerResultOrdinal: 0, providerName: "fixture", providerResultId: "result", sttAttemptGeneration: nil))
        XCTAssertThrowsError(try V2StreamKey(sessionId: "session\0bad", streamId: "stream-mic", captureGeneration: 4, source: .microphone))
    }

    func testCanonicalAudioFrameAndRetryCommitment() throws {
        let key = try V2StreamKey(sessionId: "session-v2", streamId: "stream-mic", captureGeneration: 4, source: .microphone)
        let payload = Data((0..<320).map { UInt8(($0 * 17 + 3) % 256) })
        let input = try V2AudioFrameInput(key: key, sequence: 0, firstSample: 0, lastSampleExclusive: 160, sampleRateHertz: 8_000, channelCount: 1, durationMs: 20, payload: payload)
        let metadata = try v2CanonicalAudioMetadata(input)
        let frame = try v2EncodeAudioFrame(input)
        let parsed = try v2ParseAudioFrame(frame)
        XCTAssertEqual(parsed.input, input)
        XCTAssertEqual(parsed.eventId, "aevt_93876bd7ae88af5c4c875e668bae680ce508d9982fc7f0f8d8e009c234f6dca2")
        XCTAssertEqual(metadata.count, 472)
        XCTAssertEqual(SHA256.hash(data: metadata).map { String(format: "%02x", $0) }.joined(), "4d4bfb8c38171b661d1a3890059701bbd343a4d6e2cfc62c1ff045cc8e1858bd")
        XCTAssertEqual(frame.count, 796)
        XCTAssertEqual(SHA256.hash(data: frame).map { String(format: "%02x", $0) }.joined(), "b6a1f52fe0d0bf30ab444c16ec5c9c935c014109fa4d38d06a6ca782866a23ed")
        let commitment = try v2RetryCommitment(sessionKey: Data((0..<32).map(UInt8.init)), metadata: metadata, payload: payload)
        XCTAssertEqual(commitment.map { String(format: "%02x", $0) }.joined(), "4a8d1b9605f776c966ac0d62c5a459ead0922a026c521f9e95accce7f069e4c2")
    }

    func testAudioFrameRejectsNoncanonicalDigestLengthAndExtraFields() throws {
        let key = try V2StreamKey(sessionId: "session-v2", streamId: "stream-mic", captureGeneration: 4, source: .microphone)
        let payload = Data((0..<320).map { UInt8(($0 * 17 + 3) % 256) })
        let input = try V2AudioFrameInput(key: key, sequence: 0, firstSample: 0, lastSampleExclusive: 160, sampleRateHertz: 8_000, channelCount: 1, durationMs: 20, payload: payload)
        let frame = try v2EncodeAudioFrame(input)
        XCTAssertThrowsError(try v2ParseAudioFrame(Data(frame.dropLast())))
        var changed = frame
        changed[changed.count - 1] ^= 1
        XCTAssertThrowsError(try v2ParseAudioFrame(changed))
        var oversizedPrefix = Data([0, 0, 16, 1])
        oversizedPrefix.append(Data("{}".utf8))
        XCTAssertThrowsError(try v2ParseAudioFrame(oversizedPrefix))
        let metadata = try v2CanonicalAudioMetadata(input)
        var noncanonical = Data("{ ".utf8)
        noncanonical.append(metadata.dropFirst())
        var noncanonicalFrame = Data([0, 0, UInt8(noncanonical.count >> 8), UInt8(noncanonical.count & 0xff)])
        noncanonicalFrame.append(noncanonical)
        noncanonicalFrame.append(payload)
        XCTAssertThrowsError(try v2ParseAudioFrame(noncanonicalFrame))
    }
}
