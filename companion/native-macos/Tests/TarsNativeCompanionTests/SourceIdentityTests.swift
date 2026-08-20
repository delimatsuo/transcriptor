import XCTest
@testable import TarsNativeCompanion

final class SourceIdentityTests: XCTestCase {
    func testFactoryKeepsSourceAndGenerationIndependent() throws {
        let factory = try SourceIdentityFactory(sessionID: "session", captureGeneration: 4)
        let mic = try factory.make(source: .microphone, streamID: "mic", sampleRate: 16_000, channelCount: 1)
        let system = try factory.make(source: .systemAudio, streamID: "system", sampleRate: 48_000, channelCount: 2)
        XCTAssertNotEqual(mic, system)
        XCTAssertEqual(mic.captureGeneration, system.captureGeneration)
        XCTAssertEqual(try factory.nextGeneration().captureGeneration, 5)
    }

    func testSequenceTrackerRequiresContiguousRanges() throws {
        let identity = try SourceIdentity(sessionID: "session", streamID: "mic", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        let first = try AudioFrame(identity: identity, sequence: 0, firstSample: 0, capturedAtMs: 0, payload: Data(repeating: 1, count: 640))
        let second = try AudioFrame(identity: identity, sequence: 1, firstSample: 320, capturedAtMs: 20, payload: Data(repeating: 2, count: 640))
        var tracker = SourceSequenceTracker()
        try tracker.validate(first)
        try tracker.validate(second)
        XCTAssertEqual(tracker.expected(identity: identity).sequence, 2)
        let skipped = try AudioFrame(identity: identity, sequence: 3, firstSample: 960, capturedAtMs: 60, payload: Data(repeating: 3, count: 640))
        XCTAssertThrowsError(try tracker.validate(skipped))
    }
}
