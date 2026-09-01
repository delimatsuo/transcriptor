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

    public typealias TransportFactory = @Sendable (URL) -> AudioStreamTransport
    public typealias SourceFactory = (CaptureSourceConfiguration, CaptureFrameSink) -> any CaptureSource

    private let transportFactory: TransportFactory
    private let sourceFactory: SourceFactory
    private let isCustomSourceFactory: Bool

    // CRITICAL (documented production bug class in this repo): the controller MUST retain the source and sink as stored properties for the whole session. Never leave them as locals — Swift ARC may release a local after its last use and silently tear down the capture stream.
    private var source: (any CaptureSource)?
    private var sink: ReconnectingAudioSink?

    private var currentGeneration: UInt64 = 0
    private var startupTask: Task<Void, Never>?

    /// Defaults: URLSessionWebSocketTransport + ScreenCaptureKitSystemAudioSource.
    /// Both injectable so unit tests never touch real sockets or ScreenCaptureKit.
    public init(
        transportFactory: TransportFactory? = nil,
        sourceFactory: SourceFactory? = nil
    ) {
        self.isCustomSourceFactory = sourceFactory != nil
        self.transportFactory = transportFactory ?? { url in
            URLSessionWebSocketTransport(url: url)
        }
        self.sourceFactory = sourceFactory ?? { config, sink in
            ScreenCaptureKitSystemAudioSource(configuration: config, liveCaptureEnabled: true, sink: sink)
        }
    }

    public func start(sessionID: String, streamKey: String, gatewayBase: String) async {
        guard state == .idle || isErrorState(state) else {
            return
        }

        currentGeneration &+= 1
        let generation = currentGeneration

        state = .connecting

        let startup = Task { @MainActor [weak self] in
            guard let self = self else { return }
            await self.performStart(
                sessionID: sessionID,
                streamKey: streamKey,
                gatewayBase: gatewayBase,
                generation: generation
            )
        }
        self.startupTask = startup
        await startup.value
        if self.currentGeneration == generation && self.startupTask == startup {
            self.startupTask = nil
        }
    }

    private func performStart(
        sessionID: String,
        streamKey: String,
        gatewayBase: String,
        generation: UInt64
    ) async {
        if !isCustomSourceFactory {
            if !CGPreflightScreenCaptureAccess() {
                _ = CGRequestScreenCaptureAccess()
                if !CGPreflightScreenCaptureAccess() {
                    guard self.currentGeneration == generation else { return }
                    self.state = .error("Permissão ausente. Habilite em Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema para o TarsCompanion, e tente novamente.")
                    return
                }
            }
        }

        guard self.currentGeneration == generation else { return }

        let url: URL
        do {
            url = try makeGatewayURL(gatewayBase: gatewayBase, sessionID: sessionID, streamKey: streamKey)
        } catch {
            guard self.currentGeneration == generation else { return }
            self.state = .error("Falha ao iniciar a captura de áudio do sistema: \(error.localizedDescription)")
            return
        }

        guard self.currentGeneration == generation else { return }

        let tf = self.transportFactory
        let newSink = ReconnectingAudioSink(
            sessionID: sessionID,
            transportFactory: { tf(url) }
        )
        self.sink = newSink

        newSink.onStateChange = { [weak self] connected in
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                guard self.currentGeneration == generation else { return }
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

        var attemptedSource: (any CaptureSource)?
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

            guard self.currentGeneration == generation else {
                await newSink.stop()
                return
            }

            let newSource = sourceFactory(config, newSink)
            attemptedSource = newSource
            self.source = newSource

            try await newSource.start()

            guard self.currentGeneration == generation else {
                await newSource.stop()
                await newSink.stop()
                return
            }

            self.activeSessionID = sessionID
            if self.state == .connecting {
                self.state = .capturing
            }
        } catch {
            await newSink.stop()
            if let attemptedSource {
                await attemptedSource.stop()
            }
            guard self.currentGeneration == generation else {
                return
            }
            self.sink = nil
            self.source = nil
            self.activeSessionID = nil
            self.state = .error("Falha ao iniciar a captura de áudio do sistema: \(error.localizedDescription)")
            return
        }
    }

    public func stop() async {
        currentGeneration &+= 1
        let stopGeneration = currentGeneration

        let inFlightStartup = self.startupTask
        inFlightStartup?.cancel()

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

        if let inFlightStartup {
            await inFlightStartup.value

            // Stop the same source again after startup settles so a CaptureSource that
            // ignores cancellation and becomes active late cannot remain active.
            if let currentSource {
                await currentSource.stop()
            }
        }

        guard self.currentGeneration == stopGeneration else {
            return
        }

        self.source = nil
        self.sink = nil
        self.activeSessionID = nil
        self.state = .idle
        self.startupTask = nil
    }

    private func isErrorState(_ state: CompanionState) -> Bool {
        if case .error = state { return true }
        return false
    }

    private func makeGatewayURL(gatewayBase: String, sessionID: String, streamKey: String) throws -> URL {
        let base = "\(gatewayBase)/\(sessionID)"
        guard var components = URLComponents(string: base) else {
            throw CompanionError.invalid("invalid gateway URL: \(base)")
        }
        if streamKey.isEmpty {
            components.percentEncodedQuery = nil
        } else {
            let unreserved = CharacterSet(
                charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
            )
            guard let encodedKey = streamKey.addingPercentEncoding(withAllowedCharacters: unreserved) else {
                throw CompanionError.invalid("unable to percent-encode stream key")
            }
            components.percentEncodedQuery = "stream_key=\(encodedKey)"
        }
        guard let url = components.url else {
            throw CompanionError.invalid("invalid gateway URL: \(base)")
        }
        return url
    }
}
