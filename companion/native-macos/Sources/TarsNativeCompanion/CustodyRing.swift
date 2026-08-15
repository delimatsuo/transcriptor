import Foundation

public struct CustodyLimits: Equatable, Sendable {
    public let sampleRate: Int
    public let channelCount: Int
    public let maxDurationMs: UInt64
    public let maxFrames: Int
    public let maxPayloadBytes: Int
    public let maxMetadataBytes: Int

    public init(sampleRate: Int, channelCount: Int) throws {
        guard (8_000...48_000).contains(sampleRate), (1...2).contains(channelCount) else {
            throw CompanionError.invalid("custody format is outside the supported bounds")
        }
        self.sampleRate = sampleRate
        self.channelCount = channelCount
        self.maxDurationMs = 2_000
        self.maxFrames = min(96_000, sampleRate * 2)
        self.maxPayloadBytes = min(384_000, self.maxFrames * channelCount * 2)
        self.maxMetadataBytes = 409_600
    }
}

public enum CustodyRelease: String, Equatable, Sendable {
    case forwarded
    case durableDiscard = "durable_discard"
    case localPrivacyDiscard = "local_privacy_discard"
    case privacyTimeout = "privacy_timeout"
    case deletion = "deletion_local"
    case emergency = "emergency_local"
    case forwardedAfterLocalRelease = "forwarded_after_local_release"
    case ambiguousEffect = "ambiguous_effect"
}

public enum ProviderEffectState: Equatable, Sendable {
    case prepared
    case invoking
    case terminal
    case cancelled
}

public struct ProviderEffectToken: Hashable, Equatable, Sendable {
    public let effectID: String
    public let ownerGeneration: UInt64

    public init(effectID: String, ownerGeneration: UInt64) throws {
        guard SourceIdentity.isIdentifier(effectID) else {
            throw CompanionError.invalid("effect identifier is invalid")
        }
        self.effectID = effectID
        self.ownerGeneration = ownerGeneration
    }
}

public struct ProviderEffectFence: Equatable, Sendable {
    public let token: ProviderEffectToken
    public private(set) var state: ProviderEffectState = .prepared
    public private(set) var journalCommitted = false

    public init(token: ProviderEffectToken) {
        self.token = token
    }

    public mutating func markInvoking() throws {
        guard state == .prepared else { throw CompanionError.callbackFenced }
        state = .invoking
    }

    public mutating func markTerminal(journalCommitted: Bool) throws {
        guard state == .invoking || state == .prepared else { throw CompanionError.callbackFenced }
        self.journalCommitted = journalCommitted
        state = .terminal
    }

    public mutating func cancelPrepared() throws {
        guard state == .prepared else { throw CompanionError.callbackFenced }
        state = .cancelled
    }
}

public enum PendingEffectOutcome: Sendable {
    case forwarded
    case ambiguous
}

public struct CustodyEntry: Equatable, Sendable {
    public var frame: AudioFrame
    public let metadataBytes: Int

    public init(frame: AudioFrame, metadataBytes: Int = 256) throws {
        guard (1...4_096).contains(metadataBytes) else {
            throw CompanionError.invalid("metadata size is outside the bound")
        }
        self.frame = frame
        self.metadataBytes = metadataBytes
    }

    public var residentBytes: Int { frame.payload.count + metadataBytes }
}

public struct CustodyRing: Sendable {
    public let limits: CustodyLimits
    private var entries: [String: CustodyEntry] = [:]
    private var releases: [String: CustodyRelease] = [:]
    private var effects: [String: ProviderEffectFence] = [:]
    private var pendingAfterLocalRelease: Set<String> = []
    private var gapReasons: [String: CustodyRelease] = [:]
    public private(set) var acquisitionStopped = false
    public private(set) var lastClockMs: UInt64 = 0

    public init(limits: CustodyLimits) {
        self.limits = limits
    }

    public var retainedCount: Int { entries.count }
    public var retainedPayloadBytes: Int { entries.values.reduce(0) { $0 + $1.frame.payload.count } }
    public var retainedMetadataBytes: Int { entries.values.reduce(0) { $0 + $1.metadataBytes } }
    public var retainedDurationMs: UInt64 {
        entries.values.reduce(0) { $0 + $1.frame.durationMs }
    }
    public var released: [String: CustodyRelease] { releases }
    public var pendingEffects: Set<String> { pendingAfterLocalRelease }
    public var gapObligations: [String: CustodyRelease] { gapReasons }

    @discardableResult
    public mutating func reserve(_ entry: CustodyEntry) throws -> Bool {
        let eventID = entry.frame.eventID
        if let existing = entries[eventID] {
            guard existing == entry else { throw CompanionError.invalid("retry changed frame content") }
            return false
        }
        guard releases[eventID] == nil, !acquisitionStopped, entry.frame.identity.sampleRate == limits.sampleRate,
              entry.frame.identity.channelCount == limits.channelCount else {
            throw CompanionError.invalid("frame does not match active custody format")
        }
        let nextCount = entries.count + 1
        let nextPayload = retainedPayloadBytes + entry.frame.payload.count
        let nextMetadata = retainedMetadataBytes + entry.metadataBytes
        let nextDuration = retainedDurationMs + entry.frame.durationMs
        guard nextCount <= 100, nextPayload <= limits.maxPayloadBytes,
              nextMetadata <= limits.maxMetadataBytes, nextDuration <= limits.maxDurationMs else {
            acquisitionStopped = true
            throw CompanionError.custodyLimitExceeded
        }
        entries[eventID] = entry
        lastClockMs = max(lastClockMs, entry.frame.capturedAtMs)
        return true
    }

    public func entry(for eventID: String) -> CustodyEntry? { entries[eventID] }

    public mutating func prepareEffect(for eventID: String, token: ProviderEffectToken) throws {
        guard entries[eventID] != nil, releases[eventID] == nil,
              effects[eventID] == nil, pendingAfterLocalRelease.isEmpty || !pendingAfterLocalRelease.contains(eventID) else {
            throw CompanionError.invalid("provider effect requires live unreleased custody")
        }
        effects[eventID] = ProviderEffectFence(token: token)
    }

    public func effect(for eventID: String) -> ProviderEffectFence? { effects[eventID] }

    public mutating func markEffectInvoking(eventID: String, token: ProviderEffectToken) throws {
        guard var effect = effects[eventID], effect.token == token else { throw CompanionError.callbackFenced }
        try effect.markInvoking()
        effects[eventID] = effect
    }

    public mutating func markEffectTerminal(eventID: String, token: ProviderEffectToken, journalCommitted: Bool) throws {
        guard var effect = effects[eventID], effect.token == token else { throw CompanionError.callbackFenced }
        try effect.markTerminal(journalCommitted: journalCommitted)
        effects[eventID] = effect
    }

    public mutating func acknowledgeForwarded(eventID: String, journalCommitted: Bool) throws {
        guard effects[eventID] == nil else { throw CompanionError.invalid("effect-bound forwarding requires its fence") }
        guard journalCommitted else { throw CompanionError.invalid("forwarding requires immutable journal acknowledgement") }
        try release(eventID: eventID, reason: .forwarded)
    }

    public mutating func acknowledgeEffectForwarded(eventID: String, token: ProviderEffectToken) throws {
        guard let effect = effects[eventID], effect.token == token, effect.state == .terminal,
              effect.journalCommitted, !pendingAfterLocalRelease.contains(eventID) else {
            throw CompanionError.callbackFenced
        }
        try release(eventID: eventID, reason: .forwarded)
    }

    public mutating func localDiscard(eventID: String, reason: CustodyRelease) throws {
        guard [.localPrivacyDiscard, .privacyTimeout, .deletion, .emergency].contains(reason), releases[eventID] == nil else {
            throw CompanionError.invalid("local discard reason or release state is invalid")
        }
        let hasEffect = effects[eventID] != nil
        try release(eventID: eventID, reason: reason)
        if hasEffect {
            pendingAfterLocalRelease.insert(eventID)
        } else {
            gapReasons[eventID] = reason
        }
    }

    public mutating func cancelPreparedEffectAndDiscard(
        eventID: String,
        token: ProviderEffectToken,
        gapReason: CustodyRelease = .durableDiscard
    ) throws {
        guard var effect = effects[eventID], effect.token == token, effect.state == .prepared,
              pendingAfterLocalRelease.isEmpty || !pendingAfterLocalRelease.contains(eventID) else {
            throw CompanionError.callbackFenced
        }
        try effect.cancelPrepared()
        effects[eventID] = effect
        try release(eventID: eventID, reason: gapReason)
        gapReasons[eventID] = gapReason
    }

    public mutating func resolvePendingEffect(eventID: String, token: ProviderEffectToken, outcome: PendingEffectOutcome) throws {
        guard pendingAfterLocalRelease.contains(eventID), let effect = effects[eventID], effect.token == token,
              effect.state == .terminal else { throw CompanionError.callbackFenced }
        switch outcome {
        case .forwarded:
            guard effect.journalCommitted else { throw CompanionError.invalid("forwarded resolution requires journal") }
            releases[eventID] = .forwardedAfterLocalRelease
        case .ambiguous:
            guard !effect.journalCommitted else { throw CompanionError.invalid("ambiguous effect has a committed journal") }
            gapReasons[eventID] = .ambiguousEffect
        }
        pendingAfterLocalRelease.remove(eventID)
    }

    @discardableResult
    public mutating func advanceClock(_ nowMs: UInt64, clockCertain: Bool = true) throws -> [String] {
        guard nowMs >= lastClockMs else { throw CompanionError.invalid("clock moved backwards") }
        lastClockMs = nowMs
        if !clockCertain {
            acquisitionStopped = true
            let all = entries.keys.sorted()
            for id in all { try localDiscard(eventID: id, reason: .privacyTimeout) }
            return all
        }
        if entries.values.contains(where: { nowMs - $0.frame.capturedAtMs >= 10_000 }) {
            acquisitionStopped = true
        }
        let expired = entries.values.filter { nowMs - $0.frame.capturedAtMs >= 30_000 }.map { $0.frame.eventID }.sorted()
        for id in expired { try localDiscard(eventID: id, reason: .privacyTimeout) }
        return expired
    }

    private mutating func release(eventID: String, reason: CustodyRelease) throws {
        guard var entry = entries.removeValue(forKey: eventID) else {
            guard releases[eventID] == reason else { throw CompanionError.invalid("release references missing or conflicting custody") }
            return
        }
        zeroize(&entry.frame.payload)
        releases[eventID] = reason
    }

    private func zeroize(_ data: inout Data) {
        data.withUnsafeMutableBytes { rawBuffer in
            guard let base = rawBuffer.baseAddress else { return }
            base.initializeMemory(as: UInt8.self, repeating: 0, count: rawBuffer.count)
        }
    }
}
