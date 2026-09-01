import AppKit
import SwiftUI
import TarsNativeCompanion

@main
struct TarsCompanionApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var controller = CompanionSessionController()

    var body: some Scene {
        MenuBarExtra {
            CompanionMenuView(controller: controller)
                .onOpenURL { url in
                    JoinLink.receive(url.absoluteString) { request in
                        handleJoin(request)
                    }
                }
        } label: {
            Image(systemName: iconName(for: controller.state))
                .onAppear {
                    // Wire up the AppDelegate URL event handler on label appearance, which executes
                    // immediately upon app launch when the menu bar item is mounted in the macOS status bar.
                    appDelegate.onJoinRequest = { request in
                        handleJoin(request)
                    }
                }
        }
        .menuBarExtraStyle(.window)
    }

    private func handleJoin(_ request: JoinRequest) {
        switch controller.state {
        case .idle, .error:
            let effectiveGateway = UserDefaults.standard.string(forKey: "tars_gateway_base") ?? "ws://127.0.0.1:8000/api/stream/native"
            Task {
                await controller.start(
                    sessionID: request.sessionID,
                    streamKey: request.streamKey,
                    gatewayBase: effectiveGateway
                )
            }
        case .connecting, .capturing, .reconnecting:
            NSLog("TarsCompanion: sessão já ativa — link ignorado")
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

            if controller.state == .idle || isErrorState(controller.state) {
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

            if controller.state != .idle {
                Button("Parar") {
                    Task {
                        await controller.stop()
                    }
                }
            }

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
            Text("Capturando — sessão \(prefix)")
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
        Task {
            await controller.start(
                sessionID: request.sessionID,
                streamKey: request.streamKey,
                gatewayBase: gatewayBase
            )
        }
    }
}
