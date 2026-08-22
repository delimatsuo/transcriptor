import CoreGraphics
import Foundation
import TarsNativeCompanion

/// One gateway connection, built fresh by `ReconnectingAudioSink` on every
/// (re)connect. `resume()` alone is optimistic — URLSession happily "starts" a
/// WebSocket task against a dead host and only reports the failure on the first
/// read/write — so `connect()` is not considered successful until a ping has
/// made the round trip.
@available(macOS 13.0, *)
final class URLSessionWebSocketTransport: AudioStreamTransport, @unchecked Sendable {
    private let url: URL
    private let session: URLSession
    private let lock = NSLock()
    private var webSocketTask: URLSessionWebSocketTask?

    init(url: URL, session: URLSession) {
        self.url = url
        self.session = session
    }

    func connect() async throws {
        let task = session.webSocketTask(with: url)
        lock.withLock { webSocketTask = task }
        task.resume()
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            task.sendPing { error in
                if let error = error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume()
                }
            }
        }
    }

    func send(_ data: Data) async throws {
        try await send(.data(data))
    }

    func sendText(_ text: String) async throws {
        try await send(.string(text))
    }

    private func send(_ message: URLSessionWebSocketTask.Message) async throws {
        guard let task = lock.withLock({ webSocketTask }) else {
            throw CompanionError.invalid("gateway transport is not connected")
        }
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            task.send(message) { error in
                if let error = error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume()
                }
            }
        }
    }

    func cancel() {
        let task = lock.withLock { () -> URLSessionWebSocketTask? in
            let current = webSocketTask
            webSocketTask = nil
            return current
        }
        task?.cancel(with: .goingAway, reason: nil)
    }
}

/// Prints one line per connection state *change*. The sink already collapses
/// repeats, so a long outage produces a single warning rather than one per
/// failed send.
final class ConnectionAnnouncer: @unchecked Sendable {
    private let lock = NSLock()
    private var connectCount = 0

    func announce(connected: Bool) {
        if connected {
            let isFirst: Bool = lock.withLock {
                connectCount += 1
                return connectCount == 1
            }
            print(isFirst ? "✓ Conectado ao gateway T.A.R.S." : "✓ Reconectado ao gateway T.A.R.S.")
        } else {
            let everConnected: Bool = lock.withLock { connectCount > 0 }
            fputs(everConnected
                ? "⚠ Conexão perdida com o gateway — o áudio fica em buffer e será reenviado ao reconectar.\n"
                : "⚠ Sem conexão com o gateway — tentando novamente...\n", stderr)
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

        // The sink owns connection lifetime from here: it buffers ~30 s of
        // audio across a gateway outage, replays it in capture order, and only
        // reports a gap when the outage actually outlasted the buffer.
        let session = URLSession(configuration: .default)
        let announcer = ConnectionAnnouncer()
        let sink = ReconnectingAudioSink(
            sessionID: options.sessionID,
            transportFactory: { URLSessionWebSocketTransport(url: url, session: session) }
        )
        sink.onStateChange = { connected in announcer.announce(connected: connected) }
        sink.start()

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
                // Gated on a live connection because `framesSent` counts
                // delivered frames: while the gateway is unreachable the
                // connection warnings already explain the silence, and blaming
                // the microphone permission for it would be a lie.
                Task {
                    try? await Task.sleep(nanoseconds: 15_000_000_000)
                    if sink.isConnected, sink.framesSent(for: .systemAudio) == 0 {
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

        // `activeSources` is never read after the appends above, and Swift's ARC
        // is free to release a local at its LAST USE — not at end of scope. In a
        // release build that dropped the only strong reference to the capture
        // sources moments after start(), tearing down the SCStream while this
        // loop slept: capture reported "active" and then not one frame ever
        // reached the sink (silently — no error, no disconnect, just zero system
        // audio forever). Touching the array on every tick keeps every source
        // alive for the whole process lifetime.
        while true {
            try await Task.sleep(nanoseconds: 1_000_000_000)
            withExtendedLifetime(activeSources) {}
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
