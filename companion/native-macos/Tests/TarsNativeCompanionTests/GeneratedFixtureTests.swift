import XCTest
@testable import TarsNativeCompanion

final class GeneratedFixtureTests: XCTestCase {
    func testOfflineSimulatorUsesBothIndependentSources() throws {
        var simulator = try OfflineCompanionSimulator()
        let trace = try simulator.run(frameCount: 4)
        XCTAssertEqual(trace.frames.count, 8)
        XCTAssertEqual(Set(trace.frames.map(\.identity.source)), Set(AudioSource.allCases))
        XCTAssertEqual(trace.snapshot.physical, .stopped)
        XCTAssertEqual(trace.snapshot.transport, .closed)
        XCTAssertEqual(trace.snapshot.coverage, .finalizing)
        XCTAssertEqual(trace.snapshot.acceptedFrames, 8)
    }

    func testOfflineDeletionDoesNotClaimProviderDeletionWithoutGatewayAck() throws {
        var simulator = try OfflineCompanionSimulator()
        _ = try simulator.run(frameCount: 2)
        let fence = try simulator.beginDeletion()
        XCTAssertEqual(simulator.lifecycle.coverage, .deleteQuiescing)
        XCTAssertEqual(simulator.deletion.phase, .awaitingGateway)
        try simulator.acknowledgeDeletion(fence, success: true)
        XCTAssertEqual(simulator.lifecycle.coverage, .deleted)
    }
}
