import XCTest
@testable import TarsNativeCompanion

final class FrameReducerTests: XCTestCase {
    func testReducerAcceptsTwoSourcesAndRejectsStaleGeneration() throws {
        let factory = try SourceIdentityFactory(sessionID: "session", captureGeneration: 1)
        let identity = try factory.make(source: .microphone, streamID: "mic", sampleRate: 16_000, channelCount: 1)
        var reducer = FrameReducer()
        try reducer.activate(identity)
        let first = try GeneratedFixtureSource(identity: identity).makeFrames(count: 2)
        XCTAssertEqual(try reducer.ingest(first[0]), .accepted(first[0]))
        XCTAssertEqual(try reducer.ingest(first[1]), .accepted(first[1]))
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
}
