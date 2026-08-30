import Foundation
import XCTest
@testable import TarsNativeCompanion

final class SystemAudioEngineSelectorTests: XCTestCase {
    private func version(_ major: Int, _ minor: Int, _ patch: Int = 0) -> OperatingSystemVersion {
        OperatingSystemVersion(majorVersion: major, minorVersion: minor, patchVersion: patch)
    }

    func testAutomaticPolicyUsesSCKThrough143AndTapFrom144() throws {
        XCTAssertEqual(try SystemAudioEngineSelector.resolve(preference: .automatic, operatingSystemVersion: version(13, 0)), .screenCaptureKit)
        XCTAssertEqual(try SystemAudioEngineSelector.resolve(preference: .automatic, operatingSystemVersion: version(14, 1)), .screenCaptureKit)
        XCTAssertEqual(try SystemAudioEngineSelector.resolve(preference: .automatic, operatingSystemVersion: version(14, 2)), .screenCaptureKit)
        XCTAssertEqual(try SystemAudioEngineSelector.resolve(preference: .automatic, operatingSystemVersion: version(14, 3)), .screenCaptureKit)
        XCTAssertEqual(try SystemAudioEngineSelector.resolve(preference: .automatic, operatingSystemVersion: version(14, 4)), .processTap)
        XCTAssertEqual(try SystemAudioEngineSelector.resolve(preference: .automatic, operatingSystemVersion: version(26, 0)), .processTap)
    }

    func testExplicitOverridesAndTapBoundaryAreLoud() throws {
        XCTAssertEqual(try SystemAudioEngineSelector.resolve(preference: .screenCaptureKit, operatingSystemVersion: version(26, 0)), .screenCaptureKit)
        XCTAssertEqual(try SystemAudioEngineSelector.resolve(preference: .processTap, operatingSystemVersion: version(14, 2)), .processTap)
        XCTAssertThrowsError(try SystemAudioEngineSelector.resolve(preference: .processTap, operatingSystemVersion: version(13, 0))) { error in
            XCTAssertEqual(error as? SystemAudioEngineSelectionError, .processTapRequiresMacOS14_2(version(13, 0)))
        }
    }

    func testAppLaunchArgumentDefaultValidMissingAndInvalid() throws {
        XCTAssertEqual(try SystemAudioEnginePreference.preference(fromLaunchArguments: ["TarsCompanionApp"]), .automatic)
        XCTAssertEqual(try SystemAudioEnginePreference.preference(fromLaunchArguments: ["TarsCompanionApp", "--system-audio-engine", "auto"]), .automatic)
        XCTAssertEqual(try SystemAudioEnginePreference.preference(fromLaunchArguments: ["TarsCompanionApp", "--system-audio-engine", "process-tap"]), .processTap)
        XCTAssertEqual(try SystemAudioEnginePreference.preference(fromLaunchArguments: ["TarsCompanionApp", "--system-audio-engine", "screen-capture-kit"]), .screenCaptureKit)
        XCTAssertEqual(try SystemAudioEnginePreference.preference(fromLaunchArguments: ["--system-audio-engine", "process-tap"]), .processTap)
        XCTAssertThrowsError(try SystemAudioEnginePreference.preference(fromLaunchArguments: ["TarsCompanionApp", "--system-audio-engine"])) { error in
            XCTAssertEqual(error as? SystemAudioEngineLaunchArgumentError, .missingValue)
        }
        XCTAssertThrowsError(try SystemAudioEnginePreference.preference(fromLaunchArguments: ["TarsCompanionApp", "--system-audio-engine", "bogus"])) { error in
            XCTAssertEqual(error as? SystemAudioEngineLaunchArgumentError, .invalidValue("bogus"))
        }
    }

    func testProcessTapSelectionHasNoFallbackDecision() throws {
        let selector = SystemAudioEngineSelector(operatingSystemVersion: version(14, 4))
        XCTAssertEqual(try selector.resolve(.processTap), .processTap)
        XCTAssertEqual(try selector.resolve(.automatic), .processTap)
    }
}
