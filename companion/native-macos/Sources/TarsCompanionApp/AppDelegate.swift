import AppKit
import TarsNativeCompanion

final class AppDelegate: NSObject, NSApplicationDelegate {
    typealias AppleEventRegistrar = (AppDelegate) -> Void
    let isHarnessMode: Bool
    private let appleEventRegistrar: AppleEventRegistrar
    private var pendingRequest: JoinRequest?

    /// Set by TarsCompanionApp at startup; called on the main actor with each parsed join request.
    /// A request that arrives before this is assigned (cold start via URL click) is buffered
    /// and delivered the moment the handler is set.
    var onJoinRequest: ((JoinRequest) -> Void)? {
        didSet {
            guard !isHarnessMode else {
                pendingRequest = nil
                return
            }
            if let handler = onJoinRequest, let pending = pendingRequest {
                pendingRequest = nil
                handler(pending)
            }
        }
    }

    override convenience init() {
        self.init(arguments: CommandLine.arguments, appleEventRegistrar: AppDelegate.registerDefaultURLHandler)
    }

    init(arguments: [String], appleEventRegistrar: @escaping AppleEventRegistrar = AppDelegate.registerDefaultURLHandler) {
        isHarnessMode = LiveHarnessLaunchConfiguration.isHarnessMode(arguments: arguments)
        self.appleEventRegistrar = appleEventRegistrar
        super.init()
        // Decide before any Apple Event registration. Malformed harness
        // arguments remain URL-dark as well.
        if !isHarnessMode { registerURLHandler() }
    }

    func applicationWillFinishLaunching(_ notification: Notification) {
        if !isHarnessMode { registerURLHandler() }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if !isHarnessMode { registerURLHandler() }
    }

    private func registerURLHandler() {
        guard !isHarnessMode else { return }
        appleEventRegistrar(self)
    }

    private static func registerDefaultURLHandler(_ delegate: AppDelegate) {
        NSAppleEventManager.shared().setEventHandler(
            delegate,
            andSelector: #selector(AppDelegate.handleGetURL(_:withReplyEvent:)),
            forEventClass: AEEventClass(kInternetEventClass),
            andEventID: AEEventID(kAEGetURL)
        )
    }

    @objc private func handleGetURL(_ event: NSAppleEventDescriptor, withReplyEvent reply: NSAppleEventDescriptor) {
        guard !isHarnessMode else { return }
        guard let urlString = event.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue else { return }
        guard let request = JoinLink.parse(urlString) else {
            NSLog("TarsCompanion: link inválido")
            return
        }
        NSLog("TarsCompanion: link aceita — sessão %@, gateway presente: %@", String(request.sessionID.prefix(8)), request.gateway == nil ? "não" : "sim")
        if let handler = onJoinRequest {
            handler(request)
        } else {
            NSLog("TarsCompanion: app iniciando — link armazenado para entrega")
            pendingRequest = request
        }
    }
}
