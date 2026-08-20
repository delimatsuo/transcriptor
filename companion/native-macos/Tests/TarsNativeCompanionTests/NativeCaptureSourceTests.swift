import XCTest
@testable import TarsNativeCompanion

final class NativeCaptureSourceTests: XCTestCase {
    func testScreenCaptureKitAudioOnlyConfiguration() throws {
        let identity = try SourceIdentity(sessionID: "sess_1", streamID: "sys_1", captureGeneration: 1, source: .systemAudio, sampleRate: 48_000, channelCount: 2)
        let config = CaptureSourceConfiguration(identity: identity, deviceIdentity: "ScreenCaptureKit.SystemAudio")
        let source = ScreenCaptureKitSystemAudioSource(configuration: config, liveCaptureEnabled: true)

        XCTAssertEqual(source.source, .systemAudio)
        XCTAssertEqual(source.status, .idle)

        let streamConfig = source.makeAudioOnlyConfiguration()
        XCTAssertTrue(streamConfig.capturesAudio)
        XCTAssertTrue(streamConfig.excludesCurrentProcessAudio)
        XCTAssertEqual(streamConfig.sampleRate, 48_000)
        XCTAssertEqual(streamConfig.channelCount, 2)
    }

    func testAVAudioEngineMicrophoneSourceConfiguration() throws {
        let identity = try SourceIdentity(sessionID: "sess_1", streamID: "mic_1", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        let config = CaptureSourceConfiguration(identity: identity, deviceIdentity: "AVAudioEngine.DefaultMic")
        let source = AVAudioEngineMicrophoneSource(configuration: config, liveCaptureEnabled: true)

        XCTAssertEqual(source.source, .microphone)
        XCTAssertEqual(source.status, .idle)
    }

    func testStopOnIdleSourceIsSafe() async throws {
        let identity = try SourceIdentity(sessionID: "sess_1", streamID: "mic_1", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        let config = CaptureSourceConfiguration(identity: identity, deviceIdentity: "AVAudioEngine.DefaultMic")
        let source = AVAudioEngineMicrophoneSource(configuration: config, liveCaptureEnabled: true)

        await source.stop()
        if case .stopped = source.status {
            // expected
        } else {
            XCTFail("expected stopped status")
        }
    }
}
