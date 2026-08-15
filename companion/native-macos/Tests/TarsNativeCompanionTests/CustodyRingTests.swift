import Foundation
import XCTest
@testable import TarsNativeCompanion

final class CustodyRingTests: XCTestCase {
    func testCustodyIsRateDerivedAndReleasesLocally() throws {
        let identity = try SourceIdentity(sessionID: "session", streamID: "mic", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        let frame = try GeneratedFixtureSource(identity: identity).makeFrames(count: 1)[0]
        var ring = CustodyRing(limits: try CustodyLimits(sampleRate: 16_000, channelCount: 1))
        XCTAssertTrue(try ring.reserve(CustodyEntry(frame: frame)))
        XCTAssertFalse(try ring.reserve(CustodyEntry(frame: frame)))
        try ring.localDiscard(eventID: frame.eventID, reason: .localPrivacyDiscard)
        XCTAssertEqual(ring.retainedCount, 0)
        XCTAssertEqual(ring.released[frame.eventID], .localPrivacyDiscard)
        XCTAssertEqual(ring.gapObligations[frame.eventID], .localPrivacyDiscard)
    }

    func testEffectFencePreventsDiscardFromClaimingForwarding() throws {
        let identity = try SourceIdentity(sessionID: "session", streamID: "mic", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        let frame = try GeneratedFixtureSource(identity: identity).makeFrames(count: 1)[0]
        var ring = CustodyRing(limits: try CustodyLimits(sampleRate: 16_000, channelCount: 1))
        _ = try ring.reserve(CustodyEntry(frame: frame))
        let token = try ProviderEffectToken(effectID: "effect", ownerGeneration: 1, ownerEpoch: ring.ownerEpoch)
        try ring.prepareEffect(for: frame.eventID, token: token)
        try ring.markEffectInvoking(eventID: frame.eventID, token: token)
        try ring.localDiscard(eventID: frame.eventID, reason: .deletion)
        XCTAssertTrue(ring.pendingEffects.contains(frame.eventID))
        var effect = try XCTUnwrap(ring.effect(for: frame.eventID))
        try effect.markTerminal(journalCommitted: false)
        try ring.markEffectTerminal(eventID: frame.eventID, token: token, journalCommitted: false)
        try ring.resolvePendingEffect(eventID: frame.eventID, token: token, outcome: .ambiguous)
        XCTAssertEqual(ring.gapObligations[frame.eventID], .ambiguousEffect)
    }

    func testPreparedEffectIsCancelledByLocalDiscardAndCannotResume() throws {
        let identity = try SourceIdentity(sessionID: "session", streamID: "mic", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        let frame = try GeneratedFixtureSource(identity: identity).makeFrames(count: 1)[0]
        var ring = CustodyRing(limits: try CustodyLimits(sampleRate: 16_000, channelCount: 1))
        _ = try ring.reserve(CustodyEntry(frame: frame))
        let token = try ProviderEffectToken(effectID: "effect", ownerGeneration: 1, ownerEpoch: ring.ownerEpoch)
        try ring.prepareEffect(for: frame.eventID, token: token)
        try ring.localDiscard(eventID: frame.eventID, reason: .deletion)
        XCTAssertEqual(ring.effect(for: frame.eventID)?.state, .cancelled)
        XCTAssertThrowsError(try ring.markEffectInvoking(eventID: frame.eventID, token: token))
        XCTAssertFalse(ring.pendingEffects.contains(frame.eventID))
    }

    func testAudioFrameCopiesShareZeroizableCustodyBuffer() throws {
        let identity = try SourceIdentity(sessionID: "session", streamID: "mic", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        let frame = try GeneratedFixtureSource(identity: identity).makeFrames(count: 1)[0]
        let frameCopy = frame
        var ring = CustodyRing(limits: try CustodyLimits(sampleRate: 16_000, channelCount: 1))
        _ = try ring.reserve(CustodyEntry(frame: frame))
        try ring.localDiscard(eventID: frame.eventID, reason: .localPrivacyDiscard)
        XCTAssertTrue(frame.payload.copyData().allSatisfy { $0 == 0 })
        XCTAssertTrue(frameCopy.payload.copyData().allSatisfy { $0 == 0 })
    }

    func testProviderEffectCannotTerminalizeBeforeInvocationOrAfterDiscardRace() throws {
        let identity = try SourceIdentity(sessionID: "session", streamID: "mic", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        let frame = try GeneratedFixtureSource(identity: identity).makeFrames(count: 1)[0]
        var ring = CustodyRing(limits: try CustodyLimits(sampleRate: 16_000, channelCount: 1))
        _ = try ring.reserve(CustodyEntry(frame: frame))
        let token = try ProviderEffectToken(effectID: "effect", ownerGeneration: 1, ownerEpoch: ring.ownerEpoch)
        try ring.prepareEffect(for: frame.eventID, token: token)
        XCTAssertThrowsError(try ring.markEffectTerminal(eventID: frame.eventID, token: token, journalCommitted: false))
        try ring.markEffectInvoking(eventID: frame.eventID, token: token)
        try ring.markEffectTerminal(eventID: frame.eventID, token: token, journalCommitted: false)
        XCTAssertTrue(ring.hasPendingProviderEffects)
        XCTAssertThrowsError(try ring.localDiscard(eventID: frame.eventID, reason: .deletion))
        try ring.resolveEffectAmbiguous(eventID: frame.eventID, token: token)
        XCTAssertEqual(ring.gapObligations[frame.eventID], .ambiguousEffect)
        XCTAssertFalse(ring.hasPendingProviderEffects)
    }

    func testForwardedTerminalEffectClearsPendingOwnerState() throws {
        let identity = try SourceIdentity(sessionID: "session", streamID: "mic", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        let frame = try GeneratedFixtureSource(identity: identity).makeFrames(count: 1)[0]
        var ring = CustodyRing(limits: try CustodyLimits(sampleRate: 16_000, channelCount: 1))
        _ = try ring.reserve(CustodyEntry(frame: frame))
        let token = try ProviderEffectToken(effectID: "effect", ownerGeneration: 1, ownerEpoch: ring.ownerEpoch)
        try ring.prepareEffect(for: frame.eventID, token: token)
        try ring.markEffectInvoking(eventID: frame.eventID, token: token)
        try ring.markEffectTerminal(eventID: frame.eventID, token: token, journalCommitted: true)
        try ring.acknowledgeEffectForwarded(eventID: frame.eventID, token: token)
        XCTAssertFalse(ring.hasPendingProviderEffects)
        XCTAssertNil(ring.effect(for: frame.eventID))
        XCTAssertEqual(ring.released[frame.eventID], .forwarded)
    }

    func testEffectGenerationMustMatchCustodyGeneration() throws {
        let identity = try SourceIdentity(sessionID: "session", streamID: "mic", captureGeneration: 2, source: .microphone, sampleRate: 16_000, channelCount: 1)
        let frame = try GeneratedFixtureSource(identity: identity).makeFrames(count: 1)[0]
        var ring = CustodyRing(limits: try CustodyLimits(sampleRate: 16_000, channelCount: 1))
        _ = try ring.reserve(CustodyEntry(frame: frame))
        let stale = try ProviderEffectToken(effectID: "effect", ownerGeneration: 1, ownerEpoch: ring.ownerEpoch)
        XCTAssertThrowsError(try ring.prepareEffect(for: frame.eventID, token: stale))
    }

    func testTwoSecondsIsARealDurationBound() throws {
        let identity = try SourceIdentity(sessionID: "session", streamID: "mic", captureGeneration: 1, source: .microphone, sampleRate: 8_000, channelCount: 1)
        let frames = try GeneratedFixtureSource(identity: identity).makeFrames(count: 100)
        var ring = CustodyRing(limits: try CustodyLimits(sampleRate: 8_000, channelCount: 1))
        for frame in frames.prefix(100) {
            _ = try ring.reserve(CustodyEntry(frame: frame))
        }
        XCTAssertEqual(ring.retainedDurationMs, 2_000)
        XCTAssertThrowsError(try ring.reserve(CustodyEntry(frame: try GeneratedFixtureSource(identity: identity, seed: 99).makeFrames(count: 1)[0])))
    }
}
