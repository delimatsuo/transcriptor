import XCTest
@testable import TarsNativeCompanion

final class JoinLinkTests: XCTestCase {
    func testRejectsFullURLWithGateway() {
        let input = "tars-companion://join?session=sess_123&key=key_abc&gateway=ws://custom:8000/api"
        XCTAssertNil(JoinLink.parse(input))
    }

    func testRejectsFullURLWithEmptyGateway() {
        let inputWithValueEmpty = "tars-companion://join?session=sess_123&key=key_abc&gateway="
        XCTAssertNil(JoinLink.parse(inputWithValueEmpty))

        let inputWithoutValue = "tars-companion://join?session=sess_123&key=key_abc&gateway"
        XCTAssertNil(JoinLink.parse(inputWithoutValue))
    }

    func testFullURLWithoutGatewayYieldsValidRequest() {
        let input = "tars-companion://join?session=sess_456&key=key_def"
        guard let request = JoinLink.parse(input) else {
            XCTFail("Failed to parse full URL without gateway")
            return
        }
        XCTAssertEqual(request.sessionID, "sess_456")
        XCTAssertEqual(request.streamKey, "key_def")
    }

    func testCompactForm() {
        let input = "abc123:key456"
        guard let request = JoinLink.parse(input) else {
            XCTFail("Failed to parse compact form")
            return
        }
        XCTAssertEqual(request.sessionID, "abc123")
        XCTAssertEqual(request.streamKey, "key456")
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

    // MARK: - Causal Receipt Function Tests

    func testReceiptHostileGatewayWithCanaryProducesZeroAdmissionsAndSafeLogs() {
        let canary = "credential-canary"
        let hostileURL = "tars-companion://join?session=sess_hostile&key=\(canary)&gateway=wss://exfil.example/api"

        var logs: [String] = []
        var admissions: [JoinRequest] = []

        JoinLink.receive(hostileURL, log: { msg in
            logs.append(msg)
        }, onAdmit: { req in
            admissions.append(req)
        })

        // Zero admissions
        XCTAssertEqual(admissions.count, 0)

        // Fixed receipt plus invalid logs
        XCTAssertEqual(logs, [
            "TarsCompanion: URL recebida",
            "TarsCompanion: link inválido"
        ])

        // Neither canary, nor exfil host, nor raw URL in any log entry
        for log in logs {
            XCTAssertFalse(log.contains(canary))
            XCTAssertFalse(log.contains("exfil.example"))
            XCTAssertFalse(log.contains("tars-companion://"))
            XCTAssertFalse(log.contains(hostileURL))
        }
    }

    func testReceiptValidNoGatewayLinkAdmitsOnceAndExcludesKeyAndRawURLFromLogs() {
        let secretKey = "VALID_STREAM_KEY_SECRET_54321"
        let validURL = "tars-companion://join?session=sess_clean&key=\(secretKey)"

        var logs: [String] = []
        var admissions: [JoinRequest] = []

        JoinLink.receive(validURL, log: { msg in
            logs.append(msg)
        }, onAdmit: { req in
            admissions.append(req)
        })

        // Valid link admits exactly once
        XCTAssertEqual(admissions.count, 1)
        XCTAssertEqual(admissions.first?.sessionID, "sess_clean")
        XCTAssertEqual(admissions.first?.streamKey, secretKey)

        // Fixed receipt log only
        XCTAssertEqual(logs, [
            "TarsCompanion: URL recebida"
        ])

        // Logs exclude key and raw URL
        for log in logs {
            XCTAssertFalse(log.contains(secretKey))
            XCTAssertFalse(log.contains("tars-companion://"))
            XCTAssertFalse(log.contains(validURL))
        }
    }

    func testReceiptEmptyGatewayRejectedWithSafeLogs() {
        let canary = "EMPTY_GATEWAY_CANARY_112233"
        let url = "tars-companion://join?session=sess_1&key=\(canary)&gateway="

        var logs: [String] = []
        var admissions: [JoinRequest] = []

        JoinLink.receive(url, log: { msg in
            logs.append(msg)
        }, onAdmit: { req in
            admissions.append(req)
        })

        XCTAssertEqual(admissions.count, 0)
        XCTAssertEqual(logs, [
            "TarsCompanion: URL recebida",
            "TarsCompanion: link inválido"
        ])
        for log in logs {
            XCTAssertFalse(log.contains(canary))
            XCTAssertFalse(log.contains(url))
        }
    }
}
