import XCTest
@testable import TarsNativeCompanion

final class LifecycleTests: XCTestCase {
    func testLifecycleAlwaysRequiresBothCaptureAxes() throws {
        var lifecycle = LifecycleCoordinator(sources: [.microphone])
        XCTAssertEqual(Set(lifecycle.sourceHealth.keys), Set(AudioSource.allCases))
        try lifecycle.beginPermissionAndDeviceCheck()
        try lifecycle.updateHealth(SourceHealth(permission: .granted, route: .healthy, deviceIdentity: "mic-device"), for: .microphone)
        XCTAssertThrowsError(try lifecycle.startCapture())
    }

    func testGatewayOwnsTerminalCoverage() throws {
        var lifecycle = LifecycleCoordinator()
        try lifecycle.beginPermissionAndDeviceCheck()
        try lifecycle.updateHealth(SourceHealth(permission: .granted, route: .healthy, deviceIdentity: "mic-device"), for: .microphone)
        try lifecycle.updateHealth(SourceHealth(permission: .granted, route: .healthy, deviceIdentity: "system-device"), for: .systemAudio)
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

    func testEveryCaptureHealthFaultDegradesClosedLoop() throws {
        let faults = [
            SourceHealth(permission: .granted, route: .changed),
            SourceHealth(permission: .granted, route: .healthy, interruption: .interrupted),
            SourceHealth(permission: .granted, route: .healthy, sleep: .sleeping),
            SourceHealth(permission: .granted, route: .healthy, overflowed: true),
            SourceHealth(permission: .unknown, route: .unknown)
        ]
        for fault in faults {
            var lifecycle = LifecycleCoordinator()
            try lifecycle.beginPermissionAndDeviceCheck()
            try lifecycle.updateHealth(fault, for: .microphone)
            XCTAssertEqual(lifecycle.physical, .degraded)
        }
    }

    func testDeletionCannotFinishWithLifecycleCallbackPending() throws {
        var lifecycle = LifecycleCoordinator()
        try lifecycle.beginPermissionAndDeviceCheck()
        try lifecycle.updateHealth(SourceHealth(permission: .granted, route: .healthy, deviceIdentity: "mic-device"), for: .microphone)
        try lifecycle.updateHealth(SourceHealth(permission: .granted, route: .healthy, deviceIdentity: "system-device"), for: .systemAudio)
        try lifecycle.openTransport()
        try lifecycle.startCapture()
        try lifecycle.callbackStarted()
        try lifecycle.stopCapture()
        try lifecycle.beginDeletion()
        XCTAssertThrowsError(try lifecycle.finishDeletion(success: true))
        try lifecycle.callbackFinished()
        try lifecycle.finishDeletion(success: true)
    }
}
