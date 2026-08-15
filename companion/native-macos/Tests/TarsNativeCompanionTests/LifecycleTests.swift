import XCTest
@testable import TarsNativeCompanion

final class LifecycleTests: XCTestCase {
    func testGatewayOwnsTerminalCoverage() throws {
        var lifecycle = LifecycleCoordinator()
        try lifecycle.beginPermissionAndDeviceCheck()
        try lifecycle.updateHealth(SourceHealth(permission: .granted, route: .healthy), for: .microphone)
        try lifecycle.updateHealth(SourceHealth(permission: .granted, route: .healthy), for: .systemAudio)
        try lifecycle.openTransport()
        try lifecycle.startCapture()
        try lifecycle.stopCapture()
        try lifecycle.beginTransportDrain()
        try lifecycle.closeTransport()
        XCTAssertEqual(lifecycle.coverage, .finalizing)
        try lifecycle.observeGatewayCoverage(.completedWithGaps)
        XCTAssertEqual(lifecycle.coverage, .completedWithGaps)
    }

    func testPermissionOrRouteFaultDegradesCapture() throws {
        var lifecycle = LifecycleCoordinator()
        try lifecycle.beginPermissionAndDeviceCheck()
        try lifecycle.updateHealth(SourceHealth(permission: .denied, route: .unavailable), for: .microphone)
        XCTAssertEqual(lifecycle.physical, .degraded)
        XCTAssertThrowsError(try lifecycle.startCapture())
    }
}
