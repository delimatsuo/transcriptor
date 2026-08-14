import Foundation

public struct V2CustodyReservation: Equatable, Sendable {
    public let eventId: String
    public let frames: Int
    public let payloadBytes: Int
    public let metadataBytes: Int
    public let residentBytes: Int
    public let capturedAtMs: UInt64

    public init(
        eventId: String,
        frames: Int,
        payloadBytes: Int,
        metadataBytes: Int,
        residentBytes: Int,
        capturedAtMs: UInt64
    ) {
        self.eventId = eventId
        self.frames = frames
        self.payloadBytes = payloadBytes
        self.metadataBytes = metadataBytes
        self.residentBytes = residentBytes
        self.capturedAtMs = capturedAtMs
    }
}

public struct V2CustodyBudget: Sendable {
    public let sampleRateHertz: Int
    public let channelCount: Int
    private var entries: [String: V2CustodyReservation] = [:]
    private var effectIds: [String: String] = [:]
    public private(set) var released: [String: String] = [:]
    public private(set) var discardGapIds: [String: String] = [:]
    public private(set) var gapObligations: [String: String] = [:]
    public private(set) var effectPendingReleases: Set<String> = []
    public private(set) var forwarded: Set<String> = []
    public private(set) var acquisitionStopped = false
    public private(set) var lastClockMs: UInt64 = 0

    public init(sampleRateHertz: Int, channelCount: Int) throws {
        guard (8_000...48_000).contains(sampleRateHertz), (1...2).contains(channelCount) else {
            throw ProtocolV2ValidationError.invalid("custody format is outside v2 bounds")
        }
        self.sampleRateHertz = sampleRateHertz
        self.channelCount = channelCount
    }

    public var retainedEvents: Int { entries.count }
    public var retainedFrames: Int { entries.values.reduce(0) { $0 + $1.frames } }
    public var retainedPayloadBytes: Int { entries.values.reduce(0) { $0 + $1.payloadBytes } }
    public var retainedMetadataBytes: Int { entries.values.reduce(0) { $0 + $1.metadataBytes } }
    public var retainedResidentBytes: Int { entries.values.reduce(0) { $0 + $1.residentBytes } }

    @discardableResult
    public mutating func reserve(_ value: V2CustodyReservation) throws -> Bool {
        if let existing = entries[value.eventId] {
            guard existing == value else {
                throw ProtocolV2ValidationError.invalid("custody retry changed content")
            }
            return false
        }
        guard released[value.eventId] == nil, !acquisitionStopped,
              !value.eventId.isEmpty, value.frames > 0,
              value.payloadBytes > 0, value.payloadBytes <= 64_000,
              (1...4_096).contains(value.metadataBytes),
              value.residentBytes >= 0,
              value.capturedAtMs >= lastClockMs else {
            throw ProtocolV2ValidationError.invalid("custody reservation is invalid")
        }
        let (expectedPayloadBytes, payloadOverflow) = value.frames.multipliedReportingOverflow(by: channelCount * 2)
        let (durationNumerator, durationOverflow) = value.frames.multipliedReportingOverflow(by: 1_000)
        let (minimumResidentBytes, residentOverflow) = value.payloadBytes.addingReportingOverflow(value.metadataBytes)
        guard !payloadOverflow, !durationOverflow, !residentOverflow,
              value.payloadBytes == expectedPayloadBytes,
              durationNumerator % sampleRateHertz == 0,
              (20...250).contains(durationNumerator / sampleRateHertz),
              value.residentBytes >= minimumResidentBytes else {
            throw ProtocolV2ValidationError.invalid("custody event is outside framing bounds")
        }
        let maxFrames = min(96_000, 2 * sampleRateHertz)
        let maxPayload = min(384_000, maxFrames * channelCount * 2)
        let (nextEvents, eventsOverflow) = retainedEvents.addingReportingOverflow(1)
        let (nextFrames, framesOverflow) = retainedFrames.addingReportingOverflow(value.frames)
        let (nextPayload, aggregatePayloadOverflow) = retainedPayloadBytes.addingReportingOverflow(value.payloadBytes)
        let (nextMetadata, metadataOverflow) = retainedMetadataBytes.addingReportingOverflow(value.metadataBytes)
        let (nextResident, aggregateResidentOverflow) = retainedResidentBytes.addingReportingOverflow(value.residentBytes)
        guard !eventsOverflow, !framesOverflow, !aggregatePayloadOverflow, !metadataOverflow, !aggregateResidentOverflow,
              nextEvents <= 100,
              nextFrames <= maxFrames,
              nextPayload <= maxPayload,
              nextMetadata <= 409_600,
              nextResident <= 1_048_576 else {
            acquisitionStopped = true
            throw ProtocolV2ValidationError.invalid("custody reservation exceeds a frozen bound")
        }
        entries[value.eventId] = value
        lastClockMs = value.capturedAtMs
        return true
    }

    public mutating func acknowledgeForwarded(_ eventId: String, journalCommitted: Bool) throws {
        guard effectIds[eventId] == nil else {
            throw ProtocolV2ValidationError.invalid("registered provider effect requires effect-bound forwarding")
        }
        guard journalCommitted else {
            throw ProtocolV2ValidationError.invalid("forwarding release requires immutable journal")
        }
        try release(eventId, outcome: "forwarded")
        forwarded.insert(eventId)
    }

    public mutating func acknowledgeEffectForwarded(_ eventId: String, effect: V2EffectFence) throws {
        guard effectIds[eventId] == effect.effectId, effect.journalCommitted else {
            throw ProtocolV2ValidationError.invalid("effect-bound forwarding requires the original immutable journal")
        }
        guard !effectPendingReleases.contains(eventId) else {
            throw ProtocolV2ValidationError.invalid("locally released custody requires pending-effect resolution")
        }
        try release(eventId, outcome: "forwarded")
        forwarded.insert(eventId)
    }

    public mutating func acknowledgeDurableDiscard(_ eventId: String, gapId: String) throws {
        guard !gapId.isEmpty, !forwarded.contains(eventId), !effectPendingReleases.contains(eventId) else {
            throw ProtocolV2ValidationError.invalid("durable discard conflicts with forwarding")
        }
        if let existing = discardGapIds[eventId], existing != gapId {
            throw ProtocolV2ValidationError.invalid("durable discard identity replay conflicts")
        }
        try release(eventId, outcome: "durable_discard")
        discardGapIds[eventId] = gapId
        gapObligations[eventId] = gapId
    }

    public mutating func registerEffect(eventId: String, effect: V2EffectFence) throws {
        guard entries[eventId] != nil, released[eventId] == nil,
              gapObligations[eventId] == nil, effect.state == .prepared,
              effect.ownerId != nil, effect.token != nil else {
            throw ProtocolV2ValidationError.invalid("provider effect requires live unreleased custody")
        }
        if let existing = effectIds[eventId] {
            guard existing == effect.effectId else {
                throw ProtocolV2ValidationError.invalid("range already has a different provider effect")
            }
            return
        }
        effectIds[eventId] = effect.effectId
    }

    public mutating func invokeEffect(
        eventId: String,
        effect: inout V2EffectFence,
        token: V2EffectToken
    ) throws {
        guard entries[eventId] != nil, effectIds[eventId] == effect.effectId,
              effect.state == .prepared, effect.ownerId != nil else {
            throw ProtocolV2ValidationError.invalid("provider invocation requires registered live custody")
        }
        try effect.invoke(token)
    }

    public mutating func localPrivacyRelease(_ eventId: String, reason: String) throws {
        guard ["privacy_timeout_local", "deletion_local", "emergency_local"].contains(reason),
              !forwarded.contains(eventId) else {
            throw ProtocolV2ValidationError.invalid("local privacy release is invalid")
        }
        try release(eventId, outcome: reason)
        if effectIds[eventId] != nil {
            effectPendingReleases.insert(eventId)
        } else {
            gapObligations[eventId] = reason
        }
    }

    public mutating func resolvePendingEffect(
        eventId: String,
        effect: V2EffectFence,
        outcome: V2PendingEffectOutcome
    ) throws {
        guard effectPendingReleases.contains(eventId), effectIds[eventId] == effect.effectId else {
            throw ProtocolV2ValidationError.invalid("pending effect resolution is stale or foreign")
        }
        switch outcome {
        case .forwarded:
            guard effect.journalCommitted else {
                throw ProtocolV2ValidationError.invalid("forwarded resolution requires immutable journal")
            }
            forwarded.insert(eventId)
            released[eventId] = "forwarded_after_local_release"
        case .ambiguousEffect:
            guard effect.state == .terminal, !effect.journalCommitted else {
                throw ProtocolV2ValidationError.invalid("ambiguous resolution requires unforwarded positive quiescence")
            }
            gapObligations[eventId] = "ambiguous_effect"
        }
        effectPendingReleases.remove(eventId)
    }

    public mutating func advanceClock(_ nowMs: UInt64, clockCertain: Bool = true) throws -> [String] {
        guard nowMs >= lastClockMs else {
            throw ProtocolV2ValidationError.invalid("custody clock moved backwards")
        }
        lastClockMs = nowMs
        if !clockCertain {
            acquisitionStopped = true
            let all = entries.keys.sorted()
            for eventId in all { try localPrivacyRelease(eventId, reason: "privacy_timeout_local") }
            return all
        }
        if entries.values.contains(where: { nowMs - $0.capturedAtMs >= 10_000 }) {
            acquisitionStopped = true
        }
        let expired = entries.values
            .filter { nowMs - $0.capturedAtMs >= 30_000 }
            .map(\.eventId)
            .sorted()
        for eventId in expired { try localPrivacyRelease(eventId, reason: "privacy_timeout_local") }
        return expired
    }

    private mutating func release(_ eventId: String, outcome: String) throws {
        if entries.removeValue(forKey: eventId) == nil {
            guard released[eventId] == outcome else {
                throw ProtocolV2ValidationError.invalid("release references absent or conflicting custody")
            }
            return
        }
        released[eventId] = outcome
    }
}

public enum V2PendingEffectOutcome: Sendable {
    case forwarded
    case ambiguousEffect
}

public struct V2QuotaLimits: Sendable {
    public let eventRate: Int
    public let eventBurst: Int
    public let payloadRate: Int
    public let payloadBurst: Int
    public let metadataRate: Int
    public let metadataBurst: Int
    public let custodyBytes: Int

    public init(
        eventRate: Int,
        eventBurst: Int,
        payloadRate: Int,
        payloadBurst: Int,
        metadataRate: Int,
        metadataBurst: Int,
        custodyBytes: Int
    ) throws {
        let values = [eventRate, eventBurst, payloadRate, payloadBurst, metadataRate, metadataBurst, custodyBytes]
        guard values.allSatisfy({ $0 >= 0 }) else {
            throw ProtocolV2ValidationError.invalid("quota limit is negative")
        }
        self.eventRate = eventRate
        self.eventBurst = eventBurst
        self.payloadRate = payloadRate
        self.payloadBurst = payloadBurst
        self.metadataRate = metadataRate
        self.metadataBurst = metadataBurst
        self.custodyBytes = custodyBytes
    }
}

public struct V2TokenBucket: Sendable {
    public let limits: V2QuotaLimits
    public private(set) var events: Int
    public private(set) var payload: Int
    public private(set) var metadata: Int
    public private(set) var custody = 0
    public private(set) var lastSecond = 0

    public init(limits: V2QuotaLimits) {
        self.limits = limits
        events = limits.eventBurst
        payload = limits.payloadBurst
        metadata = limits.metadataBurst
    }

    @discardableResult
    public mutating func reserve(
        second: Int,
        events requestedEvents: Int,
        payloadBytes: Int,
        metadataBytes: Int,
        custodyBytes: Int
    ) throws -> Bool {
        guard second >= lastSecond,
              [requestedEvents, payloadBytes, metadataBytes, custodyBytes].allSatisfy({ $0 >= 0 }) else {
            throw ProtocolV2ValidationError.invalid("quota reservation is invalid")
        }
        let elapsed = second - lastSecond
        if elapsed > 0 {
            let (eventRefill, eventRefillOverflow) = elapsed.multipliedReportingOverflow(by: limits.eventRate)
            let (payloadRefill, payloadRefillOverflow) = elapsed.multipliedReportingOverflow(by: limits.payloadRate)
            let (metadataRefill, metadataRefillOverflow) = elapsed.multipliedReportingOverflow(by: limits.metadataRate)
            let (refilledEvents, eventAddOverflow) = events.addingReportingOverflow(eventRefill)
            let (refilledPayload, payloadAddOverflow) = payload.addingReportingOverflow(payloadRefill)
            let (refilledMetadata, metadataAddOverflow) = metadata.addingReportingOverflow(metadataRefill)
            guard !eventRefillOverflow, !payloadRefillOverflow, !metadataRefillOverflow,
                  !eventAddOverflow, !payloadAddOverflow, !metadataAddOverflow else {
                throw ProtocolV2ValidationError.invalid("quota refill overflow")
            }
            events = min(limits.eventBurst, refilledEvents)
            payload = min(limits.payloadBurst, refilledPayload)
            metadata = min(limits.metadataBurst, refilledMetadata)
            lastSecond = second
        }
        let (nextCustody, custodyOverflow) = custody.addingReportingOverflow(custodyBytes)
        guard !custodyOverflow else {
            throw ProtocolV2ValidationError.invalid("quota custody overflow")
        }
        let allowed = events >= requestedEvents && payload >= payloadBytes && metadata >= metadataBytes &&
            nextCustody <= limits.custodyBytes
        events = max(0, events - requestedEvents)
        payload = max(0, payload - payloadBytes)
        metadata = max(0, metadata - metadataBytes)
        if allowed { custody = nextCustody }
        return allowed
    }

    public mutating func releaseCustody(_ bytes: Int) throws {
        guard bytes >= 0, bytes <= custody else {
            throw ProtocolV2ValidationError.invalid("custody release exceeds reservation")
        }
        custody -= bytes
    }
}

public struct V2EffectToken: Equatable, Sendable {
    public let effectId: String
    public let runtimeEpoch: UInt64
    public let egressFence: UInt64
    public let ownerId: String
}

public enum V2EffectState: String, Sendable {
    case prepared
    case invoking
    case providerReturned = "provider_returned"
    case journaled
    case effectQuiescenceRequired = "effect_quiescence_required"
    case terminal
}

public struct V2EffectFence: Sendable {
    public let effectId: String
    public private(set) var state: V2EffectState = .prepared
    public private(set) var runtimeEpoch: UInt64 = 0
    public private(set) var egressFence: UInt64 = 0
    public private(set) var ownerId: String?
    public private(set) var token: V2EffectToken?
    public private(set) var invokeCount = 0
    public private(set) var journalCommitted = false
    public private(set) var providerClosed = false
    public private(set) var ownerTerminated = false

    public init(effectId: String) throws {
        guard !effectId.isEmpty else {
            throw ProtocolV2ValidationError.invalid("effect identity is required")
        }
        self.effectId = effectId
    }

    public mutating func prepare(ownerId: String) throws -> V2EffectToken {
        guard state == .prepared, !ownerId.isEmpty else {
            throw ProtocolV2ValidationError.invalid("effect is not prepareable")
        }
        if let existing = self.ownerId {
            guard existing == ownerId, let token else {
                throw ProtocolV2ValidationError.invalid("effect already has a durable owner")
            }
            return token
        }
        let created = V2EffectToken(
            effectId: effectId,
            runtimeEpoch: runtimeEpoch,
            egressFence: egressFence,
            ownerId: ownerId
        )
        self.ownerId = ownerId
        token = created
        return created
    }

    public mutating func invoke(_ presented: V2EffectToken) throws {
        try check(presented)
        guard state == .prepared else {
            throw ProtocolV2ValidationError.invalid("effect invocation is not single-use")
        }
        state = .invoking
        invokeCount += 1
    }

    public mutating func providerReturned(_ presented: V2EffectToken) throws {
        try check(presented)
        guard state == .invoking else {
            throw ProtocolV2ValidationError.invalid("provider return is out of order")
        }
        state = .providerReturned
    }

    public mutating func commitJournal(_ presented: V2EffectToken) throws {
        try check(presented)
        guard state == .providerReturned else {
            throw ProtocolV2ValidationError.invalid("journal is out of order")
        }
        journalCommitted = true
        state = .journaled
    }

    public mutating func recover(runtimeEpoch: UInt64, egressFence: UInt64) throws {
        guard state != .terminal,
              runtimeEpoch > self.runtimeEpoch, egressFence > self.egressFence else {
            throw ProtocolV2ValidationError.invalid("recovery epoch and fence must advance")
        }
        self.runtimeEpoch = runtimeEpoch
        self.egressFence = egressFence
        state = .effectQuiescenceRequired
    }

    public mutating func acknowledgeProviderClose() { providerClosed = true }
    public mutating func acknowledgeOwnerTermination() { ownerTerminated = true }

    public mutating func terminalize() throws {
        guard state == .effectQuiescenceRequired, providerClosed, ownerTerminated else {
            throw ProtocolV2ValidationError.invalid("positive quiescence is required")
        }
        state = .terminal
    }

    private func check(_ presented: V2EffectToken) throws {
        guard presented == token,
              presented.runtimeEpoch == runtimeEpoch,
              presented.egressFence == egressFence else {
            throw ProtocolV2ValidationError.invalid("effect token is stale or foreign")
        }
    }
}

public enum V2PhysicalState: String, Sendable {
    case setupRequired = "setup_required"
    case checkingPermissionsAndDevices = "checking_permissions_and_devices"
    case readyBothSources = "ready_both_sources"
    case starting, recording, degraded, reconnecting, paused, stopping, stopped
}
public enum V2TransportState: String, Sendable {
    case disconnected, admitting, forwarding, draining, fenced, closed
}
public enum V2CoverageState: String, Sendable {
    case notStarted = "not_started", open, finalizing, completed, completedWithGaps = "completed_with_gaps"
    case deleteQuiescing = "delete_quiescing", deleting, deleted, deletionFailed = "deletion_failed"
}
public enum V2DisplayState: String, Sendable {
    case recording, degraded, finalizing, completed, completedWithGaps = "completed_with_gaps"
    case deleting, deleted, deletionFailed = "deletion_failed"
}

public struct V2LifecycleProjection: Sendable {
    private var physical: (UInt64, V2PhysicalState)?
    private var transport: (UInt64, V2TransportState)?
    private var coverage: (UInt64, V2CoverageState)?

    public init() {}

    public mutating func companion(version: UInt64, state: V2PhysicalState) throws {
        guard physical == nil || version > physical!.0 else {
            throw ProtocolV2ValidationError.invalid("physical state version is stale")
        }
        physical = (version, state)
    }

    public mutating func gatewayTransport(version: UInt64, state: V2TransportState) throws {
        guard transport == nil || version > transport!.0 else {
            throw ProtocolV2ValidationError.invalid("transport state version is stale")
        }
        transport = (version, state)
    }

    public mutating func gatewayCoverage(version: UInt64, state: V2CoverageState) throws {
        guard coverage == nil || version > coverage!.0 else {
            throw ProtocolV2ValidationError.invalid("coverage state version is stale")
        }
        coverage = (version, state)
    }

    public var derived: V2DisplayState {
        if coverage?.1 == .deleted { return .deleted }
        if coverage?.1 == .deletionFailed { return .deletionFailed }
        if coverage?.1 == .deleteQuiescing || coverage?.1 == .deleting { return .deleting }
        if coverage?.1 == .completed || coverage?.1 == .completedWithGaps {
            guard physical?.1 == .stopped, transport?.1 == .closed else { return .finalizing }
            return coverage?.1 == .completed ? .completed : .completedWithGaps
        }
        if coverage?.1 == .finalizing || transport?.1 == .draining { return .finalizing }
        if physical?.1 == .recording, transport?.1 == .forwarding, coverage?.1 == .open { return .recording }
        return .degraded
    }
}

public enum V2DeletionState: String, Sendable {
    case active, deleteQuiescing = "delete_quiescing", deleting, deleted, deletionFailed = "deletion_failed"
}

public struct V2DeletionFence: Sendable {
    public private(set) var state: V2DeletionState = .active
    public private(set) var generation: UInt64 = 0
    public let participants: Set<String>
    public let stores: Set<String>
    public private(set) var acknowledgements: Set<String> = []
    public private(set) var absencePasses: [Int: [String: Bool]] = [:]
    public private(set) var lateCallbackRejections = 0

    public init(participants: Set<String>, stores: Set<String>) throws {
        guard participants.allSatisfy({ !$0.isEmpty }), stores.allSatisfy({ !$0.isEmpty }) else {
            throw ProtocolV2ValidationError.invalid("deletion participant or store identity is invalid")
        }
        self.participants = participants
        self.stores = stores
    }

    public mutating func request() throws -> UInt64 {
        guard state == .active, generation < UInt64.max else {
            throw ProtocolV2ValidationError.invalid("deletion request is out of order")
        }
        generation += 1
        state = .deleteQuiescing
        return generation
    }

    public func assertAdmissionAllowed() throws {
        guard state == .active else {
            throw ProtocolV2ValidationError.invalid("admission is fenced during deletion")
        }
    }

    public mutating func acknowledge(_ participant: String, generation: UInt64) throws {
        guard state == .deleteQuiescing, generation == self.generation,
              participants.contains(participant) else {
            throw ProtocolV2ValidationError.invalid("deletion acknowledgement is stale or foreign")
        }
        acknowledgements.insert(participant)
    }

    public mutating func startDeleting() throws {
        guard state == .deleteQuiescing, acknowledgements == participants else {
            throw ProtocolV2ValidationError.invalid("positive deletion quiescence is required")
        }
        state = .deleting
    }

    @discardableResult
    public mutating func recordAbsencePass(_ number: Int, results: [String: Bool]) throws -> Bool {
        guard state == .deleting, number == 1 || number == 2,
              number == 1 || absencePasses[1] != nil,
              Set(results.keys) == stores else {
            throw ProtocolV2ValidationError.invalid("absence pass is out of order or incomplete")
        }
        if let existing = absencePasses[number] {
            guard existing == results else {
                throw ProtocolV2ValidationError.invalid("absence pass replay conflicts")
            }
            return true
        }
        guard results.values.allSatisfy({ $0 }) else {
            state = .deletionFailed
            return false
        }
        absencePasses[number] = results
        return true
    }

    public mutating func resume(generation: UInt64) throws {
        guard state == .deletionFailed, generation == self.generation else {
            throw ProtocolV2ValidationError.invalid("deletion resume is stale")
        }
        state = .deleting
    }

    public mutating func rejectLateCallback(generation: UInt64) throws {
        guard state != .active, generation <= self.generation else {
            throw ProtocolV2ValidationError.invalid("callback generation is not fenced")
        }
        lateCallbackRejections += 1
        throw ProtocolV2ValidationError.invalid("late callback rejected before persistence")
    }

    public mutating func finish() throws {
        guard state == .deleting, Set(absencePasses.keys) == Set([1, 2]) else {
            throw ProtocolV2ValidationError.invalid("two absence passes are required")
        }
        state = .deleted
    }
}

public struct V2TransportEdgeBudget: Sendable {
    private struct Pending: Sendable {
        let sourceIp: String
        let startedAtMs: UInt64
        let receiveBytes: Int
    }

    private var pending: [String: Pending] = [:]
    private var authenticated: Set<String> = []

    public init() {}

    public var pendingBytes: Int { pending.values.reduce(0) { $0 + $1.receiveBytes } }
    public var parserBytes: Int { authenticated.count * 68_100 }

    public mutating func openPending(
        connectionId: String,
        sourceIp: String,
        nowMs: UInt64,
        headerBytes: Int,
        firstAuthBytes: Int,
        receiveBytes: Int
    ) throws {
        let (nextPendingBytes, overflow) = pendingBytes.addingReportingOverflow(receiveBytes)
        guard !overflow, pending[connectionId] == nil, !authenticated.contains(connectionId),
              !connectionId.isEmpty, !sourceIp.isEmpty,
              (0...16_384).contains(headerBytes),
              (0...8_192).contains(firstAuthBytes),
              (0...32_768).contains(receiveBytes),
              pending.count < 64,
              pending.values.filter({ $0.sourceIp == sourceIp }).count < 16,
              nextPendingBytes <= 2_097_152 else {
            throw ProtocolV2ValidationError.invalid("pending transport bound exceeded")
        }
        pending[connectionId] = Pending(sourceIp: sourceIp, startedAtMs: nowMs, receiveBytes: receiveBytes)
    }

    public func rejectPreAuthAudio(declaredBytes: Int) throws {
        guard declaredBytes >= 0 else {
            throw ProtocolV2ValidationError.invalid("declared audio length is invalid")
        }
        throw ProtocolV2ValidationError.invalid("audio is rejected before authentication")
    }

    public mutating func authenticate(connectionId: String, nowMs: UInt64) throws {
        guard let value = pending[connectionId], nowMs >= value.startedAtMs,
              nowMs - value.startedAtMs <= 8_000, authenticated.count < 16 else {
            throw ProtocolV2ValidationError.invalid("authentication deadline or connection bound exceeded")
        }
        pending.removeValue(forKey: connectionId)
        authenticated.insert(connectionId)
    }
}
