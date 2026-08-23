# Task 01 Report — Menu bar app shell: `TarsCompanionApp` target + `CompanionSessionController`

## 1. Summary of Work Implemented

Implemented the menu bar app target and session control layer for the macOS companion according to `docs/builder/task-01-brief.md`:
- **`URLSessionWebSocketTransport.swift`**: Extracted `URLSessionWebSocketTransport` from `TarsCompanionCLI/main.swift` into the `TarsNativeCompanion` library, making it `public` with default `URLSession` configuration so both CLI and GUI app targets can use it.
- **`JoinLink.swift`**: Implemented `JoinRequest` and `JoinLink.parse(_:)` supporting both full deep links (`tars-companion://join?session=X&key=Y[&gateway=Z]`) and compact formats (`X:Y`), trimming whitespace, automatically percent-decoding query items via `URLComponents`, and rejecting invalid/malformed inputs.
- **`CompanionSessionController.swift`**: Implemented `@MainActor` observable session controller managing companion lifecycle states (`.idle`, `.connecting`, `.capturing`, `.reconnecting`, `.error`). Handled ScreenCaptureKit preflight permission checks (skipping when a custom `sourceFactory` is injected for tests), RFC 3986 stream key encoding, wiring `ReconnectingAudioSink` state transitions to the controller, and retaining the `source` and `sink` as stored properties for session lifetime to prevent ARC teardown.
- **`TarsCompanionApp.swift`**: Implemented SwiftUI `MenuBarExtra` window interface with Brazilian Portuguese copy, dynamic SF Symbol menu icon per state, live frame counter (1s polling loop), session code input and validation, "Parar" control, "Abrir T.A.R.S." cockpit launcher, collapsible "Ajustes" persisted in `UserDefaults` (`tars_gateway_base` and `tars_cockpit_url`), and "Sair" termination.
- **`JoinLinkTests.swift`**: Added unit tests covering full URL with and without gateway, compact format, whitespace trimming, percent-encoded keys/gateways, and rejection of invalid/missing parameters.
- **`CompanionSessionControllerTests.swift`**: Added asynchronous unit tests with injected mock transports and mock capture sources covering: (a) start → `.capturing`, (b) transport failure → `.reconnecting`, (c) source startup failure → `.error`, (d) stop → `.idle`, and (e) start ignored while already capturing.
- **`Package.swift`**: Added `TarsCompanionApp` executable target and product depending on `TarsNativeCompanion`.
- **`TarsCompanionCLI/main.swift`**: Removed local `URLSessionWebSocketTransport` class definition, preserving all other lines byte-identical.

---

## 2. Files Changed

### Created
1. `companion/native-macos/Sources/TarsNativeCompanion/URLSessionWebSocketTransport.swift`
2. `companion/native-macos/Sources/TarsNativeCompanion/JoinLink.swift`
3. `companion/native-macos/Sources/TarsNativeCompanion/CompanionSessionController.swift`
4. `companion/native-macos/Sources/TarsCompanionApp/TarsCompanionApp.swift`
5. `companion/native-macos/Tests/TarsNativeCompanionTests/JoinLinkTests.swift`
6. `companion/native-macos/Tests/TarsNativeCompanionTests/CompanionSessionControllerTests.swift`

### Modified
7. `companion/native-macos/Package.swift`
8. `companion/native-macos/Sources/TarsCompanionCLI/main.swift`

---

## 3. TDD Output

### RED Phase (Test suite execution before implementation)
Command: `cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift test`

```
/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos/Tests/TarsNativeCompanionTests/CompanionSessionControllerTests.swift:116:32: error: cannot find 'CompanionSessionController' in scope
116 |         let controller = await CompanionSessionController(
    |                                `- error: cannot find 'CompanionSessionController' in scope

/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos/Tests/TarsNativeCompanionTests/CompanionSessionControllerTests.swift:123:86: error: cannot infer contextual base in reference to member 'reconnecting'
123 |         let reached = await waitForState(controller: controller, condition: { $0 == .reconnecting })
    |                                                                                      `- error: cannot infer contextual base in reference to member 'reconnecting'

/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos/Tests/TarsNativeCompanionTests/CompanionSessionControllerTests.swift:126:32: error: type 'Equatable' has no member 'reconnecting'
126 |         XCTAssertEqual(state, .reconnecting)
    |                                `- error: type 'Equatable' has no member 'reconnecting'

/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos/Tests/TarsNativeCompanionTests/JoinLinkTests.swift:8:31: error: cannot find 'JoinLink' in scope
8   |         guard let request = JoinLink.parse(input) else {
    |                               `- error: cannot find 'JoinLink' in scope
```

### GREEN Phase (Test suite execution after implementation)
Command: `cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift test`

```
Test Suite 'JoinLinkTests' passed at 2026-08-22 22:04:41.658.
	 Executed 12 tests, with 0 failures (0 unexpected) in 0.005 (0.008) seconds
Test Suite 'CompanionSessionControllerTests' passed at 2026-08-22 22:04:41.650.
	 Executed 5 tests, with 0 failures (0 unexpected) in 0.065 (0.066) seconds
Test Suite 'All tests' passed at 2026-08-22 22:04:41.755.
	 Executed 72 tests, with 0 failures (0 unexpected) in 0.368 (0.385) seconds
```

---

## 4. Verification Suite Results

1. **Swift Package Tests**:
   - Command: `cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift test`
   - Result: 72 passed (55 existing + 17 new tests), 0 failures.
2. **Swift Package Build**:
   - Command: `cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift build`
   - Result: All 3 targets (`TarsNativeCompanion`, `tars-companion`, `TarsCompanionApp`) built cleanly with zero warnings/errors.
3. **Backend Regression Tests**:
   - Command: `cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests -q`
   - Result: 290 passed in 5.48s, untouched.

---

## 5. Notes & Observations

- **GUI / MenuBarExtra Runtime**: While `TarsCompanionApp` compiles and links against SwiftUI and `TarsNativeCompanion` without issues and all backing logic (`CompanionSessionController`, `JoinLink`) is verified via unit tests, live menu bar window popover rendering and interactive OS clicks require running the app binary interactively in a macOS graphical desktop session (ready for the designer's smoke test).
- **Git Compliance**: No git commands were executed. Only files listed in the brief's file plan were touched.
