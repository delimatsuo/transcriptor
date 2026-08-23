import XCTest
@testable import TarsNativeCompanion

final class JoinLinkTests: XCTestCase {
    func testFullURLWithGateway() {
        let input = "tars-companion://join?session=sess_123&key=key_abc&gateway=ws://custom:8000/api"
        guard let request = JoinLink.parse(input) else {
            XCTFail("Failed to parse full URL with gateway")
            return
        }
        XCTAssertEqual(request.sessionID, "sess_123")
        XCTAssertEqual(request.streamKey, "key_abc")
        XCTAssertEqual(request.gateway, "ws://custom:8000/api")
    }

    func testFullURLWithoutGatewayYieldsNilGateway() {
        let input = "tars-companion://join?session=sess_456&key=key_def"
        guard let request = JoinLink.parse(input) else {
            XCTFail("Failed to parse full URL without gateway")
            return
        }
        XCTAssertEqual(request.sessionID, "sess_456")
        XCTAssertEqual(request.streamKey, "key_def")
        XCTAssertNil(request.gateway)
    }

    func testCompactForm() {
        let input = "abc123:key456"
        guard let request = JoinLink.parse(input) else {
            XCTFail("Failed to parse compact form")
            return
        }
        XCTAssertEqual(request.sessionID, "abc123")
        XCTAssertEqual(request.streamKey, "key456")
        XCTAssertNil(request.gateway)
    }

    func testTrimsWhitespace() {
        let inputURL = "  tars-companion://join?session=s1&key=k1   \n"
        let requestURL = JoinLink.parse(inputURL)
        XCTAssertEqual(requestURL?.sessionID, "s1")
        XCTAssertEqual(requestURL?.streamKey, "k1")

        let inputCompact = "   s2:k2 \t"
        let requestCompact = JoinLink.parse(inputCompact)
        XCTAssertEqual(requestCompact?.sessionID, "s2")
        XCTAssertEqual(requestCompact?.streamKey, "k2")
    }

    func testPercentEncodedKeyInURLDecodes() {
        let input = "tars-companion://join?session=s1&key=k%2B%2F%3D"
        guard let request = JoinLink.parse(input) else {
            XCTFail("Failed to parse percent-encoded key")
            return
        }
        XCTAssertEqual(request.sessionID, "s1")
        XCTAssertEqual(request.streamKey, "k+/=")
    }

    func testPercentEncodedGatewayInURLDecodes() {
        let input = "tars-companion://join?session=s1&key=k1&gateway=ws%3A%2F%2F127.0.0.1%3A8000%2Fnative"
        guard let request = JoinLink.parse(input) else {
            XCTFail("Failed to parse percent-encoded gateway")
            return
        }
        XCTAssertEqual(request.sessionID, "s1")
        XCTAssertEqual(request.streamKey, "k1")
        XCTAssertEqual(request.gateway, "ws://127.0.0.1:8000/native")
    }

    func testRejectsEmptyAndWhitespaceStrings() {
        XCTAssertNil(JoinLink.parse(""))
        XCTAssertNil(JoinLink.parse("   \n\t"))
    }

    func testRejectsWrongScheme() {
        XCTAssertNil(JoinLink.parse("foo://join?session=s1&key=k1"))
        XCTAssertNil(JoinLink.parse("https://join?session=s1&key=k1"))
    }

    func testRejectsWrongHost() {
        XCTAssertNil(JoinLink.parse("tars-companion://other?session=s1&key=k1"))
    }

    func testRejectsMissingOrEmptyKey() {
        XCTAssertNil(JoinLink.parse("tars-companion://join?session=s1"))
        XCTAssertNil(JoinLink.parse("tars-companion://join?session=s1&key="))
        XCTAssertNil(JoinLink.parse("s1:"))
    }

    func testRejectsMissingOrEmptySession() {
        XCTAssertNil(JoinLink.parse("tars-companion://join?key=k1"))
        XCTAssertNil(JoinLink.parse("tars-companion://join?session=&key=k1"))
        XCTAssertNil(JoinLink.parse(":k1"))
    }

    func testRejectsSinglePartWithoutColon() {
        XCTAssertNil(JoinLink.parse("justonepart"))
    }
}
