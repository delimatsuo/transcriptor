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

    func testGatewayURLIsKeylessEvenWithStreamKey() throws {
        var o = try CompanionOptions.parse(["bin", "--session-id", "s1", "--stream-key", "k/+="])
        XCTAssertEqual(try o.gatewayURL().absoluteString, "ws://127.0.0.1:8000/api/stream/native/s1")
        o.streamKey = ""
        XCTAssertEqual(try o.gatewayURL().absoluteString, "ws://127.0.0.1:8000/api/stream/native/s1")
    }

    func testWebSocketProtocolsDerivesValidSubprotocol() throws {
        let o = try CompanionOptions.parse(["bin", "--session-id", "s1", "--stream-key", "valid_key-123"])
        let protocols = try o.webSocketProtocols()
        XCTAssertEqual(protocols, ["tars-stream", "valid_key-123"])
    }

    func testWebSocketProtocolsRejectsEmptyAndInvalidKeysWithoutEchoing() throws {
        var o = try CompanionOptions.parse(["bin", "--session-id", "s1", "--stream-key", ""])
        XCTAssertThrowsError(try o.webSocketProtocols()) { error in
            let description = "\(error)"
            XCTAssertTrue(description.contains("stream key must not be empty"))
        }

        o.streamKey = "  valid_key-123  "
        XCTAssertThrowsError(try o.webSocketProtocols()) { error in
            let description = "\(error)"
            XCTAssertFalse(description.contains("valid_key-123"), "Must not echo invalid key in error description")
        }

        o.streamKey = "chave_inválida_123"
        XCTAssertThrowsError(try o.webSocketProtocols()) { error in
            let description = "\(error)"
            XCTAssertFalse(description.contains("chave_inválida_123"), "Must not echo non-ASCII key in error description")
        }

        o.streamKey = "bad key with spaces"
        XCTAssertThrowsError(try o.webSocketProtocols()) { error in
            let description = "\(error)"
            XCTAssertFalse(description.contains("bad key with spaces"), "Must not echo invalid key in error description")
        }
    }
}
