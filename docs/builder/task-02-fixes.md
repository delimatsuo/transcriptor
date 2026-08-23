# Task 02 — Fixes round 1

One finding from designer review. Fix exactly this, re-verify, APPEND a fix section to `docs/builder/task-02-report.md`.

## FINDING (Important): cold-start deep link can be silently dropped

Your runtime verification launched the app first, then opened the URL (warm start). The untested path is COLD start: the app is NOT running, the user clicks a `tars-companion://` link, macOS launches the app and delivers the kAEGetURL event during startup. If that event arrives before the MenuBarExtra label's `.onAppear` has assigned `appDelegate.onJoinRequest`, the handler is nil and the click does nothing — the recruiter's very first interaction fails silently.

## FIX (exactly this, in `companion/native-macos/Sources/TarsCompanionApp/AppDelegate.swift`)

Buffer the request until a handler exists:

```swift
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

    // ... applicationDidFinishLaunching unchanged ...

    @objc private func handleGetURL(_ event: NSAppleEventDescriptor, withReplyEvent reply: NSAppleEventDescriptor) {
        guard let urlString = event.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue else { return }
        NSLog("TarsCompanion: URL recebida: %@", urlString)
        guard let request = JoinLink.parse(urlString) else {
            NSLog("TarsCompanion: link inválido")
            return
        }
        if let handler = onJoinRequest {
            handler(request)
        } else {
            NSLog("TarsCompanion: app iniciando — link armazenado para entrega")
            pendingRequest = request
        }
    }
}
```

No other file changes. Do not alter TarsCompanionApp.swift.

## Verification (paste real output in the fix report)

```
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift test           # 72/72 still
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && bash scripts/package_menubar_app.sh          # rebuild bundle with the fix
pkill -x TarsCompanionApp 2>/dev/null; sleep 1
open "tars-companion://join?session=cold-start-test&key=cold-key"                                 # COLD start: this both launches the app and delivers the link
sleep 5
log show --last 2m --predicate 'eventMessage CONTAINS "TarsCompanion"' --style compact | tail -8
pgrep -x TarsCompanionApp && pkill -x TarsCompanionApp
```

Expected in the log: `URL recebida` for the cold-start link AND evidence the session actually started — either the buffered-delivery line (`link armazenado para entrega`) followed by controller activity, or direct delivery if timing won the race; either way the app must end up attempting the session (it will sit in reconnecting with no backend — fine). If the log shows `URL recebida` but no delivery evidence, the fix is not working — say so plainly rather than declaring success.
