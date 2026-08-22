import XCTest
@testable import TarsNativeCompanion

final class CompanionOptionsTests: XCTestCase {
    func testDefaultsToSystemAudioOnly() throws {
        let o = try CompanionOptions.parse(["bin"])
        XCTAssertEqual(o.sources, .systemAudio)
    }
    func testParsesAllFlags() throws {
        let o = try CompanionOptions.parse(["bin", "--session-id", "s1", "--stream-key", "k1",
                                            "--sources", "both", "--gateway", "ws://x/api/stream/native"])
        XCTAssertEqual(o.sessionID, "s1")
        XCTAssertEqual(o.streamKey, "k1")
        XCTAssertEqual(o.sources, .both)
    }
    func testRejectsUnknownSources() {
        XCTAssertThrowsError(try CompanionOptions.parse(["bin", "--sources", "tab_audio"]))
    }
    func testURLIncludesEncodedStreamKey() throws {
        var o = try CompanionOptions.parse(["bin", "--session-id", "s1", "--stream-key", "k/+="])
        XCTAssertEqual(try o.gatewayURL().absoluteString,
                       "ws://127.0.0.1:8000/api/stream/native/s1?stream_key=k%2F%2B%3D")
        o.streamKey = ""
        XCTAssertEqual(try o.gatewayURL().absoluteString, "ws://127.0.0.1:8000/api/stream/native/s1")
    }
}
