# Task 02b — App diagnostics: make failures visible

Read `docs/builder/README.md` first. Repo: `"/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"` (quote — space in path). Work only in `companion/native-macos/`.

## Why (evidence from a real demo run, 2026-08-23)

The bundled app received a deep link correctly (`URL recebida` logged), then `CompanionSessionController.start(...)` returned without opening any socket and **logged nothing at all**. The failure was only visible as text inside the menu-bar window. Diagnosing it cost a long debugging session. Every early-return in `start()` must announce itself.

## File plan (modify only these two)

1. `companion/native-macos/Sources/TarsNativeCompanion/CompanionSessionController.swift`
   - Add a private helper `private func log(_ message: String) { NSLog("TarsCompanion: %@", message) }` (import Foundation is already there).
   - Emit a log line at EVERY branch of `start(...)`:
     - entry: `log("start solicitado — sessão \(sessionID.prefix(8)), gateway \(gatewayBase)")`
     - ignored because busy (the `guard state == .idle || isErrorState(state)` path): `log("start ignorado — estado atual \(state)")` BEFORE returning
     - preflight failed: `log("preflight de permissão falhou — CGPreflightScreenCaptureAccess=false")` immediately before setting the error state
     - preflight passed: `log("permissão de captura concedida")`
     - URL build failure: `log("falha ao montar URL do gateway: \(error.localizedDescription)")`
     - sink created / started: `log("sink iniciado — conectando a \(url.absoluteString.replacingOccurrences(of: streamKey, with: "***"))")` — **the stream key MUST be redacted in logs**
     - source start success: `log("captura de áudio do sistema ativa")`
     - source start failure: `log("falha ao iniciar captura: \(error.localizedDescription)")` before setting the error state
   - In `stop()`: `log("sessão encerrada")`.
   - In the `onStateChange` handler: `log("conexão: \(connected ? "estabelecida" : "perdida")")`.
2. `companion/native-macos/Tests/TarsNativeCompanionTests/CompanionSessionControllerTests.swift`
   - Add ONE test `testStartIgnoredLogsAndKeepsState` is NOT needed. Instead add: `testErrorStateMessageIsSurfaced` — using the existing failing-source fake, assert the controller's `.error` message is non-empty and contains "Falha ao iniciar". (Keep all existing tests passing unchanged.)

## Constraints

- Do NOT change control flow, state transitions, or any public API — logging only, plus the one new test.
- The stream key must never appear in any log line (redact as shown).
- pt-BR log text as given.

## Verification

```
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift test    # 72 existing + 1 new, 0 failures
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && bash scripts/package_menubar_app.sh   # rebuild bundle
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && pkill -x TarsCompanionApp 2>/dev/null; "dist/TarsCompanion.app/Contents/MacOS/TarsCompanionApp" > /tmp/diag.log 2>&1 &
sleep 3; open "tars-companion://join?session=diagtest&key=diagkey&gateway=ws://127.0.0.1:8000/api/stream/native"; sleep 5; cat /tmp/diag.log; pkill -x TarsCompanionApp
```
Expected in `/tmp/diag.log`: `URL recebida`, then `start solicitado`, then either the preflight-failure line or the permission-granted + sink lines. **Paste that log verbatim in your report** — it is the deliverable that proves the app is now diagnosable. Confirm no stream key appears in it.

## Report

`docs/builder/task-02b-report.md`: files changed, test output, the verbatim diagnostic log, and confirmation that the key was redacted.
