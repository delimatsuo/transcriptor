# Task 02 — Deep-link handling + app bundle: Info.plist, packaging script, URL events

Read `docs/builder/README.md` first (protocol, hard rules). Repo: `"/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"` (quote the path — space). Task 01 is merged: targets `TarsNativeCompanion` (library), `tars-companion` (CLI), `TarsCompanionApp` (menu bar app) all build; `JoinLink.parse(_:) -> JoinRequest?` and `CompanionSessionController` (MainActor: `state`, `activeSessionID`, `start(sessionID:streamKey:gatewayBase:) async`, `stop() async`) exist in the library.

## Objective

Make the app a real `.app` bundle that macOS launches and routes `tars-companion://join?...` links to, auto-starting the session. Ad-hoc signing for now (Developer ID is a later task).

## File plan

**Create:**
1. `companion/native-macos/Resources/TarsCompanionApp-Info.plist` — exact keys:
   - `CFBundleIdentifier` = `com.ellaexecutivesearch.tarscompanion`
   - `CFBundleName` = `TarsCompanion`; `CFBundleDisplayName` = `TarsCompanion`
   - `CFBundleExecutable` = `TarsCompanionApp`
   - `CFBundlePackageType` = `APPL`; `CFBundleShortVersionString` = `0.1.0`; `CFBundleVersion` = `1`
   - `LSMinimumSystemVersion` = `13.0`
   - `LSUIElement` = `true` (no Dock icon)
   - `NSAudioCaptureUsageDescription` = `O TarsCompanion captura o áudio do sistema para transcrever a fala do candidato durante entrevistas no T.A.R.S.`
   - `CFBundleURLTypes` = one entry: `CFBundleURLName` = `com.ellaexecutivesearch.tarscompanion.join`, `CFBundleURLSchemes` = [`tars-companion`]
2. `scripts/package_menubar_app.sh` (repo root `scripts/`, `chmod +x`, bash, `set -euo pipefail`):
   - `swift build -c release --package-path companion/native-macos` (only the app product is needed: `--product TarsCompanionApp`)
   - Assemble `dist/TarsCompanion.app/Contents/{MacOS,Resources}`: copy the built `TarsCompanionApp` binary into `Contents/MacOS/`, copy the Info.plist above into `Contents/Info.plist`, write `Contents/PkgInfo` containing exactly `APPL????`
   - `codesign --force --deep -s - dist/TarsCompanion.app` (ad-hoc — same style as the existing `scripts/package_macos_companion.sh`; read it first and match its conventions/quoting)
   - Print the bundle path and `codesign -dv` summary at the end
3. `companion/native-macos/Sources/TarsCompanionApp/AppDelegate.swift`:
```swift
import AppKit
import TarsNativeCompanion

final class AppDelegate: NSObject, NSApplicationDelegate {
    /// Set by TarsCompanionApp at startup; called on the main actor with each parsed join request.
    var onJoinRequest: ((JoinRequest) -> Void)?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSAppleEventManager.shared().setEventHandler(
            self,
            andSelector: #selector(handleGetURL(_:withReplyEvent:)),
            forEventClass: AEEventClass(kInternetEventClass),
            andEventID: AEEventID(kAEGetURL)
        )
    }

    @objc private func handleGetURL(_ event: NSAppleEventDescriptor, withReplyEvent reply: NSAppleEventDescriptor) {
        guard let urlString = event.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue else { return }
        NSLog("TarsCompanion: URL recebida: %@", urlString)
        guard let request = JoinLink.parse(urlString) else {
            NSLog("TarsCompanion: link inválido")
            return
        }
        onJoinRequest?(request)
    }
}
```
**Modify:**
4. `companion/native-macos/Sources/TarsCompanionApp/TarsCompanionApp.swift`:
   - Add `@NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate` to the App struct.
   - Wire in `body`/`init` path (use `.onAppear`-safe pattern or configure in an `init` that runs after StateObject is available — the standard approach: set `appDelegate.onJoinRequest` inside the MenuBarExtra content's `.onAppear`, or via a small `connect(controller:)` helper called from the label's `.onAppear`; pick ONE clean spot and comment why): handler behavior —
     - If `controller.state` is `.idle` or `.error`: read the gateway default from `UserDefaults.standard.string(forKey: "tars_gateway_base") ?? "ws://127.0.0.1:8000/api/stream/native"`, then `Task { await controller.start(sessionID: request.sessionID, streamKey: request.streamKey, gatewayBase: request.gateway ?? storedGateway) }`.
     - Otherwise (already connecting/capturing/reconnecting): ignore the link and `NSLog("TarsCompanion: sessão já ativa — link ignorado")`. Do not stop/replace mid-session.
   - Also attach SwiftUI `.onOpenURL { url in ... }` on the MenuBarExtra content view doing the same via `JoinLink.parse(url.absoluteString)` — belt and suspenders; both paths MUST funnel through one shared `handleJoin(_ request: JoinRequest)` function so the policy lives in one place.

## Constraints

- Library and CLI files: DO NOT touch. Controller API: DO NOT change (if the wiring genuinely needs a controller change, STOP and write why in your report).
- `dist/` is gitignored — the bundle is a build artifact, only the script + plist + sources are deliverables.
- pt-BR for any user-visible string; NSLog lines as given.

## Verification (run all; paste real output in the report)

```
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift test          # 72 existing, 0 failures (no new unit tests required — JoinLink is already covered; the AE handler is thin glue)
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && bash scripts/package_menubar_app.sh        # produces dist/TarsCompanion.app, codesign summary printed
open "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/dist/TarsCompanion.app" && sleep 3
open "tars-companion://join?session=smoke-test&key=smoke-key" && sleep 2
log show --last 2m --predicate 'eventMessage CONTAINS "TarsCompanion"' --style compact | tail -5   # must show "URL recebida" for the smoke link
pgrep -x TarsCompanionApp && pkill -x TarsCompanionApp                                          # clean up
```
Expected: the log line proves the scheme routed to the bundle and parsed. (The session start will land in `reconnecting` since no backend is running — that is fine and worth noting, not fixing.) If `log show` is slow/empty, an acceptable alternative proof is redirecting NSLog via launching the binary from a terminal — document whichever you used.

## Report

`docs/builder/task-02-report.md`: files changed, verification transcript (build, codesign, the URL log line), anything uncertain — especially if the `open`-URL smoke could not be made to work headlessly; say exactly what you observed.
