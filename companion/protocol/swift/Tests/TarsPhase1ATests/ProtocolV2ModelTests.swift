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
}
