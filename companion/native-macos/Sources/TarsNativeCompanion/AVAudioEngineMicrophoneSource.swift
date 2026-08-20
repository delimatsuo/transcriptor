import AVFoundation
import CoreAudio
import Foundation

@available(macOS 13.0, *)
public final class AVAudioEngineMicrophoneSource: CaptureSource, @unchecked Sendable {
    public let source: AudioSource = .microphone
    public let configuration: CaptureSourceConfiguration
    public private(set) var status: CaptureSourceStatus = .idle

    private let liveCaptureEnabled: Bool
    private var engine: AVAudioEngine?

    public init(configuration: CaptureSourceConfiguration, liveCaptureEnabled: Bool = false) {
        self.configuration = configuration
        self.liveCaptureEnabled = liveCaptureEnabled
    }

    public func start() async throws {
        // The source-only corridor is fail-closed. A separately authorized fixture may opt in later.
        guard liveCaptureEnabled else {
            status = .failed(CompanionError.nativeCaptureRequiresSeparateAuthorization.description)
            throw CompanionError.nativeCaptureRequiresSeparateAuthorization
        }
        guard configuration.identity.source == .microphone,
              configuration.deviceIdentity != nil,
              configuration.identity.channelCount > 0 else {
            status = .failed("microphone device identity is unavailable")
            throw CompanionError.invalid("microphone device identity is unavailable")
        }
        _ = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyDeviceUID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        // Do not request permission, install a tap, or start an engine in this source-only build.
        engine = nil
        status = .failed(CompanionError.nativeCaptureRequiresSeparateAuthorization.description)
        throw CompanionError.nativeCaptureRequiresSeparateAuthorization
    }

    public func stop() async {
        engine?.stop()
        engine = nil
        status = .stopped(SourceHealth(permission: .unknown, route: .unknown))
    }
}
