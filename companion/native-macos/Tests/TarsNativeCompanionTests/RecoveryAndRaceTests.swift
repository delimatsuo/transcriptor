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

    func testCustodyReservationRollsBackWhenReducerRejectsFrame() throws {
        var simulator = try OfflineCompanionSimulator()
        try simulator.start()
        let mismatchedIdentity = try SourceIdentity(
            sessionID: simulator.microphone.sessionID,
            streamID: "other-mic",
            captureGeneration: simulator.microphone.captureGeneration,
            source: .microphone,
            sampleRate: simulator.microphone.sampleRate,
            channelCount: simulator.microphone.channelCount
        )
        let frame = try GeneratedFixtureSource(identity: mismatchedIdentity).makeFrames(count: 1)[0]
        XCTAssertThrowsError(try simulator.ingest(frame))
        XCTAssertEqual(simulator.microphoneCustody.retainedCount, 0)
    }
}
