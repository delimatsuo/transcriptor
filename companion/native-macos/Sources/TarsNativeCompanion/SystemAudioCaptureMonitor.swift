import CoreAudio
import Foundation
import TarsRealtimeAudioBridge

public enum SystemAudioCaptureFailure: Error, Equatable, Sendable, CustomStringConvertible {
    case denied
    case ambiguous
    case malformed(String)
    case unsupportedFormat(String)
    case routeFailure(String)
    case cleanupFailure(String)

    public var description: String {
        switch self {
        case .denied: return SystemAudioCaptureMonitor.permissionDeniedMessage
        case .ambiguous: return SystemAudioCaptureMonitor.ambiguousCaptureMessage
        case .malformed(let message): return "Falha de captura de áudio: \(message)"
        case .unsupportedFormat(let message): return "Formato de áudio não suportado: \(message)"
        case .routeFailure(let message): return "Rota de áudio indisponível: \(message)"
        case .cleanupFailure(let message): return "Falha ao liberar a captura de áudio: \(message)"
        }
    }
}

public enum SystemAudioCaptureMonitorAction: Equatable, Sendable {
    case none
    case permissionUnknown
    case granted
    case denied(String)
    case ambiguous(String)
    case rebuild(reason: String)
    case failed(String)
}

public struct SystemAudioCaptureCounterSnapshot: Equatable, Sendable {
    public let callbackArrivals: UInt64
    public let validNonemptyArrivals: UInt64
    public let emptyArrivals: UInt64
    public let malformedArrivals: UInt64
    public let capacityRejectedArrivals: UInt64
    public let ringOverflowCount: UInt64
    public let ringOverflowMetadataDrops: UInt64
    public let cursorOverflow: UInt64

    public init(
        callbackArrivals: UInt64 = 0,
        validNonemptyArrivals: UInt64 = 0,
        emptyArrivals: UInt64 = 0,
        malformedArrivals: UInt64 = 0,
        capacityRejectedArrivals: UInt64 = 0,
        ringOverflowCount: UInt64 = 0,
        ringOverflowMetadataDrops: UInt64 = 0,
        cursorOverflow: UInt64 = 0
    ) {
        self.callbackArrivals = callbackArrivals
        self.validNonemptyArrivals = validNonemptyArrivals
        self.emptyArrivals = emptyArrivals
        self.malformedArrivals = malformedArrivals
        self.capacityRejectedArrivals = capacityRejectedArrivals
        self.ringOverflowCount = ringOverflowCount
        self.ringOverflowMetadataDrops = ringOverflowMetadataDrops
        self.cursorOverflow = cursorOverflow
    }
}

/// Probe/watchdog state is independent from delivery.  In particular, a
/// valid silent callback cancels the no-buffer deadline even if its ring slot
/// is later rejected by backpressure.
public final class SystemAudioCaptureMonitor: @unchecked Sendable {
    public static let permissionDeniedMessage =
        "O macOS negou a captura de áudio do sistema. Autorize o TarsCompanion em Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema e tente novamente."
    public static let ambiguousCaptureMessage =
        "Nenhum áudio verificável foi recebido. Isso não confirma uma permissão negada: a causa também pode ser silêncio, ausência de áudio sendo reproduzido, rota indisponível ou uma falha do sistema de áudio. Verifique se há áudio sendo reproduzido e se o TarsCompanion está autorizado em Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema."

    private let lock = NSLock()
    public let deadlineNanoseconds: UInt64
    private var generationValue: UInt64
    private var permissionValue: PermissionState = .unknown
    private var deadlineArmedValue = false
    private var rebuildUsedValue = false
    private var baseline = SystemAudioCaptureCounterSnapshot()
    private var terminalAction: SystemAudioCaptureMonitorAction = .none
    private var deadlineGeneration: UInt64 = 0
    private var deadlineAt: UInt64 = 0

    /// All externally visible monitor state is read through the same lock
    /// that guards its mutations.  The monitor is shared by the source's
    /// drain, watchdog, and listener tasks, so synthesized `private(set)`
    /// storage would still be an unsynchronized cross-queue read.
    public var generation: UInt64 { lock.withLock { generationValue } }
    public var permission: PermissionState { lock.withLock { permissionValue } }
    public var deadlineArmed: Bool { lock.withLock { deadlineArmedValue } }
    public var rebuildUsed: Bool { lock.withLock { rebuildUsedValue } }

    public init(generation: UInt64 = 1, deadlineNanoseconds: UInt64 = 2_000_000_000) {
        self.generationValue = generation
        self.deadlineNanoseconds = deadlineNanoseconds
    }

    /// Arms before `AudioDeviceStart`, preserving callbacks that can arrive
    /// synchronously during start.
    @discardableResult
    public func arm(generation: UInt64, counters: SystemAudioCaptureCounterSnapshot, nowNanoseconds: UInt64) -> UInt64 {
        lock.withLock {
            self.generationValue = generation
            baseline = counters
            deadlineGeneration = generation
            deadlineAt = Self.saturatingDeadline(nowNanoseconds, after: deadlineNanoseconds)
            deadlineArmedValue = true
            terminalAction = .none
            permissionValue = .unknown
            return deadlineAt
        }
    }

    public func arm(generation: UInt64, counters: TarsRealtimeCounters, nowNanoseconds: UInt64) -> UInt64 {
        arm(generation: generation, counters: SystemAudioCaptureCounterSnapshot(
            callbackArrivals: counters.callbackArrivals,
            validNonemptyArrivals: counters.validNonemptyArrivals,
            emptyArrivals: counters.emptyArrivals,
            malformedArrivals: counters.malformedArrivals,
            capacityRejectedArrivals: counters.capacityRejectedArrivals,
            ringOverflowCount: counters.ringOverflowCount,
            ringOverflowMetadataDrops: counters.ringOverflowMetadataDrops,
            cursorOverflow: counters.cursorOverflow
        ), nowNanoseconds: nowNanoseconds)
    }

    public func cancelDeadline() {
        lock.withLock { deadlineArmedValue = false }
    }

    public func observeCounters(_ counters: SystemAudioCaptureCounterSnapshot) -> SystemAudioCaptureMonitorAction {
        lock.withLock {
            guard terminalAction == .none else { return terminalAction }
            if counters.malformedArrivals > baseline.malformedArrivals {
                deadlineArmedValue = false
                terminalAction = .failed("O Process Tap recebeu um descritor de áudio malformado; a captura foi encerrada.")
                return terminalAction
            }
            if counters.capacityRejectedArrivals > baseline.capacityRejectedArrivals {
                deadlineArmedValue = false
                terminalAction = .failed("O Process Tap recebeu um buffer maior que a capacidade realtime pré-alocada; a captura foi encerrada.")
                return terminalAction
            }
            if counters.ringOverflowMetadataDrops > baseline.ringOverflowMetadataDrops {
                deadlineArmedValue = false
                terminalAction = .failed("O Process Tap excedeu a capacidade da evidência realtime de overflow; a captura foi encerrada.")
                return terminalAction
            }
            if counters.cursorOverflow > baseline.cursorOverflow {
                deadlineArmedValue = false
                terminalAction = .failed("O cursor realtime da captura atingiu o limite suportado; a captura foi encerrada antes de uma ambiguidade de ordem.")
                return terminalAction
            }
            if counters.validNonemptyArrivals > baseline.validNonemptyArrivals {
                // Structural liveness is enough to cancel the watchdog.  The
                // caller separately reports functional nonzero signal.
                deadlineArmedValue = false
            }
            return .none
        }
    }

    public func observeCounters(_ counters: TarsRealtimeCounters) -> SystemAudioCaptureMonitorAction {
        observeCounters(SystemAudioCaptureCounterSnapshot(
            callbackArrivals: counters.callbackArrivals,
            validNonemptyArrivals: counters.validNonemptyArrivals,
            emptyArrivals: counters.emptyArrivals,
            malformedArrivals: counters.malformedArrivals,
            capacityRejectedArrivals: counters.capacityRejectedArrivals,
            ringOverflowCount: counters.ringOverflowCount,
            ringOverflowMetadataDrops: counters.ringOverflowMetadataDrops,
            cursorOverflow: counters.cursorOverflow
        ))
    }

    public func observeSilentNonempty() -> SystemAudioCaptureMonitorAction {
        lock.withLock {
            guard terminalAction == .none else { return terminalAction }
            deadlineArmedValue = false
            permissionValue = .unknown
            return SystemAudioCaptureMonitorAction.permissionUnknown
        }
    }

    public func observeFunctionalNonzeroSignal() -> SystemAudioCaptureMonitorAction {
        lock.withLock {
            guard terminalAction == .none else { return terminalAction }
            deadlineArmedValue = false
            permissionValue = PermissionState.granted
            return SystemAudioCaptureMonitorAction.granted
        }
    }

    public func observePermissionError(_ status: OSStatus) -> SystemAudioCaptureMonitorAction {
        guard status == kAudioDevicePermissionsError else {
            return observeFailure("Core Audio retornou erro \(status) que não é uma negativa de permissão")
        }
        return lock.withLock {
            deadlineArmedValue = false
            permissionValue = .denied
            terminalAction = .denied(Self.permissionDeniedMessage)
            return terminalAction
        }
    }

    public func observePermissionError() -> SystemAudioCaptureMonitorAction {
        lock.withLock {
            deadlineArmedValue = false
            permissionValue = .denied
            terminalAction = .denied(Self.permissionDeniedMessage)
            return terminalAction
        }
    }

    public func observeFailure(_ message: String) -> SystemAudioCaptureMonitorAction {
        lock.withLock {
            guard terminalAction == .none else { return terminalAction }
            deadlineArmedValue = false
            terminalAction = .failed(message)
            return terminalAction
        }
    }

    /// Claims the single automatic recovery budget shared by watchdog and
    /// every HAL route/format/reset event.  The claim is atomic with the
    /// monitor's terminal state so two independent causes cannot both start a
    /// replacement graph.
    public func claimAutomaticRebuild() -> Bool {
        lock.withLock {
            guard terminalAction == .none, !rebuildUsedValue else { return false }
            rebuildUsedValue = true
            return true
        }
    }

    public func deadlineFired(generation firedGeneration: UInt64, nowNanoseconds: UInt64) -> SystemAudioCaptureMonitorAction {
        lock.withLock {
            guard terminalAction == .none,
                  deadlineArmedValue,
                  firedGeneration == deadlineGeneration,
                  nowNanoseconds >= deadlineAt else { return .none }
            deadlineArmedValue = false
            if !rebuildUsedValue {
                rebuildUsedValue = true
                terminalAction = .rebuild(reason: "no-buffer-watchdog")
            } else {
                terminalAction = .ambiguous(Self.ambiguousCaptureMessage)
            }
            return terminalAction
        }
    }

    /// A new graph gets a new generation but the one-rebuild budget belongs to
    /// the user start and is therefore intentionally retained.
    public func beginRebuildGeneration(_ newGeneration: UInt64, counters: SystemAudioCaptureCounterSnapshot, nowNanoseconds: UInt64) -> UInt64 {
        lock.withLock {
            generationValue = newGeneration
            baseline = counters
            deadlineGeneration = newGeneration
            deadlineAt = Self.saturatingDeadline(nowNanoseconds, after: deadlineNanoseconds)
            deadlineArmedValue = true
            terminalAction = .none
            permissionValue = .unknown
            return deadlineAt
        }
    }

    public func resetForNewUserStart(generation: UInt64, counters: SystemAudioCaptureCounterSnapshot, nowNanoseconds: UInt64) -> UInt64 {
        lock.withLock { rebuildUsedValue = false }
        return arm(generation: generation, counters: counters, nowNanoseconds: nowNanoseconds)
    }

    public func isDeadlineCurrent(generation checkedGeneration: UInt64, nowNanoseconds: UInt64) -> Bool {
        lock.withLock { deadlineArmedValue && checkedGeneration == deadlineGeneration && nowNanoseconds >= deadlineAt }
    }

    private static func saturatingDeadline(_ nowNanoseconds: UInt64, after duration: UInt64) -> UInt64 {
        let result = nowNanoseconds.addingReportingOverflow(duration)
        return result.overflow ? UInt64.max : result.partialValue
    }
}
