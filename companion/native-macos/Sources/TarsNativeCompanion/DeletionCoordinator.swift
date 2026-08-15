import Foundation

public enum DeletionPhase: String, Sendable {
    case idle
    case quiescing
    case localZeroized = "local_zeroized"
    case awaitingGateway = "awaiting_gateway"
    case deleted
    case failed
}

public struct DeletionFence: Hashable, Sendable {
    public let sessionID: String
    public let generation: UInt64
    public let deletionEpoch: UUID

    public init(sessionID: String, generation: UInt64, deletionEpoch: UUID = UUID()) throws {
        guard SourceIdentity.isIdentifier(sessionID) else { throw CompanionError.invalid("deletion session is invalid") }
        self.sessionID = sessionID
        self.generation = generation
        self.deletionEpoch = deletionEpoch
    }
}

public struct DeletionCoordinator: Sendable {
    public private(set) var phase: DeletionPhase = .idle
    public private(set) var activeFence: DeletionFence?
    public private(set) var activeWorkers = 0
    public private(set) var activeStreams = 0
    public private(set) var activeCallbacks = 0
    public private(set) var activeEffects = 0
    public private(set) var lateCallbacksRejected = 0

    public init() {}

    public mutating func begin(fence: DeletionFence) throws {
        guard phase == .idle || phase == .failed else { throw CompanionError.invalidTransition("begin deletion") }
        activeFence = fence
        phase = .quiescing
    }

    public mutating func workerStarted(_ fence: DeletionFence) throws {
        try validateActive(fence)
        guard phase == .quiescing else { throw CompanionError.deletionInProgress }
        activeWorkers += 1
    }

    public mutating func workerStopped(_ fence: DeletionFence) throws {
        try validateActive(fence)
        guard activeWorkers > 0 else { throw CompanionError.invalid("worker underflow") }
        activeWorkers -= 1
    }

    public mutating func streamStarted(_ fence: DeletionFence) throws {
        try validateActive(fence)
        guard phase == .quiescing else { throw CompanionError.deletionInProgress }
        activeStreams += 1
    }

    public mutating func streamStopped(_ fence: DeletionFence) throws {
        try validateActive(fence)
        guard activeStreams > 0 else { throw CompanionError.invalid("stream underflow") }
        activeStreams -= 1
    }

    public mutating func markLocalZeroized(_ fence: DeletionFence) throws {
        try validateActive(fence)
        guard phase == .quiescing, activeWorkers == 0, activeStreams == 0, activeCallbacks == 0, activeEffects == 0 else {
            throw CompanionError.invalidTransition("local zeroization before quiescence")
        }
        phase = .localZeroized
    }

    public mutating func awaitGatewayAcknowledgement(_ fence: DeletionFence) throws {
        try validateActive(fence)
        guard phase == .localZeroized else { throw CompanionError.invalidTransition("gateway acknowledgement") }
        phase = .awaitingGateway
    }

    public mutating func gatewayAcknowledged(_ fence: DeletionFence, deleted: Bool) throws {
        try validateActive(fence)
        guard phase == .awaitingGateway else { throw CompanionError.invalidTransition("gateway deletion acknowledgement") }
        phase = deleted ? .deleted : .failed
    }

    public mutating func acceptCallback(_ fence: DeletionFence) -> Bool {
        guard activeFence == fence, phase == .quiescing else {
            lateCallbacksRejected += 1
            return false
        }
        return true
    }

    public mutating func callbackStarted(_ fence: DeletionFence) throws {
        guard acceptCallback(fence) else { throw CompanionError.callbackFenced }
        activeCallbacks += 1
    }

    public mutating func callbackFinished(_ fence: DeletionFence) throws {
        guard activeFence == fence, activeCallbacks > 0 else { throw CompanionError.callbackFenced }
        activeCallbacks -= 1
    }

    public mutating func effectStarted(_ fence: DeletionFence) throws {
        try validateActive(fence)
        guard phase == .quiescing else { throw CompanionError.deletionInProgress }
        activeEffects += 1
    }

    public mutating func effectFinished(_ fence: DeletionFence) throws {
        try validateActive(fence)
        guard activeEffects > 0 else { throw CompanionError.invalid("effect underflow") }
        activeEffects -= 1
    }

    public mutating func replaceFence(_ fence: DeletionFence) throws {
        guard phase == .failed else { throw CompanionError.invalidTransition("replace deletion fence") }
        guard activeWorkers == 0, activeStreams == 0, activeCallbacks == 0, activeEffects == 0,
              activeFence?.sessionID == fence.sessionID else {
            throw CompanionError.invalidTransition("replace fence while active or across sessions")
        }
        activeFence = fence
        phase = .quiescing
    }

    private func validateActive(_ fence: DeletionFence) throws {
        guard activeFence == fence else { throw CompanionError.callbackFenced }
    }
}
