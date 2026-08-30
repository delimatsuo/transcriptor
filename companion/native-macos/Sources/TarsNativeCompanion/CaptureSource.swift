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
}

public typealias CaptureSourceHealthObserver = @Sendable (CaptureSourceHealthUpdate) -> Void

public protocol CaptureSource: AnyObject, Sendable {
    var source: AudioSource { get }
    var configuration: CaptureSourceConfiguration { get }
    var status: CaptureSourceStatus { get }
    func start() async throws
    func stop() async
    func installHealthObserver(_ observer: @escaping CaptureSourceHealthObserver) -> CaptureSourceObserverToken
    func removeHealthObserver(_ token: CaptureSourceObserverToken)
}

public extension CaptureSource {
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
