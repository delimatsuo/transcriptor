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

        var oversizedEvent = try V2CustodyBudget(sampleRateHertz: 48_000, channelCount: 2)
        XCTAssertThrowsError(try oversizedEvent.reserve(V2CustodyReservation(
            eventId: "oversized", frames: 96_000, payloadBytes: 384_000,
            metadataBytes: 472, residentBytes: 384_600, capturedAtMs: 0
        )))
        XCTAssertEqual(oversizedEvent.retainedEvents, 0)
        XCTAssertThrowsError(try oversizedEvent.reserve(V2CustodyReservation(
            eventId: "overflow", frames: Int.max, payloadBytes: Int.max,
            metadataBytes: 472, residentBytes: Int.max, capturedAtMs: 0
        )))
        XCTAssertEqual(oversizedEvent.retainedEvents, 0)

        var expiring = try V2CustodyBudget(sampleRateHertz: 8_000, channelCount: 1)
        try expiring.reserve(V2CustodyReservation(
            eventId: "expiring", frames: 160, payloadBytes: 320,
            metadataBytes: 472, residentBytes: 920, capturedAtMs: 0
        ))
        XCTAssertEqual(try expiring.advanceClock(10_000), [])
        XCTAssertTrue(expiring.acquisitionStopped)
        XCTAssertEqual(try expiring.advanceClock(30_000), ["expiring"])
        XCTAssertEqual(expiring.released["expiring"], "privacy_timeout_local")

        var discarded = try V2CustodyBudget(sampleRateHertz: 8_000, channelCount: 1)
        try discarded.reserve(V2CustodyReservation(
            eventId: "discard", frames: 160, payloadBytes: 320,
            metadataBytes: 472, residentBytes: 920, capturedAtMs: 0
        ))
        try discarded.acknowledgeDurableDiscard("discard", gapId: "gap-1")
        try discarded.acknowledgeDurableDiscard("discard", gapId: "gap-1")
        XCTAssertThrowsError(try discarded.acknowledgeDurableDiscard("discard", gapId: "gap-2"))
        XCTAssertEqual(discarded.discardGapIds["discard"], "gap-1")
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
        var effect = try V2EffectFence(effectId: "effect-1")
        var foreign = try V2EffectFence(effectId: "effect-2")
        let token = try effect.prepare(ownerId: "owner-a")
        _ = try foreign.prepare(ownerId: "owner-a")
        XCTAssertThrowsError(try foreign.invoke(token))
        XCTAssertEqual(try effect.prepare(ownerId: "owner-a"), token)
        XCTAssertThrowsError(try effect.prepare(ownerId: "owner-b"))
        try effect.invoke(token)
        XCTAssertThrowsError(try effect.invoke(token))
        try effect.providerReturned(token)
        XCTAssertFalse(effect.journalCommitted)
        try effect.commitJournal(token)
        XCTAssertTrue(effect.journalCommitted)
        let stale = try effect.recover(
            runtimeEpoch: 1, egressFence: 1,
            providerActorId: "provider-a", ownerActorId: "owner-a"
        )
        let current = try effect.recover(
            runtimeEpoch: 2, egressFence: 2,
            providerActorId: "provider-b", ownerActorId: "owner-a"
        )
        XCTAssertThrowsError(try effect.acknowledgeProviderClose(stale.provider, actorId: "provider-a"))
        XCTAssertThrowsError(try effect.acknowledgeOwnerTermination(stale.owner, actorId: "owner-a"))
        XCTAssertThrowsError(try effect.acknowledgeProviderClose(current.provider, actorId: "provider-a"))
        XCTAssertThrowsError(try effect.terminalize())
        try effect.acknowledgeProviderClose(current.provider, actorId: "provider-b")
        XCTAssertThrowsError(try effect.terminalize())
        try effect.acknowledgeOwnerTermination(current.owner, actorId: "owner-a")
        try effect.terminalize()
        XCTAssertEqual(effect.state, .terminal)
        XCTAssertEqual(effect.invokeCount, 1)
        XCTAssertTrue(effect.journalCommitted)
        XCTAssertThrowsError(try effect.recover(
            runtimeEpoch: 3, egressFence: 3,
            providerActorId: "provider-c", ownerActorId: "owner-a"
        ))
    }

    func testLocalPrivacyReleaseFencesPendingProviderEffect() throws {
        let reservation = V2CustodyReservation(
            eventId: "forwarded", frames: 160, payloadBytes: 320,
            metadataBytes: 472, residentBytes: 920, capturedAtMs: 0
        )
        var preparedCustody = try V2CustodyBudget(sampleRateHertz: 8_000, channelCount: 1)
        try preparedCustody.reserve(V2CustodyReservation(
            eventId: "prepared-discard", frames: 160, payloadBytes: 320,
            metadataBytes: 472, residentBytes: 920, capturedAtMs: 0
        ))
        var prepared = try V2EffectFence(effectId: "effect-prepared-discard")
        let preparedToken = try prepared.prepare(ownerId: "owner-a")
        try preparedCustody.registerEffect(eventId: "prepared-discard", effect: prepared)
        XCTAssertThrowsError(try preparedCustody.acknowledgeDurableDiscard("prepared-discard", gapId: "gap-deletion"))
        var sameIdentity = try V2EffectFence(effectId: "effect-prepared-discard")
        _ = try sameIdentity.prepare(ownerId: "owner-a")
        XCTAssertThrowsError(try preparedCustody.cancelPreparedEffectAndDiscard(
            eventId: "prepared-discard", effect: &sameIdentity, gapId: "gap-deletion"
        ))
        try preparedCustody.cancelPreparedEffectAndDiscard(
            eventId: "prepared-discard", effect: &prepared, gapId: "gap-deletion"
        )
        try preparedCustody.cancelPreparedEffectAndDiscard(
            eventId: "prepared-discard", effect: &prepared, gapId: "gap-deletion"
        )
        XCTAssertTrue(prepared.cancelledWithoutInvoke)
        XCTAssertEqual(preparedCustody.gapObligations["prepared-discard"], "gap-deletion")
        XCTAssertThrowsError(try prepared.invoke(preparedToken))
        XCTAssertThrowsError(try prepared.callback(preparedToken))
        XCTAssertThrowsError(try preparedCustody.invokeEffect(
            eventId: "prepared-discard", effect: &prepared, token: preparedToken
        ))
        var foreignCancelled = try V2EffectFence(effectId: "effect-prepared-discard")
        let foreignCancelledToken = try foreignCancelled.prepare(ownerId: "owner-a")
        try foreignCancelled.cancelPrepared(foreignCancelledToken)
        XCTAssertThrowsError(try preparedCustody.cancelPreparedEffectAndDiscard(
            eventId: "prepared-discard", effect: &foreignCancelled, gapId: "gap-deletion"
        ))

        var custody = try V2CustodyBudget(sampleRateHertz: 8_000, channelCount: 1)
        try custody.reserve(reservation)
        var effect = try V2EffectFence(effectId: "effect-forwarded")
        let token = try effect.prepare(ownerId: "owner-a")
        try custody.registerEffect(eventId: "forwarded", effect: effect)
        try custody.invokeEffect(eventId: "forwarded", effect: &effect, token: token)
        XCTAssertThrowsError(try custody.cancelPreparedEffectAndDiscard(
            eventId: "forwarded", effect: &effect, gapId: "gap-forbidden"
        ))
        XCTAssertThrowsError(try custody.acknowledgeForwarded("forwarded", journalCommitted: true))
        try custody.localPrivacyRelease("forwarded", reason: "emergency_local")
        XCTAssertTrue(custody.effectPendingReleases.contains("forwarded"))
        XCTAssertNil(custody.gapObligations["forwarded"])
        var replacement = try V2EffectFence(effectId: "replacement")
        _ = try replacement.prepare(ownerId: "owner-b")
        XCTAssertThrowsError(try custody.registerEffect(eventId: "forwarded", effect: replacement))
        XCTAssertThrowsError(try custody.acknowledgeDurableDiscard("forwarded", gapId: "gap-forbidden"))
        var foreign = try V2EffectFence(effectId: "effect-forwarded")
        let foreignToken = try foreign.prepare(ownerId: "owner-b")
        try foreign.invoke(foreignToken)
        try foreign.providerReturned(foreignToken)
        try foreign.commitJournal(foreignToken)
        XCTAssertThrowsError(try custody.resolvePendingEffect(eventId: "forwarded", effect: foreign, outcome: .forwarded))
        try effect.providerReturned(token)
        try effect.commitJournal(token)
        try custody.resolvePendingEffect(eventId: "forwarded", effect: effect, outcome: .forwarded)
        XCTAssertTrue(custody.forwarded.contains("forwarded"))
        XCTAssertNil(custody.gapObligations["forwarded"])

        custody = try V2CustodyBudget(sampleRateHertz: 8_000, channelCount: 1)
        try custody.reserve(V2CustodyReservation(
            eventId: "ambiguous", frames: 160, payloadBytes: 320,
            metadataBytes: 472, residentBytes: 920, capturedAtMs: 0
        ))
        effect = try V2EffectFence(effectId: "effect-ambiguous")
        let ambiguousToken = try effect.prepare(ownerId: "owner-a")
        try custody.registerEffect(eventId: "ambiguous", effect: effect)
        try custody.invokeEffect(eventId: "ambiguous", effect: &effect, token: ambiguousToken)
        try custody.localPrivacyRelease("ambiguous", reason: "deletion_local")
        let quiescence = try effect.recover(
            runtimeEpoch: 1, egressFence: 1,
            providerActorId: "provider-a", ownerActorId: "owner-a"
        )
        try effect.acknowledgeProviderClose(quiescence.provider, actorId: "provider-a")
        try effect.acknowledgeOwnerTermination(quiescence.owner, actorId: "owner-a")
        try effect.terminalize()
        try custody.resolvePendingEffect(eventId: "ambiguous", effect: effect, outcome: .ambiguousEffect)
        XCTAssertEqual(custody.gapObligations["ambiguous"], "ambiguous_effect")
        XCTAssertFalse(custody.forwarded.contains("ambiguous"))
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

    func testDeletionRequiresQuiescenceTwoPassesAndFencesLateCallbacks() throws {
        var deletion = try V2DeletionFence(
            participants: ["worker", "connection", "effect"],
            stores: ["session", "retry", "backup"]
        )
        let generation = try deletion.request()
        XCTAssertThrowsError(try deletion.assertAdmissionAllowed())
        try deletion.acknowledge("worker", generation: generation)
        try deletion.acknowledge("connection", generation: generation)
        XCTAssertThrowsError(try deletion.startDeleting())
        try deletion.acknowledge("effect", generation: generation)
        try deletion.startDeleting()

        var restarted = deletion
        let absent = ["session": true, "retry": true, "backup": true]
        XCTAssertTrue(try restarted.recordAbsencePass(1, results: absent))
        XCTAssertFalse(try restarted.recordAbsencePass(
            2,
            results: ["session": true, "retry": true, "backup": false]
        ))
        XCTAssertEqual(restarted.state, .deletionFailed)
        try restarted.resume(generation: generation)
        XCTAssertTrue(try restarted.recordAbsencePass(2, results: absent))
        try restarted.finish()
        XCTAssertEqual(restarted.state, .deleted)
        XCTAssertThrowsError(try restarted.rejectLateCallback(generation: generation))
        XCTAssertEqual(restarted.lateCallbackRejections, 1)
    }

    func testTransportEdgeRejectsPreAuthAudioCountsAndBackwardDeadline() throws {
        var edge = V2TransportEdgeBudget()
        for index in 0..<16 {
            try edge.openPending(
                connectionId: "connection-\(index)", sourceIp: "192.0.2.1", nowMs: 10_000,
                headerBytes: 16_384, firstAuthBytes: 8_192, receiveBytes: 32_768
            )
        }
        let before = edge.pendingBytes
        XCTAssertThrowsError(try edge.openPending(
            connectionId: "connection-16", sourceIp: "192.0.2.1", nowMs: 10_000,
            headerBytes: 1, firstAuthBytes: 1, receiveBytes: 1
        ))
        XCTAssertThrowsError(try edge.rejectPreAuthAudio(declaredBytes: 68_100))
        XCTAssertEqual(edge.pendingBytes, before)
        XCTAssertThrowsError(try edge.authenticate(connectionId: "connection-0", nowMs: 9_999))
        try edge.authenticate(connectionId: "connection-0", nowMs: 18_000)
        XCTAssertEqual(edge.parserBytes, 68_100)
        XCTAssertThrowsError(try edge.authenticate(connectionId: "connection-1", nowMs: 18_001))
    }
}
