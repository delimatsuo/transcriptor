import CoreGraphics
import Foundation
import TarsNativeCompanion

@available(macOS 13.0, *)
final class WebSocketAudioSink: CaptureFrameSink, @unchecked Sendable {
    private let webSocketTask: URLSessionWebSocketTask
    private let sessionID: String
    private let lock = NSLock()
    private var isClosed = false
    private var frameCounts: [AudioSource: Int] = [:]

    init(webSocketTask: URLSessionWebSocketTask, sessionID: String) {
        self.webSocketTask = webSocketTask
        self.sessionID = sessionID
    }

    private func checkClosed() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return isClosed
    }

    private func markClosed() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if isClosed { return false }
        isClosed = true
        return true
    }

    private func recordFrameSent(for source: AudioSource) {
        lock.lock()
        defer { lock.unlock() }
        frameCounts[source, default: 0] += 1
    }

    /// Thread-safe count of frames handed to this sink for `source`. Backs
    /// the zero-frame advisory, which needs to tell "capture started but no
    /// audio is actually flowing" (TCC granted, nothing playing/muted input)
    /// apart from "capture never started" (already fail-loud elsewhere).
    func framesSent(for source: AudioSource) -> Int {
        lock.lock()
        defer { lock.unlock() }
        return frameCounts[source, default: 0]
    }

    func receive(_ frame: AudioFrame) async throws {
        if checkClosed() { return }
        recordFrameSent(for: frame.identity.source)

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
        webSocketTask.send(message) { [weak self] error in
            if let error = error {
                guard let self = self else { return }
                if self.markClosed() {
                    fputs("[tars-companion] Gateway connection closed: \(error.localizedDescription)\n", stderr)
                }
            }
        }
    }

    func receiveGap(_ gap: CoverageGap) async throws {
        if checkClosed() { return }

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

/// macOS TCC (Ajustes do Sistema → Privacidade e Segurança) denies system
/// audio capture silently unless the running binary has been explicitly
/// granted "Gravação de Tela e Áudio do Sistema". These strings are the
/// user-facing remediation copy for that gate and for a capture that starts
/// but never actually produces frames.
private let preflightDeniedMessage =
    "❌ Permissão ausente. Habilite em: Ajustes do Sistema → Privacidade e Segurança → " +
    "Gravação de Tela e Áudio do Sistema → habilite o seu app de Terminal, e rode novamente."

private let systemAudioStartHint =
    "💡 Dica: Habilite a permissão em 'Ajustes do Sistema → Privacidade e Segurança → " +
    "Gravação de Tela e Áudio do Sistema' para o seu Terminal."

private let zeroFrameAdvisory =
    "⚠ Nenhum frame de áudio do sistema em 15s — verifique se há áudio tocando e se a " +
    "permissão foi concedida ao app de Terminal correto.\n"

private func sourcesLabel(_ sources: CompanionOptions.Sources) -> String {
    switch sources {
    case .systemAudio: return "Áudio do Sistema"
    case .microphone: return "Microfone"
    case .both: return "Áudio do Sistema + Microfone"
    }
}

@available(macOS 13.0, *)
struct CompanionApp {
    static func run() async throws {
        let options: CompanionOptions
        do {
            options = try CompanionOptions.parse(CommandLine.arguments)
        } catch {
            fputs("Invalid arguments: \(error)\n", stderr)
            exit(1)
        }

        let url: URL
        do {
            url = try options.gatewayURL()
        } catch {
            fputs("Invalid gateway URL: \(error)\n", stderr)
            exit(1)
        }

        let wantsMicrophone = options.sources == .microphone || options.sources == .both
        let wantsSystemAudio = options.sources == .systemAudio || options.sources == .both

        // Permission preflight (spec S6): system audio capture fails silently
        // without this entitlement, so check and prompt *before* we spin up a
        // gateway connection and sources, rather than after a confusing start
        // failure.
        if wantsSystemAudio {
            if !CGPreflightScreenCaptureAccess() {
                _ = CGRequestScreenCaptureAccess()
                if !CGPreflightScreenCaptureAccess() {
                    fputs(preflightDeniedMessage + "\n", stderr)
                    exit(2)
                }
            }
        }

        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  T.A.R.S. Native macOS Companion (Wispr Architecture)     ")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("Session ID:   \(options.sessionID)")
        print("Gateway URL:  \(url.absoluteString)")
        print("Fontes:       \(sourcesLabel(options.sources))")
        print("Capture Mode: Native ScreenCaptureKit (System) + AVAudioEngine (Mic)")
        print("Driver Setup: ZERO virtual devices or MIDI configuration needed")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

        let session = URLSession(configuration: .default)
        let wsTask = session.webSocketTask(with: url)
        wsTask.resume()

        let sink = WebSocketAudioSink(webSocketTask: wsTask, sessionID: options.sessionID)

        // Sources are constructed only when selected: system-audio-only runs
        // never touch AVAudioEngine/mic infrastructure at all, matching the
        // new default's intent (no incidental mic permission prompt).
        var activeSources: [any CaptureSource] = []

        if wantsMicrophone {
            let micIdentity = try SourceIdentity(
                sessionID: options.sessionID,
                streamID: "mic",
                captureGeneration: 1,
                source: .microphone,
                sampleRate: 16_000,
                channelCount: 1
            )
            let micConfig = CaptureSourceConfiguration(identity: micIdentity, deviceIdentity: "AVAudioEngine.Microphone")
            let micSource = AVAudioEngineMicrophoneSource(configuration: micConfig, liveCaptureEnabled: true, sink: sink)
            do {
                try await micSource.start()
                print("✓ Microphone capture active (AVAudioEngine)")
                activeSources.append(micSource)
            } catch {
                if options.sources == .microphone {
                    // Microphone is the only requested source — continuing
                    // would mean streaming nothing at all. Fail loud instead
                    // of a silently-dead process (spec: no warn-and-continue
                    // when it results in zero active sources).
                    fputs("❌ Microphone capture failed: \(error.localizedDescription)\n", stderr)
                    exit(3)
                }
                print("⚠ Microphone capture failed: \(error.localizedDescription)")
            }
        }

        if wantsSystemAudio {
            let sysIdentity = try SourceIdentity(
                sessionID: options.sessionID,
                streamID: "system",
                captureGeneration: 1,
                source: .systemAudio,
                sampleRate: 16_000,
                channelCount: 1
            )
            let sysConfig = CaptureSourceConfiguration(identity: sysIdentity, deviceIdentity: "ScreenCaptureKit.SystemAudio")
            let sysSource = ScreenCaptureKitSystemAudioSource(configuration: sysConfig, liveCaptureEnabled: true, sink: sink)
            do {
                try await sysSource.start()
                print("✓ System audio capture active (ScreenCaptureKit)")
                activeSources.append(sysSource)

                // Zero-frame advisory: TCC can grant access yet nothing is
                // actually playing (or routed to the wrong output), which
                // looks identical to a healthy idle session. Advisory only —
                // the hard-fail path above already covers start() throwing.
                Task {
                    try? await Task.sleep(nanoseconds: 15_000_000_000)
                    if sink.framesSent(for: .systemAudio) == 0 {
                        FileHandle.standardError.write(Data(zeroFrameAdvisory.utf8))
                    }
                }
            } catch {
                // System audio is the default/primary source for this task;
                // unlike a mic failure alongside a working system-audio
                // stream, there is no useful degraded mode here.
                fputs("❌ System audio capture failed: \(error.localizedDescription)\n", stderr)
                fputs(systemAudioStartHint + "\n", stderr)
                exit(3)
            }
        }

        print("\nEnviando áudio (\(sourcesLabel(options.sources))) para o gateway T.A.R.S....")
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
