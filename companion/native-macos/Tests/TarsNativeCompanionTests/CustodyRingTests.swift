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
        let token = try ProviderEffectToken(effectID: "effect", ownerGeneration: 1)
        try ring.prepareEffect(for: frame.eventID, token: token)
        try ring.localDiscard(eventID: frame.eventID, reason: .deletion)
        XCTAssertTrue(ring.pendingEffects.contains(frame.eventID))
        var effect = try XCTUnwrap(ring.effect(for: frame.eventID))
        try effect.markTerminal(journalCommitted: false)
        try ring.markEffectTerminal(eventID: frame.eventID, token: token, journalCommitted: false)
        try ring.resolvePendingEffect(eventID: frame.eventID, token: token, outcome: .ambiguous)
        XCTAssertEqual(ring.gapObligations[frame.eventID], .ambiguousEffect)
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
