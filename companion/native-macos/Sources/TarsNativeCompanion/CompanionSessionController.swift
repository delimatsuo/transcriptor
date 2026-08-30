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
    @Published public private(set) var resolvedSystemAudioEngine: ResolvedSystemAudioEngine?
    @Published public private(set) var systemAudioStatus: CaptureSourceStatus = .idle
    @Published public private(set) var systemAudioHealth: SourceHealth = SourceHealth()
    public var systemAudioFramesSent: Int {
        sink?.framesSent(for: .systemAudio) ?? 0
    }

    public typealias TransportFactory = @Sendable (URL, [String]) -> AudioStreamTransport
    public typealias SourceFactory = (CaptureSourceConfiguration, CaptureFrameSink) -> any CaptureSource
    public typealias EngineSourceFactory = (ResolvedSystemAudioEngine, CaptureSourceConfiguration, CaptureFrameSink) -> any CaptureSource

    private let transportFactory: TransportFactory
    private let sourceFactory: SourceFactory
    private let engineSourceFactory: EngineSourceFactory?
    private let isCustomSourceFactory: Bool
    public let systemAudioEnginePreference: SystemAudioEnginePreference
    private let operatingSystemVersion: OperatingSystemVersion
    private var sourceObserverToken: CaptureSourceObserverToken?
    private var sourceObserverIdentity: ObjectIdentifier?
    private var activeCaptureGeneration: UInt64?
    private var launchArgumentError: String?
    private var sessionAttempt: UInt64 = 0
    // A terminal source failure takes ownership of the old source/sink pair
    // until both have been stopped.  Keep the completed task as an ownership
    // marker until the next explicit start/stop consumes it, so a start that
    // is still unwinding cannot stop the same sink a second time.
    private var terminalCleanupTask: Task<Void, Never>?

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
        sourceFactory: SourceFactory? = nil,
        enginePreference: SystemAudioEnginePreference = .automatic,
        operatingSystemVersion: OperatingSystemVersion = ProcessInfo.processInfo.operatingSystemVersion,
        engineSourceFactory: EngineSourceFactory? = nil,
        launchArgumentError: String? = nil
    ) {
        // Any injected source factory also represents the test boundary.  In
        // that mode the controller must not call CoreGraphics/TCC preflight,
        // even when the injected policy resolves to ScreenCaptureKit.
        self.isCustomSourceFactory = sourceFactory != nil || engineSourceFactory != nil
        self.systemAudioEnginePreference = enginePreference
        self.operatingSystemVersion = operatingSystemVersion
        self.engineSourceFactory = engineSourceFactory
        self.launchArgumentError = launchArgumentError
        self.transportFactory = transportFactory ?? { url, protocols in
            URLSessionWebSocketTransport(url: url, protocols: protocols)
        }
        self.sourceFactory = sourceFactory ?? { config, sink in
            ScreenCaptureKitSystemAudioSource(configuration: config, liveCaptureEnabled: true, sink: sink)
        }
    }

    public func start(sessionID: String, streamKey: String, gatewayBase: String) async {
        log("start solicitado — sessão \(sessionID.prefix(8)), gateway \(gatewayBase)")

        if let launchArgumentError {
            state = .error(launchArgumentError)
            return
        }

        let resolvedEngine: ResolvedSystemAudioEngine
        do {
            resolvedEngine = try SystemAudioEngineSelector(
                operatingSystemVersion: operatingSystemVersion
            ).resolve(systemAudioEnginePreference)
            resolvedSystemAudioEngine = resolvedEngine
        } catch {
            state = .error(String(describing: error))
            return
        }

        guard state == .idle || isErrorState(state) else {
            log("start ignorado — estado atual \(state)")
            return
        }

        sessionAttempt &+= 1
        let attempt = sessionAttempt
        state = .connecting
        if let cleanup = terminalCleanupTask {
            await cleanup.value
            guard sessionAttempt == attempt else { return }
            terminalCleanupTask = nil
        }

        if !isCustomSourceFactory && resolvedEngine == .screenCaptureKit {
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
        let sinkIdentity = ObjectIdentifier(newSink)
        self.sink = newSink

        newSink.onStateChange = { [weak self] connected in
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                guard self.sessionAttempt == attempt,
                      self.sink.map({ ObjectIdentifier($0) }) == sinkIdentity else { return }
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

        var sourceIdentityObject: ObjectIdentifier?
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
                deviceIdentity: resolvedEngine == .processTap ? "ProcessTap.SystemAudio" : "ScreenCaptureKit.SystemAudio"
            )
            let newSource: any CaptureSource
            if let engineSourceFactory {
                newSource = engineSourceFactory(resolvedEngine, config, newSink)
            } else if !isCustomSourceFactory && resolvedEngine == .processTap {
                guard #available(macOS 14.2, *) else {
                    throw SystemAudioEngineSelectionError.processTapRequiresMacOS14_2(operatingSystemVersion)
                }
                newSource = ProcessTapSystemAudioSource(configuration: config, sink: newSink)
            } else {
                newSource = sourceFactory(config, newSink)
            }
            self.source = newSource

            let identityObject = ObjectIdentifier(newSource as AnyObject)
            sourceIdentityObject = identityObject
            var installedToken: CaptureSourceObserverToken?
            let token = newSource.installHealthObserver { [weak self] update in
                Task { @MainActor [weak self] in
                    guard let self,
                          self.sessionAttempt == attempt,
                          let installedToken,
                          self.sourceObserverToken == installedToken,
                          self.sourceObserverIdentity == identityObject,
                          let activeGeneration = self.activeCaptureGeneration,
                          update.generation >= activeGeneration else { return }
                    // A source rebuild advances the generation.  Accept that
                    // monotonic transition, while rejecting late callbacks
                    // from an older graph.
                    self.activeCaptureGeneration = update.generation
                    self.systemAudioStatus = update.status
                    if case .ready(let health) = update.status { self.systemAudioHealth = health }
                    if case .running(let health) = update.status { self.systemAudioHealth = health }
                    if case .stopped(let health) = update.status { self.systemAudioHealth = health }
                    if case .failed(let message) = update.status {
                        if message == SystemAudioCaptureMonitor.permissionDeniedMessage {
                            self.systemAudioHealth.permission = .denied
                        }
                        // Terminal source updates are authoritative once the
                        // source/token/generation fence above accepts them.
                        // Preserve the source's exact remediation/diagnostic
                        // copy instead of leaving the UI in Capturando.
                        if self.state != .idle {
                            self.state = .error(message)
                        }
                        self.beginTerminalCleanup()
                    }
                }
            }
            installedToken = token
            self.sourceObserverToken = token
            self.sourceObserverIdentity = identityObject
            self.activeCaptureGeneration = identity.captureGeneration
            self.systemAudioStatus = newSource.status
            if case .ready(let health) = newSource.status { self.systemAudioHealth = health }
            if case .running(let health) = newSource.status { self.systemAudioHealth = health }

            try await newSource.start()
            guard self.sessionAttempt == attempt,
                  self.sourceObserverIdentity == identityObject,
                  self.source != nil,
                  self.terminalCleanupTask == nil,
                  !isErrorState(self.state) else {
                if let cleanup = self.terminalCleanupTask {
                    await cleanup.value
                }
                return
            }
            log("captura de áudio do sistema ativa")
            self.activeSessionID = sessionID
            if self.state == .connecting {
                self.state = .capturing
            }
        } catch {
            if let failure = error as? SystemAudioCaptureFailure, failure == .denied {
                systemAudioHealth.permission = .denied
                systemAudioStatus = .failed(SystemAudioCaptureMonitor.permissionDeniedMessage)
            }
            let cleanup = self.terminalCleanupTask
            let ownsSource = sourceIdentityObject.map { self.sourceObserverIdentity == $0 } ?? false
            let currentSource = ownsSource ? self.source : nil
            let currentToken = ownsSource ? self.sourceObserverToken : nil
            let ownsSink = self.sink.map({ ObjectIdentifier($0) }) == sinkIdentity
            if ownsSource {
                self.sourceObserverToken = nil
                self.sourceObserverIdentity = nil
                self.activeCaptureGeneration = nil
                self.source = nil
                self.activeSessionID = nil
            }
            if ownsSink { self.sink = nil }
            if let cleanup {
                // The terminal-failure owner already removed this observer
                // and owns both stop calls.  Join it instead of duplicating
                // either cleanup edge.
                await cleanup.value
                guard self.sessionAttempt == attempt else { return }
                // The observer already published the exact terminal source
                // message.  Do not replace it with the generic start() error
                // after the source's cleanup owner has completed.
                if isErrorState(self.state) { return }
            } else {
                if let currentSource, let currentToken {
                    currentSource.removeHealthObserver(currentToken)
                }
                if let currentSource { await currentSource.stop() }
                if ownsSink { await newSink.stop() }
            }
            guard self.sessionAttempt == attempt else { return }
            let displayError: String
            if let failure = error as? SystemAudioCaptureFailure, failure == .denied {
                // NSError.localizedDescription is a generic wrapper for this
                // typed Core Audio denial.  Keep the approved remediation
                // copy visible so the operator sees the real permission
                // boundary rather than an opaque "operation failed" string.
                displayError = SystemAudioCaptureMonitor.permissionDeniedMessage
            } else {
                displayError = error.localizedDescription
            }
            log("falha ao iniciar captura: \(displayError)")
            if let failure = error as? SystemAudioCaptureFailure, failure == .denied {
                // Permission is a first-class remediation boundary.  Keep the
                // approved copy byte-for-byte identical to the source health
                // update instead of wrapping it in a generic startup prefix.
                state = .error(SystemAudioCaptureMonitor.permissionDeniedMessage)
            } else {
                state = .error("Falha ao iniciar a captura de áudio do sistema: \(displayError)")
            }
            return
        }
    }

    public func stop() async {
        log("sessão encerrada")
        sessionAttempt &+= 1
        let terminalCleanup = terminalCleanupTask
        let currentSource = self.source
        let currentSink = self.sink
        if let currentSource, let token = self.sourceObserverToken {
            currentSource.removeHealthObserver(token)
        }
        self.sourceObserverToken = nil
        self.sourceObserverIdentity = nil
        self.activeCaptureGeneration = nil
        self.source = nil
        self.sink = nil
        self.activeSessionID = nil
        self.state = .idle
        self.systemAudioStatus = .idle
        self.systemAudioHealth = SourceHealth()

        if let currentSource {
            await currentSource.stop()
        }
        if let currentSink {
            await currentSink.stop()
        }
        if let terminalCleanup {
            await terminalCleanup.value
            terminalCleanupTask = nil
        }
    }

    private func beginTerminalCleanup() {
        guard terminalCleanupTask == nil else { return }
        let oldSource = source
        let oldSink = sink
        let oldObserver = sourceObserverToken
        source = nil
        sink = nil
        sourceObserverToken = nil
        sourceObserverIdentity = nil
        activeCaptureGeneration = nil
        activeSessionID = nil

        let cleanup = Task { @MainActor [oldSource, oldSink, oldObserver] in
            if let oldSource, let oldObserver {
                oldSource.removeHealthObserver(oldObserver)
            }
            if let oldSource {
                await oldSource.stop()
            }
            if let oldSink {
                await oldSink.stop()
            }
        }
        terminalCleanupTask = cleanup
    }

    private func isErrorState(_ state: CompanionState) -> Bool {
        if case .error = state { return true }
        return false
    }
}
