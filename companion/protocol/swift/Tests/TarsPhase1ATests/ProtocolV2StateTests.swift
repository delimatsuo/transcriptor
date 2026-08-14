import XCTest
@testable import TarsPhase1A

final class ProtocolV2StateTests: XCTestCase {
    func testExactRateDerivedCustodyBoundsAndPrivacyDeadline() throws {
        var lowRate = try V2CustodyBudget(sampleRateHertz: 8_000, channelCount: 1)
        for index in 0..<100 {
            XCTAssertTrue(try lowRate.reserve(V2CustodyReservation(
                eventId: "event-\(index)", frames: 160, payloadBytes: 320,
                metadataBytes: 4_096, residentBytes: 4_544, capturedAtMs: UInt64(index * 20)
            )))
        }
        XCTAssertEqual(lowRate.retainedEvents, 100)
        XCTAssertEqual(lowRate.retainedFrames, 16_000)
        XCTAssertEqual(lowRate.retainedPayloadBytes, 32_000)
        XCTAssertEqual(lowRate.retainedMetadataBytes, 409_600)
        XCTAssertThrowsError(try lowRate.reserve(V2CustodyReservation(
            eventId: "event-100", frames: 160, payloadBytes: 320,
            metadataBytes: 4_096, residentBytes: 4_544, capturedAtMs: 2_000
        )))
        XCTAssertTrue(lowRate.acquisitionStopped)

        var highRate = try V2CustodyBudget(sampleRateHertz: 48_000, channelCount: 2)
        for index in 0..<8 {
            XCTAssertTrue(try highRate.reserve(V2CustodyReservation(
                eventId: "stereo-\(index)", frames: 12_000, payloadBytes: 48_000,
                metadataBytes: 4_096, residentBytes: 52_608, capturedAtMs: UInt64(index * 250)
            )))
        }
        XCTAssertEqual(highRate.retainedFrames, 96_000)
        XCTAssertEqual(highRate.retainedPayloadBytes, 384_000)
        XCTAssertThrowsError(try highRate.reserve(V2CustodyReservation(
            eventId: "stereo-8", frames: 12_000, payloadBytes: 48_000,
            metadataBytes: 4_096, residentBytes: 52_608, capturedAtMs: 2_000
        )))

        var expiring = try V2CustodyBudget(sampleRateHertz: 8_000, channelCount: 1)
        try expiring.reserve(V2CustodyReservation(
            eventId: "expiring", frames: 160, payloadBytes: 320,
            metadataBytes: 472, residentBytes: 920, capturedAtMs: 0
        ))
        XCTAssertEqual(try expiring.advanceClock(10_000), [])
        XCTAssertTrue(expiring.acquisitionStopped)
        XCTAssertEqual(try expiring.advanceClock(30_000), ["expiring"])
        XCTAssertEqual(expiring.released["expiring"], "privacy_timeout_local")
    }

    func testMixedSource6090120MinuteQuotaMatrixStaysBounded() throws {
        let sourceLimits = try V2QuotaLimits(
            eventRate: 50, eventBurst: 100,
            payloadRate: 192_000, payloadBurst: 384_000,
            metadataRate: 205_000, metadataBurst: 410_000,
            custodyBytes: 1_048_576
        )
        let sessionLimits = try V2QuotaLimits(
            eventRate: 100, eventBurst: 200,
            payloadRate: 384_000, payloadBurst: 768_000,
            metadataRate: 410_000, metadataBurst: 820_000,
            custodyBytes: 2_097_152
        )
        for minutes in [60, 90, 120] {
            var sources = [V2TokenBucket(limits: sourceLimits), V2TokenBucket(limits: sourceLimits)]
            var session = V2TokenBucket(limits: sessionLimits)
            var tenant = V2TokenBucket(limits: try V2QuotaLimits(
                eventRate: 400, eventBurst: 800,
                payloadRate: 1_536_000, payloadBurst: 3_072_000,
                metadataRate: 1_640_000, metadataBurst: 3_280_000,
                custodyBytes: 8_388_608
            ))
            var process = V2TokenBucket(limits: try V2QuotaLimits(
                eventRate: 1_600, eventBurst: 3_200,
                payloadRate: 6_144_000, payloadBurst: 12_288_000,
                metadataRate: 6_560_000, metadataBurst: 13_120_000,
                custodyBytes: 33_554_432
            ))
            let payloads = [320, 3_840]
            let events = minutes * 60 * 50
            for event in 0..<events {
                let second = event / 50
                for source in sources.indices {
                    let payload = payloads[source]
                    XCTAssertTrue(try sources[source].reserve(
                        second: second, events: 1, payloadBytes: payload,
                        metadataBytes: 4_100, custodyBytes: payload
                    ))
                    XCTAssertTrue(try session.reserve(
                        second: second, events: 1, payloadBytes: payload,
                        metadataBytes: 4_100, custodyBytes: payload
                    ))
                    XCTAssertTrue(try tenant.reserve(
                        second: second, events: 1, payloadBytes: payload,
                        metadataBytes: 4_100, custodyBytes: payload
                    ))
                    XCTAssertTrue(try process.reserve(
                        second: second, events: 1, payloadBytes: payload,
                        metadataBytes: 4_100, custodyBytes: payload
                    ))
                    try sources[source].releaseCustody(payload)
                    try session.releaseCustody(payload)
                    try tenant.releaseCustody(payload)
                    try process.releaseCustody(payload)
                }
            }
            XCTAssertEqual(sources[0].custody, 0)
            XCTAssertEqual(sources[1].custody, 0)
            XCTAssertEqual(session.custody, 0)
            XCTAssertEqual(tenant.custody, 0)
            XCTAssertEqual(process.custody, 0)
        }
    }

    func testEffectRequiresDurableOwnerJournalAndPositiveQuiescence() throws {
        var effect = V2EffectFence()
        let token = try effect.prepare(ownerId: "owner-a")
        XCTAssertEqual(try effect.prepare(ownerId: "owner-a"), token)
        XCTAssertThrowsError(try effect.prepare(ownerId: "owner-b"))
        try effect.invoke(token)
        XCTAssertThrowsError(try effect.invoke(token))
        try effect.providerReturned(token)
        XCTAssertFalse(effect.journalCommitted)
        try effect.commitJournal(token)
        XCTAssertTrue(effect.journalCommitted)
        try effect.recover(runtimeEpoch: 1, egressFence: 1)
        XCTAssertThrowsError(try effect.terminalize())
        effect.acknowledgeProviderClose()
        XCTAssertThrowsError(try effect.terminalize())
        effect.acknowledgeOwnerTermination()
        try effect.terminalize()
        XCTAssertEqual(effect.state, .terminal)
        XCTAssertEqual(effect.invokeCount, 1)
        XCTAssertTrue(effect.journalCommitted)
    }

    func testLifecycleProjectionCannotClaimCompletionFromOneAxis() throws {
        var lifecycle = V2LifecycleProjection()
        XCTAssertEqual(lifecycle.derived, .degraded)
        try lifecycle.companion(version: 1, state: .recording)
        try lifecycle.gatewayTransport(version: 1, state: .forwarding)
        try lifecycle.gatewayCoverage(version: 1, state: .open)
        XCTAssertEqual(lifecycle.derived, .recording)
        try lifecycle.gatewayCoverage(version: 2, state: .completed)
        XCTAssertEqual(lifecycle.derived, .finalizing)
        try lifecycle.companion(version: 2, state: .stopped)
        try lifecycle.gatewayTransport(version: 2, state: .closed)
        XCTAssertEqual(lifecycle.derived, .completed)
        try lifecycle.gatewayCoverage(version: 3, state: .deleteQuiescing)
        XCTAssertEqual(lifecycle.derived, .deleting)
        XCTAssertThrowsError(try lifecycle.companion(version: 2, state: .degraded))
    }
}
