import CoreGraphics
import Foundation

public enum CompanionState: Equatable, Sendable {
    case idle, connecting, capturing, reconnecting
    case error(String)
}

@MainActor
public final class CompanionSessionController: ObservableObject {
    @Published public private(set) var state: CompanionState = .idle
    @Published public private(set) var activeSessionID: String? = nil
    public var systemAudioFramesSent: Int {
        sink?.framesSent(for: .systemAudio) ?? 0
    }

    public typealias TransportFactory = @Sendable (URL, [String]) -> AudioStreamTransport
    public typealias SourceFactory = (CaptureSourceConfiguration, CaptureFrameSink) -> any CaptureSource

    private let transportFactory: TransportFactory
    private let sourceFactory: SourceFactory
    private let isCustomSourceFactory: Bool

    // CRITICAL (documented production bug class in this repo): the controller MUST retain the source and sink as stored properties for the whole session. Never leave them as locals — Swift ARC may release a local after its last use and silently tear down the capture stream.
    private var source: (any CaptureSource)?
    private var sink: ReconnectingAudioSink?

    private func log(_ message: String) {
        NSLog("TarsCompanion: %@", message)
    }

    /// Defaults: URLSessionWebSocketTransport + ScreenCaptureKitSystemAudioSource.
    /// Both injectable so unit tests never touch real sockets or ScreenCaptureKit.
    public init(
        transportFactory: TransportFactory? = nil,
        sourceFactory: SourceFactory? = nil
    ) {
        self.isCustomSourceFactory = sourceFactory != nil
        self.transportFactory = transportFactory ?? { url, protocols in
            URLSessionWebSocketTransport(url: url, protocols: protocols)
        }
        self.sourceFactory = sourceFactory ?? { config, sink in
            ScreenCaptureKitSystemAudioSource(configuration: config, liveCaptureEnabled: true, sink: sink)
        }
    }

    public func start(sessionID: String, streamKey: String, gatewayBase: String) async {
        log("start solicitado — sessão \(sessionID.prefix(8)), gateway \(gatewayBase)")

        guard state == .idle || isErrorState(state) else {
            log("start ignorado — estado atual \(state)")
            return
        }

        state = .connecting

        if !isCustomSourceFactory {
            if !CGPreflightScreenCaptureAccess() {
                _ = CGRequestScreenCaptureAccess()
                if !CGPreflightScreenCaptureAccess() {
                    log("preflight de permissão falhou — CGPreflightScreenCaptureAccess=false")
                    state = .error("Permissão ausente. Habilite em Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema para o TarsCompanion, e tente novamente.")
                    return
                }
            }
            log("permissão de captura concedida")
        }

        let url: URL
        let protocols: [String]
        do {
            let base = "\(gatewayBase)/\(sessionID)"
            guard let parsedURL = URL(string: base) else {
                throw CompanionError.invalid("invalid gateway URL: \(base)")
            }
            url = parsedURL
            protocols = try NativeStreamHandshake.protocols(streamKey: streamKey)
        } catch {
            log("falha ao configurar gateway: \(error.localizedDescription)")
            state = .error("Falha ao iniciar a captura de áudio do sistema: \(error.localizedDescription)")
            return
        }

        let tf = self.transportFactory
        let newSink = ReconnectingAudioSink(
            sessionID: sessionID,
            transportFactory: { tf(url, protocols) },
            intendedSources: [.systemAudio]
        )
        self.sink = newSink

        newSink.onStateChange = { [weak self] connected in
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                self.log("conexão: \(connected ? "estabelecida" : "perdida")")
                guard self.state == .capturing || self.state == .reconnecting || self.state == .connecting else {
                    return
                }
                if connected {
                    self.state = .capturing
                } else {
                    self.state = .reconnecting
                }
            }
        }
        newSink.start()
        log("sink iniciado — conectando a \(url.absoluteString)")

        do {
            let identity = try SourceIdentity(
                sessionID: sessionID,
                streamID: "system",
                captureGeneration: 1,
                source: .systemAudio,
                sampleRate: 16_000,
                channelCount: 1
            )
            let config = CaptureSourceConfiguration(
                identity: identity,
                deviceIdentity: "ScreenCaptureKit.SystemAudio"
            )
            let newSource = sourceFactory(config, newSink)
            self.source = newSource

            try await newSource.start()
            log("captura de áudio do sistema ativa")
            self.activeSessionID = sessionID
            if self.state == .connecting {
                self.state = .capturing
            }
        } catch {
            await newSink.stop()
            self.sink = nil
            self.source = nil
            self.activeSessionID = nil
            log("falha ao iniciar captura: \(error.localizedDescription)")
            state = .error("Falha ao iniciar a captura de áudio do sistema: \(error.localizedDescription)")
            return
        }
    }

    public func stop() async {
        log("sessão encerrada")
        let currentSource = self.source
        let currentSink = self.sink
        self.source = nil
        self.sink = nil
        self.activeSessionID = nil
        self.state = .idle

        if let currentSource {
            await currentSource.stop()
        }
        if let currentSink {
            await currentSink.stop()
        }
    }

    private func isErrorState(_ state: CompanionState) -> Bool {
        if case .error = state { return true }
        return false
    }
}
