import Foundation
import TarsNativeCompanion

@available(macOS 13.0, *)
final class WebSocketAudioSink: CaptureFrameSink, @unchecked Sendable {
    private let webSocketTask: URLSessionWebSocketTask
    private let sessionID: String
    private let lock = NSLock()

    init(webSocketTask: URLSessionWebSocketTask, sessionID: String) {
        self.webSocketTask = webSocketTask
        self.sessionID = sessionID
    }

    func receive(_ frame: AudioFrame) async throws {
        // Encode frame header + raw PCM payload
        let headerDict: [String: Any] = [
            "session_id": sessionID,
            "source": frame.identity.source.rawValue,
            "sequence": frame.sequence,
            "first_sample": frame.firstSample,
            "captured_at_ms": frame.capturedAtMs,
            "sample_rate": frame.identity.sampleRate,
            "channel_count": frame.identity.channelCount,
            "duration_ms": frame.durationMs
        ]

        guard let headerJson = try? JSONSerialization.data(withJSONObject: headerDict) else { return }
        var headerLength = UInt32(headerJson.count).bigEndian

        var packet = Data()
        withUnsafeBytes(of: &headerLength) { packet.append(contentsOf: $0) }
        packet.append(headerJson)
        packet.append(frame.payload.copyData())

        let message = URLSessionWebSocketTask.Message.data(packet)
        webSocketTask.send(message) { error in
            if let error = error {
                fputs("[tars-companion] WebSocket send error: \(error.localizedDescription)\n", stderr)
            }
        }
    }

    func receiveGap(_ gap: CoverageGap) async throws {
        let gapDict: [String: Any] = [
            "type": "gap",
            "source": gap.identity.source.rawValue,
            "reason": gap.reason.rawValue,
            "first_sample": gap.firstSample ?? 0
        ]
        if let json = try? JSONSerialization.data(withJSONObject: gapDict),
           let str = String(data: json, encoding: .utf8) {
            webSocketTask.send(.string(str)) { _ in }
        }
    }
}

@available(macOS 13.0, *)
struct CompanionApp {
    static func run() async throws {
        let args = CommandLine.arguments
        var sessionID = "default"
        var gatewayBase = "ws://127.0.0.1:8000/api/stream/native"
        var token = ""

        var i = 1
        while i < args.count {
            if args[i] == "--session-id" && i + 1 < args.count {
                sessionID = args[i + 1]
                i += 2
            } else if args[i] == "--gateway" && i + 1 < args.count {
                gatewayBase = args[i + 1]
                i += 2
            } else if args[i] == "--token" && i + 1 < args.count {
                token = args[i + 1]
                i += 2
            } else {
                i += 1
            }
        }

        var urlString = "\(gatewayBase)/\(sessionID)"
        if !token.isEmpty {
            urlString += "?token=\(token)"
        }

        guard let url = URL(string: urlString) else {
            fputs("Invalid gateway URL: \(urlString)\n", stderr)
            exit(1)
        }

        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  T.A.R.S. Native macOS Companion (Wispr Architecture)     ")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("Session ID:   \(sessionID)")
        print("Gateway URL:  \(urlString)")
        print("Capture Mode: Native ScreenCaptureKit (System) + AVAudioEngine (Mic)")
        print("Driver Setup: ZERO virtual devices or MIDI configuration needed")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

        let session = URLSession(configuration: .default)
        let wsTask = session.webSocketTask(with: url)
        wsTask.resume()

        let sink = WebSocketAudioSink(webSocketTask: wsTask, sessionID: sessionID)

        let micIdentity = try SourceIdentity(
            sessionID: sessionID,
            streamID: "mic",
            captureGeneration: 1,
            source: .microphone,
            sampleRate: 16_000,
            channelCount: 1
        )
        let sysIdentity = try SourceIdentity(
            sessionID: sessionID,
            streamID: "system",
            captureGeneration: 1,
            source: .systemAudio,
            sampleRate: 16_000,
            channelCount: 1
        )

        let micConfig = CaptureSourceConfiguration(identity: micIdentity, deviceIdentity: "AVAudioEngine.Microphone")
        let sysConfig = CaptureSourceConfiguration(identity: sysIdentity, deviceIdentity: "ScreenCaptureKit.SystemAudio")

        let micSource = AVAudioEngineMicrophoneSource(configuration: micConfig, liveCaptureEnabled: true, sink: sink)
        let sysSource = ScreenCaptureKitSystemAudioSource(configuration: sysConfig, liveCaptureEnabled: true, sink: sink)

        do {
            try await micSource.start()
            print("✓ Microphone capture active (AVAudioEngine)")
        } catch {
            print("⚠ Microphone capture failed: \(error.localizedDescription)")
        }

        do {
            try await sysSource.start()
            print("✓ System audio capture active (ScreenCaptureKit)")
        } catch {
            print("⚠ System audio capture failed: \(error.localizedDescription)")
        }

        print("\nStreaming dual-channel audio to T.A.R.S. gateway...")
        print("Press Ctrl+C to terminate.\n")

        // Keep running until interrupted
        while true {
            try await Task.sleep(nanoseconds: 1_000_000_000)
        }
    }
}

if #available(macOS 13.0, *) {
    Task {
        do {
            try await CompanionApp.run()
        } catch {
            fputs("Companion error: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }
    RunLoop.main.run()
} else {
    fputs("TarsNativeCompanion requires macOS 13.0 (Ventura) or later.\n", stderr)
    exit(1)
}
