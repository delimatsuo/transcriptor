import AppKit
import SwiftUI
import TarsNativeCompanion

private final class HarnessEventWriter: @unchecked Sendable {
    private let connection: LiveHarnessControlConnection
    private let queue = DispatchQueue(label: "com.ella.tars.live-harness.events")

    init(connection: LiveHarnessControlConnection) {
        self.connection = connection
    }

    func enqueue(_ event: LiveHarnessEvent) {
        queue.async { [connection] in
            do {
                try connection.send(event: event)
            } catch {
                // A writer failure must not close/reuse the descriptor while
                // the sole control-loss owner is inside recv.  Shutdown wakes
                // that owner; the runtime closes only after it has joined.
                connection.requestShutdown()
            }
        }
    }

    func finish() {
        // A synchronous barrier joins all queued event writes before the
        // connection owner is closed by the lifecycle seam.
        queue.sync {}
    }
}

private final class HarnessEventWriterSlot: @unchecked Sendable {
    private let lock = NSLock()
    private var writer: HarnessEventWriter?

    func install(_ writer: HarnessEventWriter) {
        lock.withLock { self.writer = writer }
    }

    func enqueue(_ event: LiveHarnessEvent) {
        let writer = lock.withLock { self.writer }
        writer?.enqueue(event)
    }

    func finish() {
        let writer = lock.withLock { () -> HarnessEventWriter? in
            let writer = self.writer
            self.writer = nil
            return writer
        }
        writer?.finish()
    }
}

private final class HarnessShutdownRequestSlot: @unchecked Sendable {
    private let lock = NSLock()
    private var request: LiveHarnessShutdownRequest?

    func install(_ request: LiveHarnessShutdownRequest) {
        lock.withLock { self.request = request }
    }

    func take() -> LiveHarnessShutdownRequest? {
        lock.withLock {
            let request = self.request
            self.request = nil
            return request
        }
    }
}

@MainActor
private final class HarnessSessionRuntime: ObservableObject {
    private let controller: CompanionSessionController
    private let client: LiveHarnessControlClient?
    private let harnessMode: Bool
    private let terminateHarnessApplication: () -> Void
    private var task: Task<Void, Never>?
    private var connection: LiveHarnessControlConnection?
    private var controllerStopCompleted = false

    init(
        controller: CompanionSessionController,
        client: LiveHarnessControlClient?,
        harnessMode: Bool,
        terminateHarnessApplication: @escaping () -> Void = { NSApplication.shared.terminate(nil) }
    ) {
        self.controller = controller
        self.client = client
        self.harnessMode = harnessMode
        self.terminateHarnessApplication = terminateHarnessApplication
    }

    private func stopControllerOnce() async {
        guard !controllerStopCompleted else { return }
        controllerStopCompleted = true
        await controller.stop()
    }

    func startIfNeeded() {
        guard task == nil, harnessMode else { return }
        task = Task { [weak self] in
            guard let self else { return }
            await self.run(client: self.client)
        }
    }

    private func run(client: LiveHarnessControlClient?) async {
        // Entry into harness mode owns the full lifecycle, even when parsing
        // produced no client.  The finalizer is the production-testable seam
        // for every open/receive/nonce/coordinator return or throw.
        let eventWriterSlot = HarnessEventWriterSlot()
        let shutdownRequestSlot = HarnessShutdownRequestSlot()
        let lifecycle = LiveHarnessLifecycleFinalizer(
            // The coordinator normally completes this stop before the exact
            // shutdown acknowledgement.  The finalizer still owns every
            // error path, but must not invoke a second controller stop after
            // the ack has crossed the serialized control writer.
            stopController: { await self.stopControllerOnce() },
            clearObserver: { self.controller.setHarnessObserver(nil) },
            closeAndJoinConnection: {
                eventWriterSlot.finish()
                self.connection?.close()
                self.connection = nil
            },
            terminateApplication: self.terminateHarnessApplication
        )
        do {
            guard let client else {
                throw LiveHarnessProtocolError.invalidMessage("missing harness client")
            }
            let connection = try client.openSession()
            self.connection = connection
            let command = try connection.receiveOneCommand()
            guard command.launchNonce == client.launchNonce else {
                throw LiveHarnessProtocolError.peerRejected("launch nonce")
            }
            eventWriterSlot.install(HarnessEventWriter(connection: connection))

            // Install the observer before the controller can start.  Every
            // activation/health event therefore has a live, authenticated
            // bidirectional destination, including the initial generation.
            controller.setHarnessObserver { event in
                eventWriterSlot.enqueue(event)
            }
            // Start the authenticated control-loss waiter before invoking the
            // controller.  A peer EOF, duplicate/trailing byte, or writer
            // close can therefore stop a source whose start() is suspended.
            let coordinator = LiveHarnessControlCoordinator()
            let activeController = self.controller
            await coordinator.run(
                start: {
                    await activeController.start(
                        sessionID: command.sessionID,
                        streamKey: command.streamKey,
                        gatewayBase: command.gateway,
                        launchNonce: command.launchNonce
                    )
                },
                stop: {
                    await self.stopControllerOnce()
                },
                waitForControlLoss: { ready in
                    do {
                        let request = try connection.waitForShutdownRequest(onReady: ready)
                        shutdownRequestSlot.install(request)
                    } catch {
                        // Every return from the post-command waiter is a
                        // terminal control-loss observation unless the exact
                        // shutdown request was installed.  Details stay out
                        // of app logs and no credential-bearing bytes escape.
                    }
                }
            )
            if let request = shutdownRequestSlot.take() {
                // Controller.stop() has returned before the coordinator
                // completes.  Join every queued event write before the sole
                // connection writer emits the matching acknowledgement.
                eventWriterSlot.finish()
                try connection.sendShutdownAcknowledgement(request)
            }
        } catch {
            // Do not expose command bytes or credentials in app output.
            NSLog("TarsCompanion: live harness control ended")
        }
        await lifecycle.finalize()
    }
}

@main
struct TarsCompanionApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var controller: CompanionSessionController
    @StateObject private var harnessRuntime: HarnessSessionRuntime
    private let harnessMode: Bool

    init() {
        let detectedHarnessMode = LiveHarnessLaunchConfiguration.isHarnessMode(arguments: CommandLine.arguments)
        harnessMode = detectedHarnessMode
        var parsedConfiguration: LiveHarnessLaunchConfiguration?
        var parsedError: String?
        if detectedHarnessMode {
            do { parsedConfiguration = try LiveHarnessLaunchConfiguration.parse(arguments: CommandLine.arguments) }
            catch { parsedError = String(describing: error) }
        }
        let harnessClient = parsedConfiguration.map {
            LiveHarnessControlClient(configuration: $0, expectedServerEUID: Int32(geteuid()))
        }
        do {
            let preference: SystemAudioEnginePreference
            if parsedConfiguration != nil {
                // Harness mode is never automatic: the signed app must ask
                // for Process Tap explicitly on every supported OS.
                preference = .processTap
            } else {
                preference = try SystemAudioEnginePreference.preference(fromLaunchArguments: CommandLine.arguments)
            }
            let configuredController = CompanionSessionController(
                enginePreference: preference,
                launchArgumentError: parsedError,
                harnessMode: detectedHarnessMode
            )
            _controller = StateObject(wrappedValue: configuredController)
            _harnessRuntime = StateObject(wrappedValue: HarnessSessionRuntime(
                controller: configuredController,
                client: harnessClient,
                harnessMode: detectedHarnessMode
            ))
        } catch {
            // Preserve the diagnostic on the controller, but harness-mode
            // startup still takes the explicit fail/terminate lifecycle path;
            // the controller refuses every start and constructs no source.
            // Historical contract: launchArgumentError: String(describing: error)
            let configuredController = CompanionSessionController(
                enginePreference: .automatic,
                launchArgumentError: parsedError ?? String(describing: error),
                harnessMode: detectedHarnessMode
            )
            _controller = StateObject(wrappedValue: configuredController)
            _harnessRuntime = StateObject(wrappedValue: HarnessSessionRuntime(
                controller: configuredController,
                client: harnessClient,
                harnessMode: detectedHarnessMode
            ))
        }
    }

    var body: some Scene {
        MenuBarExtra {
            CompanionMenuView(controller: controller, harnessMode: harnessMode)
                .onOpenURL { url in
                    guard !harnessMode else { return }
                    if let request = JoinLink.parse(url.absoluteString) {
                        handleJoin(request)
                    } else {
                        NSLog("TarsCompanion: link inválido")
                    }
                }
        } label: {
            Image(systemName: iconName(for: controller.state))
                .onAppear {
                    // Wire up the AppDelegate URL event handler on label appearance, which executes
                    // immediately upon app launch when the menu bar item is mounted in the macOS status bar.
                    if harnessMode {
                        // The status-item label is mounted at launch even when
                        // the popover content has never been presented.  The
                        // runtime owns its task guard, so SwiftUI appearance
                        // refreshes cannot start a second harness session.
                        harnessRuntime.startIfNeeded()
                    } else {
                        appDelegate.onJoinRequest = { request in handleJoin(request) }
                    }
                }
        }
        .menuBarExtraStyle(.window)
    }

    private func handleJoin(_ request: JoinRequest) {
        guard !harnessMode else { return }
        let storedGateway = UserDefaults.standard.string(forKey: "tars_gateway_base") ?? "ws://127.0.0.1:8000/api/stream/native"
        let effectiveGateway = request.gateway ?? storedGateway

        switch controller.state {
        case .idle, .error:
            Task {
                await controller.start(
                    sessionID: request.sessionID,
                    streamKey: request.streamKey,
                    gatewayBase: effectiveGateway
                )
            }
        case .connecting, .capturing, .reconnecting:
            if controller.activeSessionID == request.sessionID {
                NSLog("TarsCompanion: mesma sessão já ativa (%@) — link ignorado", String(request.sessionID.prefix(8)))
            } else {
                NSLog("TarsCompanion: trocando de sessão (%@ -> %@)",
                      controller.activeSessionID.map { String($0.prefix(8)) } ?? "nenhuma",
                      String(request.sessionID.prefix(8)))
                Task {
                    await controller.stop()
                    await controller.start(
                        sessionID: request.sessionID,
                        streamKey: request.streamKey,
                        gatewayBase: effectiveGateway
                    )
                }
            }
        }
    }

    private func iconName(for state: CompanionState) -> String {
        switch state {
        case .idle:
            return "waveform.circle"
        case .connecting:
            return "ellipsis.circle"
        case .capturing:
            return "waveform.circle.fill"
        case .reconnecting:
            return "arrow.triangle.2.circlepath.circle"
        case .error:
            return "exclamationmark.triangle.fill"
        }
    }
}

struct CompanionMenuView: View {
    @ObservedObject var controller: CompanionSessionController
    let harnessMode: Bool
    @AppStorage("tars_gateway_base") private var gatewayBase: String = "ws://127.0.0.1:8000/api/stream/native"
    @AppStorage("tars_cockpit_url") private var cockpitURL: String = "http://localhost:3000"

    @State private var sessionInput: String = ""
    @State private var isInvalidLink: Bool = false
    @State private var framesSent: Int = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            statusSection

            if controller.state == .capturing {
                Text("\(framesSent) quadros enviados")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .task {
                        while !Task.isCancelled {
                            framesSent = controller.systemAudioFramesSent
                            try? await Task.sleep(nanoseconds: 1_000_000_000)
                        }
                    }
            }

            if !harnessMode && (controller.state == .idle || isErrorState(controller.state)) {
                VStack(alignment: .leading, spacing: 6) {
                    TextField("Cole o link ou código da sessão", text: $sessionInput)
                        .textFieldStyle(.roundedBorder)

                    if isInvalidLink {
                        Text("Link inválido")
                            .font(.caption)
                            .foregroundColor(.red)
                    }

                    Button("Conectar") {
                        handleConnect()
                    }
                    .disabled(sessionInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }

            if !harnessMode && controller.state != .idle {
                Button("Parar") {
                    Task {
                        await controller.stop()
                    }
                }
            }

            if !harnessMode {
                Divider()

                Button("Abrir T.A.R.S.") {
                    if let url = URL(string: cockpitURL) {
                        NSWorkspace.shared.open(url)
                    }
                }

                DisclosureGroup("Ajustes") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Gateway Base:")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        TextField("Gateway Base", text: $gatewayBase)
                            .textFieldStyle(.roundedBorder)

                        Text("Cockpit URL:")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        TextField("Cockpit URL", text: $cockpitURL)
                            .textFieldStyle(.roundedBorder)
                    }
                    .padding(.top, 4)
                }

                Divider()
            }

            Button("Sair") {
                NSApplication.shared.terminate(nil)
            }
        }
        .padding(16)
        .frame(width: 320)
    }

    @ViewBuilder
    private var statusSection: some View {
        switch controller.state {
        case .idle:
            Text("Ocioso")
                .font(.headline)
        case .connecting:
            Text("Conectando…")
                .font(.headline)
        case .capturing:
            let prefix = controller.activeSessionID.map { String($0.prefix(8)) } ?? ""
            Text("Capturando — sessão \(prefix)\n\(engineLabel)")
                .font(.headline)
        case .reconnecting:
            Text("Reconectando…")
                .font(.headline)
                .foregroundColor(.orange)
        case .error(let msg):
            Text(msg)
                .font(.subheadline)
                .foregroundColor(.red)
        }
    }

    private var engineLabel: String {
        let engine: String
        switch controller.resolvedSystemAudioEngine {
        case .processTap: engine = "Process Tap"
        case .screenCaptureKit: engine = "ScreenCaptureKit"
        case nil: engine = "áudio do sistema"
        }
        let health: String
        switch controller.systemAudioHealth.permission {
        case .unknown: health = "permissão desconhecida"
        case .granted: health = "permissão verificada"
        case .denied: health = "permissão negada"
        case .revoked: health = "permissão revogada"
        }
        return "\(engine) — \(health)"
    }

    private func isErrorState(_ state: CompanionState) -> Bool {
        if case .error = state { return true }
        return false
    }

    private func handleConnect() {
        guard let request = JoinLink.parse(sessionInput) else {
            isInvalidLink = true
            return
        }
        isInvalidLink = false
        let effectiveGateway = request.gateway ?? gatewayBase
        Task {
            await controller.start(
                sessionID: request.sessionID,
                streamKey: request.streamKey,
                gatewayBase: effectiveGateway
            )
        }
    }
}
