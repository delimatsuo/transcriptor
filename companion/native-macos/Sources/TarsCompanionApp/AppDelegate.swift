import AppKit
import TarsNativeCompanion

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var pendingRequest: JoinRequest?

    /// Set by TarsCompanionApp at startup; called on the main actor with each parsed join request.
    /// A request that arrives before this is assigned (cold start via URL click) is buffered
    /// and delivered the moment the handler is set.
    var onJoinRequest: ((JoinRequest) -> Void)? {
        didSet {
            if let handler = onJoinRequest, let pending = pendingRequest {
                pendingRequest = nil
                handler(pending)
            }
        }
    }

    override init() {
        super.init()
        registerURLHandler()
    }

    func applicationWillFinishLaunching(_ notification: Notification) {
        registerURLHandler()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        registerURLHandler()
    }

    private func registerURLHandler() {
        NSAppleEventManager.shared().setEventHandler(
            self,
            andSelector: #selector(handleGetURL(_:withReplyEvent:)),
            forEventClass: AEEventClass(kInternetEventClass),
            andEventID: AEEventID(kAEGetURL)
        )
    }

    @objc private func handleGetURL(_ event: NSAppleEventDescriptor, withReplyEvent reply: NSAppleEventDescriptor) {
        guard let urlString = event.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue else { return }
        JoinLink.receive(urlString) { [weak self] request in
            guard let self = self else { return }
            if let handler = self.onJoinRequest {
                handler(request)
            } else {
                NSLog("TarsCompanion: app iniciando — link armazenado para entrega")
                self.pendingRequest = request
            }
        }
    }
}
