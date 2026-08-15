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
}
