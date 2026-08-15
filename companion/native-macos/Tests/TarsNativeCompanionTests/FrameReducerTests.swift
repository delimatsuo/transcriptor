import XCTest
@testable import TarsNativeCompanion

final class FrameReducerTests: XCTestCase {
    func testReducerAcceptsTwoSourcesAndRejectsStaleGeneration() throws {
        let factory = try SourceIdentityFactory(sessionID: "session", captureGeneration: 1)
        let identity = try factory.make(source: .microphone, streamID: "mic", sampleRate: 16_000, channelCount: 1)
        var reducer = FrameReducer()
        try reducer.activate(identity)
        let first = try GeneratedFixtureSource(identity: identity).makeFrames(count: 2)
        XCTAssertEqual(try reducer.ingest(first[0]), .accepted(ReducedFrame(frame: first[0])))
        XCTAssertEqual(try reducer.ingest(first[1]), .accepted(ReducedFrame(frame: first[1])))
        XCTAssertThrowsError(try reducer.ingest(first[1]))
        let nextFactory = try factory.nextGeneration()
        let stale = try nextFactory.make(source: .microphone, streamID: "mic", sampleRate: 16_000, channelCount: 1)
        XCTAssertThrowsError(try reducer.ingest(try GeneratedFixtureSource(identity: stale).makeFrames(count: 1)[0]))
    }

    func testReducerRecordsExplicitUnknownEndGap() throws {
        let identity = try SourceIdentity(sessionID: "session", streamID: "mic", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        var reducer = FrameReducer()
        try reducer.activate(identity)
        let frame = try GeneratedFixtureSource(identity: identity).makeFrames(count: 1)[0]
        _ = try reducer.ingest(frame)
        let gap = try reducer.recordGap(identity: identity, firstSample: frame.lastSampleExclusive, lastSampleExclusive: nil, reason: .unknownEnd)
        XCTAssertEqual(gap.reason, .unknownEnd)
        XCTAssertEqual(reducer.recordedGaps.count, 1)
    }

    func testReducerPreservesExactKnownGapBounds() throws {
        let identity = try SourceIdentity(sessionID: "session", streamID: "mic", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        var reducer = FrameReducer()
        try reducer.activate(identity)
        let first = try GeneratedFixtureSource(identity: identity).makeFrames(count: 1)[0]
        _ = try reducer.ingest(first)
        let gap = try reducer.recordGap(
            identity: identity,
            firstSample: 320,
            lastSampleExclusive: 960,
            reason: .overflow,
            firstSequence: 1,
            lastSequenceExclusive: 3,
            firstCapturedAtMs: 20,
            lastCapturedAtMs: 60
        )
        XCTAssertEqual(gap.firstSequence, 1)
        XCTAssertEqual(gap.lastSequenceExclusive, 3)
        XCTAssertEqual(gap.firstCapturedAtMs, 20)
        XCTAssertEqual(gap.lastCapturedAtMs, 60)
        let resumed = try AudioFrame(identity: identity, sequence: 3, firstSample: 960, capturedAtMs: 60, payload: Data(repeating: 4, count: 640))
        XCTAssertNoThrow(try reducer.ingest(resumed))
        XCTAssertThrowsError(try reducer.recordGap(identity: identity, firstSample: 1_280, lastSampleExclusive: nil, reason: .unknownEnd, firstSequence: 9))
    }
}
