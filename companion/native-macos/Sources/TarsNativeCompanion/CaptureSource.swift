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

public protocol CaptureSource: AnyObject, Sendable {
    var source: AudioSource { get }
    var configuration: CaptureSourceConfiguration { get }
    var status: CaptureSourceStatus { get }
    func start() async throws
    func stop() async
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
