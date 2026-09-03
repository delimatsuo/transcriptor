import CoreGraphics
import Foundation

public enum CompanionState: Equatable, Sendable {
    case idle, connecting, capturing, reconnecting
    case error(String)
}

@MainActor
public final class CompanionSessionController: ObservableObject {
    private static let gatewayConfigurationError =
        "Falha ao iniciar a captura de áudio do sistema: configuração do gateway inválida."
    private static let engineConfigurationError =
        "Falha ao iniciar a captura de áudio do sistema: configuração do mecanismo inválida."
    private static let captureConfigurationError =
        "Falha ao iniciar a captura de áudio do sistema: falha de configuração."
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
    private let harnessMode: Bool
    private var harnessObserver: LiveHarnessEventObserver?
    private var sourceObserverToken: CaptureSourceObserverToken?
    private var sourceObserverIdentity: ObjectIdentifier?
    private var activeCaptureGeneration: UInt64?
    private var activatedHarnessGenerations: Set<UInt64> = []
    private var pendingHarnessHealthEvents: [LiveHarnessEvent] = []
    // Harness event construction receives this key only through the private
    // controller boundary.  It is cleared when the session/control lifetime
    // ends and is never copied into an event or evidence value.
    private var harnessStreamKey: String?
    private var launchNonce = UUID().uuidString
    private var attemptID = UUID().uuidString
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
        launchArgumentError: String? = nil,
        harnessMode: Bool = false,
        harnessObserver: LiveHarnessEventObserver? = nil
    ) {
        // Any injected source factory also represents the test boundary.  In
        // that mode the controller must not call CoreGraphics/TCC preflight,
        // even when the injected policy resolves to ScreenCaptureKit.
        self.isCustomSourceFactory = sourceFactory != nil || engineSourceFactory != nil
        self.systemAudioEnginePreference = enginePreference
        self.operatingSystemVersion = operatingSystemVersion
        self.engineSourceFactory = engineSourceFactory
        self.launchArgumentError = launchArgumentError
        self.harnessMode = harnessMode
        self.harnessObserver = harnessObserver
        self.transportFactory = transportFactory ?? { url, protocols in
            URLSessionWebSocketTransport(url: url, protocols: protocols)
        }
        self.sourceFactory = sourceFactory ?? { config, sink in
            ScreenCaptureKitSystemAudioSource(configuration: config, liveCaptureEnabled: true, sink: sink)
        }
    }

    public func setHarnessObserver(_ observer: LiveHarnessEventObserver?) {
        harnessObserver = observer
    }

    public var isHarnessMode: Bool { harnessMode }

    internal var _testOnlyHasHarnessStreamKey: Bool { harnessStreamKey != nil }

    private func retireHarnessStreamKey(attempt: UInt64, attemptID: String, launchNonce: String) {
        guard self.sessionAttempt == attempt,
              self.attemptID == attemptID,
              self.launchNonce == launchNonce else { return }
        self.harnessStreamKey = nil
    }

    // Explicitly test-only identity perturbation seam.  It is internal (and
    // therefore unavailable to clients of the package) so offline tests can
    // force the exact callback race in which numeric attempt/source/token/
    // generation fields still match while the captured UUID or nonce drifts.
    internal func _testOnlyMutateCurrentAttemptIdentity(attemptID: String? = nil, launchNonce: String? = nil) {
        if let attemptID { self.attemptID = attemptID }
        if let launchNonce { self.launchNonce = launchNonce }
    }

    public func start(
        sessionID: String,
        streamKey: String,
        gatewayBase: String,
        launchNonce: String? = nil
    ) async {
        // The coordinator grants a start owner only after the authenticated
        // control waiter is ready.  Cancellation is advisory in Swift, so a
        // task canceled before it reaches this actor must not manufacture a
        // fresh attempt or revive a retired session.
        guard !Task.isCancelled else { return }
        let validatedGatewayBase: String
        do {
            if harnessMode {
                validatedGatewayBase = try LiveHarnessGatewayBase.validateForSession(
                    gatewayBase,
                    streamKey: streamKey
                )
            } else {
                // Deep-link/normal sessions retain the historical URL and
                // NativeStreamHandshake contract.  The strict Task11 gateway
                // and 43-byte credential fence is harness-only.
                validatedGatewayBase = gatewayBase
            }
        } catch {
            log("start rejeitado — configuração de gateway inválida")
            state = .error(Self.gatewayConfigurationError)
            return
        }
        // The session identifier is the only caller-controlled path segment
        // appended after the validated base.  Keep the credential out of
        // that segment as well, including percent-encoded URL spellings.
        if harnessMode {
            guard !sessionID.isEmpty,
                  !sessionID.contains("%"),
                  !sessionID.contains(streamKey) else {
                log("start rejeitado — identificador de sessão inválido")
                state = .error(Self.gatewayConfigurationError)
                return
            }
        }
        log("start solicitado")

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
            state = .error(Self.engineConfigurationError)
            return
        }

        guard state == .idle || isErrorState(state) else {
            log("start ignorado — estado atual \(state)")
            return
        }

        guard !Task.isCancelled else { return }
        harnessStreamKey = harnessMode ? streamKey : nil
        sessionAttempt &+= 1
        let attempt = sessionAttempt
        let attemptUUID = UUID().uuidString
        let capturedLaunchNonce = launchNonce ?? UUID().uuidString
        attemptID = attemptUUID
        self.launchNonce = capturedLaunchNonce
        activatedHarnessGenerations.removeAll(keepingCapacity: true)
        pendingHarnessHealthEvents.removeAll(keepingCapacity: true)
        state = .connecting
        if let cleanup = terminalCleanupTask {
            await cleanup.value
            guard sessionAttempt == attempt,
                  self.attemptID == attemptUUID,
                  self.launchNonce == capturedLaunchNonce else {
                retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
                return
            }
            terminalCleanupTask = nil
        }

        guard !Task.isCancelled,
              sessionAttempt == attempt,
              self.attemptID == attemptUUID,
              self.launchNonce == capturedLaunchNonce else {
            let ownsAttempt = sessionAttempt == attempt
                && self.attemptID == attemptUUID
                && self.launchNonce == capturedLaunchNonce
            if ownsAttempt {
                if state == .connecting || state == .capturing {
                    state = .idle
                }
                systemAudioStatus = .idle
                systemAudioHealth = SourceHealth()
            }
            retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
            return
        }

        if !isCustomSourceFactory && resolvedEngine == .screenCaptureKit {
            if !CGPreflightScreenCaptureAccess() {
                _ = CGRequestScreenCaptureAccess()
                if !CGPreflightScreenCaptureAccess() {
                    log("preflight de permissão falhou — CGPreflightScreenCaptureAccess=false")
                    state = .error("Permissão ausente. Habilite em Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema para o TarsCompanion, e tente novamente.")
                    retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
                    return
                }
            }
            log("permissão de captura concedida")
        }

        let url: URL
        let protocols: [String]
        do {
            let sessionURL = validatedGatewayBase.hasSuffix("/")
                ? "\(validatedGatewayBase)\(sessionID)"
                : "\(validatedGatewayBase)/\(sessionID)"
            guard let parsedURL = URL(string: sessionURL) else {
                throw CompanionError.invalid("invalid gateway URL")
            }
            url = parsedURL
            protocols = try NativeStreamHandshake.protocols(streamKey: streamKey)
        } catch {
            log("falha ao configurar gateway — configuração rejeitada")
            state = .error(Self.gatewayConfigurationError)
            retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
            return
        }

        let tf = self.transportFactory
        let newSink = ReconnectingAudioSink(
            sessionID: sessionID,
            transportFactory: { tf(url, protocols) },
            intendedSources: [.systemAudio]
        )
        let sinkIdentity = ObjectIdentifier(newSink)
        guard !Task.isCancelled,
              sessionAttempt == attempt,
              self.attemptID == attemptUUID,
              self.launchNonce == capturedLaunchNonce else {
            retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
            return
        }
        self.sink = newSink

        newSink.onStateChange = { [weak self] connected in
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                guard self.sessionAttempt == attempt,
                      self.attemptID == attemptUUID,
                      self.launchNonce == capturedLaunchNonce,
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
            // The selector is not an attestation.  Every system-audio source
            // must report its own concrete engine before either side effect
            // (sink connect or capture start) is allowed.  A nil identity is
            // terminal: synthesizing it from the selector would falsely turn
            // a hostile/unfinished source into Process Tap evidence.
            guard newSource.source == .systemAudio, let actualEngine = newSource.engineIdentity else {
                throw CompanionError.invalid("capture engine identity missing")
            }
            if actualEngine != resolvedEngine {
                throw CompanionError.invalid(
                    "capture engine mismatch: requested \(resolvedEngine.rawValue), actual \(actualEngine.rawValue)"
                )
            }

            // Retain the source only after its concrete identity has been
            // attested.  An identity-missing or mismatched factory result
            // must die with the local start attempt rather than remaining in
            // the controller through the catch/cleanup path.
            guard !Task.isCancelled,
                  self.sessionAttempt == attempt,
                  self.attemptID == attemptUUID,
                  self.launchNonce == capturedLaunchNonce else {
                retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
                return
            }
            self.source = newSource
            let identityObject = ObjectIdentifier(newSource as AnyObject)
            sourceIdentityObject = identityObject
            guard !Task.isCancelled,
                  self.sessionAttempt == attempt,
                  self.attemptID == attemptUUID,
                  self.launchNonce == capturedLaunchNonce else {
                retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
                return
            }
            var installedToken: CaptureSourceObserverToken?
            let token = newSource.installHealthObserver { [weak self] update in
                Task { @MainActor [weak self] in
                    guard let self,
                          self.sessionAttempt == attempt,
                          self.attemptID == attemptUUID,
                          self.launchNonce == capturedLaunchNonce,
                          let installedToken,
                          self.sourceObserverToken == installedToken,
                          self.sourceObserverIdentity == identityObject,
                          let activeGeneration = self.activeCaptureGeneration,
                          update.generation >= activeGeneration else { return }
                    // A source rebuild advances the generation.  Accept that
                    // monotonic transition, while rejecting late callbacks
                    // from an older graph.
                    let terminalFailureMessage: String? = {
                        guard case .failed(let message) = update.status else { return nil }
                        return message
                    }()
                    if update.generation > activeGeneration {
                        self.activeCaptureGeneration = update.generation
                        // A failed event is categorically non-granting and
                        // may be emitted without manufacturing an activation
                        // for a graph that is already terminal.
                        if terminalFailureMessage == nil {
                            self.emitHarnessActivation(
                                attempt: attempt,
                                attemptID: attemptUUID,
                                launchNonce: capturedLaunchNonce,
                                sessionID: sessionID,
                                generation: update.generation,
                                source: newSource,
                                token: installedToken,
                                requestedEngine: resolvedEngine,
                                actualEngine: actualEngine
                            )
                        }
                    }
                    self.systemAudioStatus = update.status
                    if case .ready(let health) = update.status { self.systemAudioHealth = health }
                    if case .running(let health) = update.status { self.systemAudioHealth = health }
                    if case .stopped(let health) = update.status { self.systemAudioHealth = health }
                    if let terminalFailureMessage {
                        // The source callback has no error-typed payload, so
                        // the approved denial is the exact monitor remediation
                        // copy.  Every other terminal message stays unknown.
                        let failurePermission: PermissionState =
                            terminalFailureMessage == SystemAudioCaptureMonitor.permissionDeniedMessage
                                ? .denied
                                : .unknown
                        self.systemAudioHealth.permission = failurePermission
                        // Emit while source/token/generation still satisfy the
                        // admission fence; cleanup below destructively retires
                        // that fence after the event has been accepted.
                        self.emitHarnessHealth(
                            attempt: attempt,
                            attemptID: attemptUUID,
                            launchNonce: capturedLaunchNonce,
                            sessionID: sessionID,
                            generation: update.generation,
                            source: newSource,
                            token: installedToken,
                            requestedEngine: resolvedEngine,
                            actualEngine: actualEngine,
                            status: .failed(""),
                            failurePermission: failurePermission,
                            failureCode: failurePermission == .denied ? .permissionDenied : .captureFailed,
                            allowBeforeActivation: true
                        )
                        // Terminal source updates are authoritative once the
                        // source/token/generation fence above accepts them.
                        // Preserve the source's exact remediation/diagnostic
                        // copy instead of leaving the UI in Capturando.
                        if self.state != .idle {
                            self.state = .error(terminalFailureMessage)
                        }
                        self.beginTerminalCleanup()
                        self.retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
                        return
                    }
                    self.emitHarnessHealth(
                        attempt: attempt,
                        attemptID: attemptUUID,
                        launchNonce: capturedLaunchNonce,
                        sessionID: sessionID,
                        generation: update.generation,
                        source: newSource,
                        token: installedToken,
                        requestedEngine: resolvedEngine,
                        actualEngine: actualEngine,
                        status: update.status
                    )
                }
            }
            installedToken = token
            self.sourceObserverToken = token
            self.sourceObserverIdentity = identityObject
            self.activeCaptureGeneration = identity.captureGeneration
            self.systemAudioStatus = newSource.status
            if case .ready(let health) = newSource.status { self.systemAudioHealth = health }
            if case .running(let health) = newSource.status { self.systemAudioHealth = health }

            // Start the sink only after the concrete source identity check.
            guard !Task.isCancelled,
                  self.sessionAttempt == attempt,
                  self.attemptID == attemptUUID,
                  self.launchNonce == capturedLaunchNonce else {
                retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
                return
            }
            newSink.start()
            log("sink iniciado — conectando a \(url.absoluteString)")
            guard !Task.isCancelled,
                  self.sessionAttempt == attempt,
                  self.attemptID == attemptUUID,
                  self.launchNonce == capturedLaunchNonce else {
                await newSink.stop()
                retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
                return
            }
            try await newSource.start()
            guard !Task.isCancelled else {
                if let cleanup = self.terminalCleanupTask {
                    await cleanup.value
                    retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
                    return
                }
                let ownsAttempt = self.sessionAttempt == attempt
                    && self.attemptID == attemptUUID
                    && self.launchNonce == capturedLaunchNonce
                let ownsSource = ownsAttempt
                    && self.sourceObserverIdentity == identityObject
                    && self.source.map({ ObjectIdentifier($0 as AnyObject) }) == identityObject
                let ownsSink = ownsAttempt
                    && self.sink.map({ ObjectIdentifier($0) }) == sinkIdentity
                if ownsSource {
                    newSource.removeHealthObserver(token)
                    self.sourceObserverToken = nil
                    self.sourceObserverIdentity = nil
                    self.activeCaptureGeneration = nil
                    self.source = nil
                    self.activeSessionID = nil
                }
                if ownsSink {
                    self.sink = nil
                }
                if ownsAttempt {
                    if self.state == .connecting || self.state == .capturing {
                        self.state = .idle
                    }
                    self.systemAudioStatus = .idle
                    self.systemAudioHealth = SourceHealth()
                    retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
                }
                if ownsSource {
                    await newSource.stop()
                }
                if ownsSink {
                    await newSink.stop()
                }
                return
            }
            guard self.sessionAttempt == attempt,
                  self.attemptID == attemptUUID,
                  self.launchNonce == capturedLaunchNonce,
                  self.sourceObserverIdentity == identityObject,
                  self.source != nil,
                  self.terminalCleanupTask == nil,
                  !isErrorState(self.state) else {
                if let cleanup = self.terminalCleanupTask {
                    await cleanup.value
                }
                retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
                return
            }
            // A replacement callback may have advanced the active generation
            // while start() was suspended.  Never publish stale generation-1
            // evidence after a newer activation has been accepted.
            if self.activeCaptureGeneration == identity.captureGeneration {
                self.emitHarnessActivation(
                    attempt: attempt,
                    attemptID: attemptUUID,
                    launchNonce: capturedLaunchNonce,
                    sessionID: sessionID,
                    generation: identity.captureGeneration,
                    source: newSource,
                    token: token,
                    requestedEngine: resolvedEngine,
                    actualEngine: actualEngine
                )
            }
            log("captura de áudio do sistema ativa")
            self.activeSessionID = sessionID
            if self.state == .connecting {
                self.state = .capturing
            }
        } catch {
            let captureFailure = error as? SystemAudioCaptureFailure
            let isPermissionDenied = captureFailure == .denied
            let displayError: String = {
                if isPermissionDenied {
                    // NSError.localizedDescription is a generic wrapper for
                    // this typed Core Audio denial.  Keep the approved
                    // remediation copy visible at every boundary.
                    return SystemAudioCaptureMonitor.permissionDeniedMessage
                }
                if let captureFailure { return captureFailure.description }
                // Preserve typed mismatch/ordering diagnostics.  The default
                // NSError bridge for an enum otherwise collapses them to a
                // generic "operation failed" string.
                return (error as? CompanionError)?.description ?? Self.captureConfigurationError
            }()
            let cleanup = self.terminalCleanupTask
            let ownsSource = sourceIdentityObject.map { self.sourceObserverIdentity == $0 } ?? false
            let currentSource = ownsSource ? self.source : nil
            let currentToken = ownsSource ? self.sourceObserverToken : nil
            let ownsSink = self.sink.map({ ObjectIdentifier($0) }) == sinkIdentity
            if captureFailure != nil, let currentSource, let currentToken {
                let failurePermission: PermissionState = isPermissionDenied ? .denied : .unknown
                self.systemAudioStatus = .failed(displayError)
                self.systemAudioHealth.permission = failurePermission
                // Startup denial has no accepted activation yet.  Publish the
                // fenced terminal observation directly before cleanup, since
                // it can never grant functional capture.
                self.emitHarnessHealth(
                    attempt: attempt,
                    attemptID: attemptUUID,
                    launchNonce: capturedLaunchNonce,
                    sessionID: sessionID,
                    generation: currentSource.configuration.identity.captureGeneration,
                    source: currentSource,
                    token: currentToken,
                    requestedEngine: resolvedEngine,
                    actualEngine: currentSource.engineIdentity ?? resolvedEngine,
                    status: .failed(""),
                    failurePermission: failurePermission,
                    failureCode: isPermissionDenied ? .permissionDenied : .captureFailed,
                    allowBeforeActivation: true
                )
            }
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
                guard self.sessionAttempt == attempt,
                      self.attemptID == attemptUUID,
                      self.launchNonce == capturedLaunchNonce else {
                    retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
                    return
                }
                // The observer already published the exact terminal source
                // message.  Do not replace it with the generic start() error
                // after the source's cleanup owner has completed.
                if isErrorState(self.state) {
                    retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
                    return
                }
            } else {
                if let currentSource, let currentToken {
                    currentSource.removeHealthObserver(currentToken)
                }
                if let currentSource { await currentSource.stop() }
                if ownsSink { await newSink.stop() }
            }
            guard self.sessionAttempt == attempt,
                  self.attemptID == attemptUUID,
                  self.launchNonce == capturedLaunchNonce else {
                retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
                return
            }
            if Task.isCancelled {
                if self.state == .connecting || self.state == .capturing {
                    self.state = .idle
                }
                self.systemAudioStatus = .idle
                self.systemAudioHealth = SourceHealth()
                retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
                return
            }
            log("falha ao iniciar captura: \(displayError)")
            if isPermissionDenied {
                // Permission is a first-class remediation boundary.  Keep the
                // approved copy byte-for-byte identical to the source health
                // update instead of wrapping it in a generic startup prefix.
                state = .error(SystemAudioCaptureMonitor.permissionDeniedMessage)
            } else {
                state = .error("Falha ao iniciar a captura de áudio do sistema: \(displayError)")
            }
            retireHarnessStreamKey(attempt: attempt, attemptID: attemptUUID, launchNonce: capturedLaunchNonce)
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
        self.harnessStreamKey = nil
        activatedHarnessGenerations.removeAll(keepingCapacity: false)
        pendingHarnessHealthEvents.removeAll(keepingCapacity: false)
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

    private func emitHarnessActivation(
        attempt: UInt64,
        attemptID: String,
        launchNonce: String,
        sessionID: String,
        generation: UInt64,
        source: any CaptureSource,
        token: CaptureSourceObserverToken?,
        requestedEngine: ResolvedSystemAudioEngine,
        actualEngine: ResolvedSystemAudioEngine
    ) {
        guard harnessMode, let token, let streamKey = harnessStreamKey,
              self.sessionAttempt == attempt,
              self.attemptID == attemptID,
              self.launchNonce == launchNonce,
              self.sourceObserverToken == token,
              self.source.map({ ObjectIdentifier($0 as AnyObject) }) == ObjectIdentifier(source as AnyObject),
              !activatedHarnessGenerations.contains(generation) else { return }
        // The callback path advances activeCaptureGeneration immediately
        // before calling this helper; the initial-start path has its own
        // equality fence after the awaited source.start().  Keeping those
        // caller-owned fences explicit makes the suspended-start ordering
        // test mutation-effective instead of hiding a stale call behind a
        // redundant helper check.
        activatedHarnessGenerations.insert(generation)
        guard let event = try? LiveHarnessEvent(
                kind: .activation,
                attemptID: attemptID,
                launchNonce: launchNonce,
                sessionID: sessionID,
                generation: generation,
                requestedEngine: requestedEngine,
                resolvedEngine: requestedEngine,
                actualEngine: actualEngine,
                sourceObjectID: String(describing: ObjectIdentifier(source as AnyObject)),
                observerTokenID: token.identifier,
                activeStreamKey: streamKey
        ) else { return }
        harnessObserver?(event)
        let pending = pendingHarnessHealthEvents.filter { $0.generation == generation }
        pendingHarnessHealthEvents.removeAll { $0.generation == generation }
        pending.forEach { harnessObserver?($0) }
    }

    private func emitHarnessHealth(
        attempt: UInt64,
        attemptID: String,
        launchNonce: String,
        sessionID: String,
        generation: UInt64,
        source: any CaptureSource,
        token: CaptureSourceObserverToken?,
        requestedEngine: ResolvedSystemAudioEngine,
        actualEngine: ResolvedSystemAudioEngine,
        status: CaptureSourceStatus,
        failurePermission: PermissionState? = nil,
        failureCode: LiveHarnessFailureCode? = nil,
        allowBeforeActivation: Bool = false
    ) {
        guard harnessMode, let token, let streamKey = harnessStreamKey,
              self.sessionAttempt == attempt,
              self.attemptID == attemptID,
              self.launchNonce == launchNonce,
              self.activeCaptureGeneration == generation,
              self.sourceObserverToken == token,
              self.source.map({ ObjectIdentifier($0 as AnyObject) }) == ObjectIdentifier(source as AnyObject) else { return }
        // Local source failures can contain arbitrary remediation/error text.
        // Normalize them to the closed wire vocabulary before constructing the
        // event; no local diagnostic is retained in the event object.
        let wireStatus: CaptureSourceStatus
        let wireFailureCode: LiveHarnessFailureCode?
        if case .failed = status {
            let permission = failurePermission ?? .unknown
            wireStatus = .failed("")
            wireFailureCode = failureCode
                ?? (permission == .denied ? .permissionDenied : .captureFailed)
        } else {
            wireStatus = status
            wireFailureCode = nil
        }
        guard let event = try? LiveHarnessEvent(
                kind: .health,
                attemptID: attemptID,
                launchNonce: launchNonce,
                sessionID: sessionID,
                generation: generation,
                requestedEngine: requestedEngine,
                resolvedEngine: requestedEngine,
                actualEngine: actualEngine,
                sourceObjectID: String(describing: ObjectIdentifier(source as AnyObject)),
                observerTokenID: token.identifier,
                status: wireStatus,
                failedPermission: failurePermission,
                failureCode: wireFailureCode,
                activeStreamKey: streamKey
        ) else { return }
        guard allowBeforeActivation || activatedHarnessGenerations.contains(generation) else {
            pendingHarnessHealthEvents.append(event)
            return
        }
        harnessObserver?(event)
    }
}
