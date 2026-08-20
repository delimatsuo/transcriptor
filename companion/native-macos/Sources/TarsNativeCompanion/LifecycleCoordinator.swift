import Foundation

public struct LifecycleCoordinator: Sendable {
    public private(set) var physical: PhysicalCaptureState = .setupRequired
    public private(set) var transport: TransportState = .disconnected
    public private(set) var coverage: CoverageState = .notStarted
    public private(set) var sourceHealth: [AudioSource: SourceHealth]
    public private(set) var acceptedFrames = 0
    public private(set) var pendingCallbacks = 0

    public init(sources: [AudioSource] = AudioSource.allCases) {
        // A supported capture session always has both independent health axes;
        // callers cannot silently construct a microphone-only ready state.
        _ = sources
        self.sourceHealth = Dictionary(uniqueKeysWithValues: AudioSource.allCases.map { ($0, SourceHealth()) })
    }

    public mutating func beginPermissionAndDeviceCheck() throws {
        guard physical == .setupRequired || physical == .degraded else {
            throw CompanionError.invalidTransition("permission/device check")
        }
        physical = .checkingPermissionsAndDevices
    }

    public mutating func updateHealth(_ health: SourceHealth, for source: AudioSource) throws {
        guard physical != .deleting else { throw CompanionError.deletionInProgress }
        sourceHealth[source] = health
        if !health.isHealthy {
            physical = .degraded
        } else if sourceHealth.values.allSatisfy(\.isHealthy) && (physical == .checkingPermissionsAndDevices || physical == .degraded || physical == .setupRequired) {
            physical = .readyBothSources
        }
    }

    public mutating func startCapture() throws {
        guard physical == .readyBothSources else { throw CompanionError.invalidTransition("start capture") }
        physical = .starting
        physical = .capturing
        coverage = .open
    }

    public mutating func pause() throws {
        guard physical == .capturing else { throw CompanionError.invalidTransition("pause") }
        physical = .paused
    }

    public mutating func resume() throws {
        guard physical == .paused else { throw CompanionError.invalidTransition("resume") }
        guard sourceHealth.values.allSatisfy(\.isHealthy) else {
            physical = .degraded
            throw CompanionError.invalidTransition("resume requires healthy sources")
        }
        physical = .capturing
    }

    public mutating func stopCapture() throws {
        guard physical == .capturing || physical == .paused || physical == .degraded else {
            throw CompanionError.invalidTransition("stop capture")
        }
        physical = .stopping
        physical = .stopped
        if coverage == .open { coverage = .finalizing }
    }

    public mutating func openTransport() throws {
        guard transport == .disconnected || transport == .closed else {
            throw CompanionError.invalidTransition("open transport")
        }
        transport = .connecting
        transport = .open
    }

    public mutating func beginTransportDrain() throws {
        guard transport == .open else { throw CompanionError.invalidTransition("drain transport") }
        transport = .draining
    }

    public mutating func closeTransport() throws {
        guard transport == .draining || transport == .open || transport == .connecting else {
            throw CompanionError.invalidTransition("close transport")
        }
        transport = .closed
    }

    /// Coverage is gateway-owned. The companion can only observe a gateway state; it cannot assert completion itself.
    public mutating func observeGatewayCoverage(_ state: CoverageState) throws {
        switch state {
        case .open:
            guard physical == .capturing || physical == .paused else {
                throw CompanionError.invalidTransition("gateway open coverage observation")
            }
        case .completed, .completedWithGaps:
            guard physical == .stopped, transport == .closed else {
                throw CompanionError.invalidTransition("gateway terminal coverage before local stop/close")
            }
        case .deleteQuiescing, .deleted, .deletionFailed:
            guard physical == .deleting else {
                throw CompanionError.invalidTransition("gateway deletion state outside local deletion")
            }
        case .notStarted, .finalizing:
            break
        }
        coverage = state
    }

    public mutating func beginDeletion() throws {
        guard physical != .deleting else { return }
        guard physical == .stopped || physical == .degraded || physical == .paused || physical == .capturing else {
            throw CompanionError.invalidTransition("delete")
        }
        physical = .deleting
        coverage = .deleteQuiescing
        if transport == .open || transport == .draining || transport == .connecting {
            transport = .closed
        }
    }

    public mutating func finishDeletion(success: Bool) throws {
        guard physical == .deleting, pendingCallbacks == 0 else {
            throw CompanionError.invalidTransition("finish deletion requires callback quiescence")
        }
        coverage = success ? .deleted : .deletionFailed
        physical = .stopped
    }

    public mutating func recordAcceptedFrame() throws {
        guard physical == .capturing || physical == .paused else { throw CompanionError.invalidTransition("accept frame") }
        acceptedFrames += 1
    }

    public mutating func callbackStarted() throws {
        guard physical != .deleting else { throw CompanionError.callbackFenced }
        pendingCallbacks += 1
    }

    public mutating func callbackFinished() throws {
        guard pendingCallbacks > 0 else { throw CompanionError.invalid("callback underflow") }
        pendingCallbacks -= 1
    }

    public func snapshot() -> CompanionSnapshot {
        CompanionSnapshot(
            physical: physical,
            transport: transport,
            coverage: coverage,
            sourceHealth: sourceHealth,
            acceptedFrames: acceptedFrames,
            pendingCallbacks: pendingCallbacks
        )
    }
}
