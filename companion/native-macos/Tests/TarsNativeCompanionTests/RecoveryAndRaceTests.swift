import Foundation
import XCTest
@testable import TarsNativeCompanion

final class RecoveryAndRaceTests: XCTestCase {
    func testStaleEffectCannotReviveReplacedFence() throws {
        var deletion = DeletionCoordinator()
        let first = try DeletionFence(sessionID: "session", generation: 1)
        let second = try DeletionFence(sessionID: "session", generation: 2)
        try deletion.begin(fence: first)
        try deletion.markLocalZeroized(first)
        try deletion.awaitGatewayAcknowledgement(first)
        try deletion.gatewayAcknowledged(first, deleted: false)
        try deletion.replaceFence(second)
        XCTAssertFalse(deletion.acceptCallback(first))
        XCTAssertTrue(deletion.acceptCallback(second))
    }

    func testClockUncertaintyStopsAcquisitionAndDiscardsAllCustody() throws {
        let identity = try SourceIdentity(sessionID: "session", streamID: "mic", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        let frame = try GeneratedFixtureSource(identity: identity).makeFrames(count: 1)[0]
        var ring = CustodyRing(limits: try CustodyLimits(sampleRate: 16_000, channelCount: 1))
        _ = try ring.reserve(CustodyEntry(frame: frame))
        _ = try ring.advanceClock(0, clockCertain: false)
        XCTAssertTrue(ring.acquisitionStopped)
        XCTAssertEqual(ring.retainedCount, 0)
    }
}
