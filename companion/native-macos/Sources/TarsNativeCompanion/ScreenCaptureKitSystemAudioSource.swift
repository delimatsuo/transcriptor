import Foundation
import ScreenCaptureKit

@available(macOS 13.0, *)
public final class ScreenCaptureKitSystemAudioSource: CaptureSource, @unchecked Sendable {
    public let source: AudioSource = .systemAudio
    public let configuration: CaptureSourceConfiguration
    public private(set) var status: CaptureSourceStatus = .idle

    private let liveCaptureEnabled: Bool
    private var stream: SCStream?

    public init(configuration: CaptureSourceConfiguration, liveCaptureEnabled: Bool = false) {
        self.configuration = configuration
        self.liveCaptureEnabled = liveCaptureEnabled
    }

    public func makeAudioOnlyConfiguration() -> SCStreamConfiguration {
        let streamConfiguration = SCStreamConfiguration()
        streamConfiguration.capturesAudio = true
        streamConfiguration.width = 1
        streamConfiguration.height = 1
        return streamConfiguration
    }

    public func start() async throws {
        guard liveCaptureEnabled else {
            status = .failed(CompanionError.nativeCaptureRequiresSeparateAuthorization.description)
            throw CompanionError.nativeCaptureRequiresSeparateAuthorization
        }
        guard configuration.identity.source == .systemAudio else {
            status = .failed("system-audio identity is invalid")
            throw CompanionError.invalid("system-audio identity is invalid")
        }
        // A later separately authorized fixture may construct SCStream and attach audio output.
        // This source-only build never requests permission or starts a stream.
        stream = nil
        status = .failed(CompanionError.nativeCaptureRequiresSeparateAuthorization.description)
        throw CompanionError.nativeCaptureRequiresSeparateAuthorization
    }

    public func stop() async {
        stream = nil
        status = .stopped(SourceHealth(permission: .unknown, route: .unknown))
    }
}
