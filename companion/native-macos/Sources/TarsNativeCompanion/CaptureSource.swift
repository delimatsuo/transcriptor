import Foundation

public struct CaptureSourceConfiguration: Equatable, Sendable {
    public let identity: SourceIdentity
    public let deviceIdentity: String?

    public init(identity: SourceIdentity, deviceIdentity: String? = nil) {
        self.identity = identity
        self.deviceIdentity = deviceIdentity
    }
}

public enum CaptureSourceStatus: Equatable, Sendable {
    case idle
    case ready(SourceHealth)
    case running(SourceHealth)
    case stopped(SourceHealth)
    case failed(String)
}

public struct CaptureSourceHealthUpdate: Equatable, Sendable {
    public let source: AudioSource
    public let generation: UInt64
    public let status: CaptureSourceStatus

    public init(source: AudioSource, generation: UInt64, status: CaptureSourceStatus) {
        self.source = source
        self.generation = generation
        self.status = status
    }
}

/// Opaque identity for one observer installation.  Tokens are intentionally
/// value types so removal can be made exactly once even when a source is being
/// torn down concurrently with a late health callback.
public struct CaptureSourceObserverToken: Hashable, Sendable {
    fileprivate let rawValue: UUID

    init(rawValue: UUID = UUID()) {
        self.rawValue = rawValue
    }

    public var identifier: String { rawValue.uuidString }
}

public typealias CaptureSourceHealthObserver = @Sendable (CaptureSourceHealthUpdate) -> Void

public protocol CaptureSource: AnyObject, Sendable {
    var source: AudioSource { get }
    /// The concrete capture engine that this object will start.  This is
    /// intentionally independent from the requested selector: a source
    /// implementation must attest its own engine, not repeat configuration
    /// supplied by its caller.
    var engineIdentity: ResolvedSystemAudioEngine? { get }
    var configuration: CaptureSourceConfiguration { get }
    var status: CaptureSourceStatus { get }
    func start() async throws
    func stop() async
    func installHealthObserver(_ observer: @escaping CaptureSourceHealthObserver) -> CaptureSourceObserverToken
    func removeHealthObserver(_ token: CaptureSourceObserverToken)
}

public extension CaptureSource {
    /// Older microphone fixtures remain source-compatible.  System-audio
    /// production sources override this with their concrete engine identity.
    var engineIdentity: ResolvedSystemAudioEngine? { nil }

    /// Descriptive alias used by harness clients; it cannot be set by the
    /// selector and therefore carries the same concrete-source guarantee.
    var captureEngineIdentity: ResolvedSystemAudioEngine? { engineIdentity }

    func installHealthObserver(_ observer: @escaping CaptureSourceHealthObserver) -> CaptureSourceObserverToken {
        // Legacy microphone/SCK test doubles can opt into the health stream
        // without being forced to implement a callback store.  The controller
        // still receives an immediate snapshot before starting the source.
        observer(CaptureSourceHealthUpdate(
            source: source,
            generation: configuration.identity.captureGeneration,
            status: status
        ))
        return CaptureSourceObserverToken()
    }

    func removeHealthObserver(_ token: CaptureSourceObserverToken) {}
}

public protocol CaptureFrameSink: Sendable {
    func receive(_ frame: AudioFrame) async throws
    func receiveGap(_ gap: CoverageGap) async throws
}

public struct GeneratedFrameSink: CaptureFrameSink {
    public private(set) var gaps: [CoverageGap] = []

    public init() {}

    public func receive(_ frame: AudioFrame) async throws {
        // Deliberately do not retain payload bytes outside the custody owner.
        _ = frame
    }

    public func receiveGap(_ gap: CoverageGap) async throws {
        _ = gap
    }
}
