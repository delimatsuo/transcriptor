import XCTest
@testable import TarsNativeCompanion

final class DeletionTests: XCTestCase {
    func testDeletionRequiresWorkerAndStreamQuiescence() throws {
        var deletion = DeletionCoordinator()
        let fence = try DeletionFence(sessionID: "session", generation: 1)
        try deletion.begin(fence: fence)
        try deletion.workerStarted(fence)
        XCTAssertThrowsError(try deletion.markLocalZeroized(fence))
        try deletion.workerStopped(fence)
        try deletion.streamStarted(fence)
        XCTAssertThrowsError(try deletion.markLocalZeroized(fence))
        try deletion.streamStopped(fence)
        try deletion.markLocalZeroized(fence)
        try deletion.awaitGatewayAcknowledgement(fence)
        try deletion.gatewayAcknowledged(fence, deleted: true)
        XCTAssertEqual(deletion.phase, .deleted)
    }

    func testLateCallbackIsRejectedAfterDeletion() throws {
        var deletion = DeletionCoordinator()
        let fence = try DeletionFence(sessionID: "session", generation: 1)
        try deletion.begin(fence: fence)
        try deletion.markLocalZeroized(fence)
        try deletion.awaitGatewayAcknowledgement(fence)
        try deletion.gatewayAcknowledged(fence, deleted: true)
        XCTAssertFalse(deletion.acceptCallback(fence))
        XCTAssertEqual(deletion.lateCallbacksRejected, 1)
    }

    func testCallbackMustFinishBeforeLocalZeroization() throws {
        var deletion = DeletionCoordinator()
        let fence = try DeletionFence(sessionID: "session", generation: 1)
        try deletion.begin(fence: fence)
        try deletion.callbackStarted(fence)
        XCTAssertThrowsError(try deletion.markLocalZeroized(fence))
        try deletion.callbackFinished(fence)
        try deletion.markLocalZeroized(fence)
        try deletion.awaitGatewayAcknowledgement(fence)
        try deletion.gatewayAcknowledged(fence, deleted: true)
    }

    func testProviderEffectMustFinishBeforeLocalZeroization() throws {
        var deletion = DeletionCoordinator()
        let fence = try DeletionFence(sessionID: "session", generation: 1)
        try deletion.begin(fence: fence)
        try deletion.effectStarted(fence)
        XCTAssertThrowsError(try deletion.markLocalZeroized(fence))
        try deletion.effectFinished(fence)
        try deletion.markLocalZeroized(fence)
    }

    func testReplacementFenceCannotCrossSession() throws {
        var deletion = DeletionCoordinator()
        let first = try DeletionFence(sessionID: "session", generation: 1)
        let other = try DeletionFence(sessionID: "other", generation: 2)
        try deletion.begin(fence: first)
        try deletion.markLocalZeroized(first)
        try deletion.awaitGatewayAcknowledgement(first)
        try deletion.gatewayAcknowledged(first, deleted: false)
        XCTAssertThrowsError(try deletion.replaceFence(other))
    }
}
