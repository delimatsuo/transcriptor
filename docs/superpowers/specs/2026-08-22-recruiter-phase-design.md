# Recruiter Phase — Menu Bar App, Taps, Signing, Onboarding, Hosted Minimal (Design)

- **Date:** 2026-08-22
- **Owner:** Deli Matsuo
- **Authority basis:** Owner-approved in the 2026-08-21/22 session ("Start the recruiter-phase design…", hosting Option A chosen, design approved by supplying the two blocking inputs). Builds on the merged solo-pilot hardening (PR #37, main@67d6c85) and ADR 0003. Grants pilot-cohort scope only — no broad/customer launch.
- **Build model:** Claude (Fable) designs, plans, and verifies; the Antigravity agent (Gemini) implements from `docs/builder/task-NN-brief.md` files. Protocol: `docs/builder/README.md`.

## 1. Goal & success criterion

A named Ella recruiter with zero technical setup: installs `TarsCompanion.dmg`, signs into the hosted cockpit with her Ella Google account, clicks **"Conectar companion"**, grants ONE audio permission ONCE, and conducts an interview that is transcribed with correct Entrevistador/Candidato labels and produces the post-session report. No terminal, no virtual drivers, no credentials on her machine.

**Pilot allowlist (Firebase, named-email):** barbara@ellaexecutivesearch.com, iasmin@ellaexecutivesearch.com, mariana@ellaexecutivesearch.com, ina@ellaexecutivesearch.com, deli@ellaexecutivesearch.com. (Verify exact account spellings against real Google accounts at Firebase-config time; "ina@" normalized from owner's "Ina@".)

## 2. Workstreams & decisions

### W1 — Menu bar app (`TarsCompanion.app`)
- SwiftUI `MenuBarExtra` app as a NEW SwiftPM executable target `TarsCompanionApp` in `companion/native-macos` + a bundling script that assembles the `.app` (Info.plist, icon, codesign) — **no Xcode project / pbxproj** (script-based, agent-friendly, matches repo convention). macOS 13.0 floor at bundle level; taps gate at runtime.
- Menu: capture-state icon (idle / conectando / capturando / **reconectando** / erro — icon state change while capture may be active satisfies `docs/product/companion-web-state-contract.md`'s visible-indicator requirement), per-source health, active session label, Iniciar/Parar, "Abrir T.A.R.S.", Ajustes (gateway URL), Sair. `LSUIElement=true` (no Dock icon). All copy pt-BR.
- **Pairing:** custom URL scheme `tars-companion://join?session=<id>&key=<stream_key>&gateway=<wss-url>` registered via Info.plist `CFBundleURLTypes`; cockpit gains a "Conectar companion" button producing that link (W4 task). Paste-a-link/code field in the app as fallback. Deep link carries the key (session-scoped, dies at session stop) — acceptable at pilot scope; revisit if keys ever become durable.
- Reuses the live-proven library unchanged (capture sources, `ReconnectingAudioSink`, `OrderedFrameRelay`, `CompanionOptions` URL building). A new library-level `CompanionSessionController` encapsulates start/stop lifecycle for the app (CLI keeps its own orchestration for now; consolidation is a later cleanup task). **ARC rule: the controller retains sources/sink as stored properties — never write-only locals (the PR #37 root-cause lesson).**

### W2 — Taps migration
- New `ProcessTapSystemAudioSource: CaptureSource` using `AudioHardwareCreateProcessTap` + `CATapDescription` (global/system tap, process-exclusion of self), consumed via an aggregate device; convert to 16 kHz mono Int16 with `AVAudioConverter` (NOT naive labeling — the mic-path lesson).
- Engine selection at runtime: macOS ≥ 14.4 → taps ("Gravação de Tela e Áudio do Sistema" audio-only tier: one-time prompt via `NSAudioCaptureUsageDescription`, no Sequoia monthly re-approval nag); 13.0–14.3 → existing `ScreenCaptureKitSystemAudioSource` fallback. Manual override flag for testing.
- **Denial is silence** (no public permission-query API): first-start probe = 2 s capture + zero-detection → on all-zeros, pt-BR instructions naming the exact Settings pane; never run silently dead.
- **Zero-buffer watchdog**: sustained exact-zero buffers while callbacks fire → full tap+aggregate teardown/rebuild (Tahoe 26.5-beta decay report), surfacing a coverage gap if audio was lost.
- Sequenced AFTER signing exists (taps require a properly signed binary even locally); SCK carries development until then.

### W3 — Signing, notarization, distribution
- Apple Developer account: EXISTS (owner-confirmed; org-vs-individual to be read off the cert at setup — either acceptable for the internal pilot).
- Developer ID Application cert, hardened runtime, `notarytool` submit + staple, `.dmg` (script-built). Verify at implementation whether taps under hardened runtime need the `com.apple.security.device.audio-input` entitlement — decide by testing, not assumption.
- NO auto-update framework for the pilot (re-download on update); Sparkle is broad-launch scope.
- Existing CLI keeps shipping (the live-proof script depends on it).

### W4 — Hosted minimal (single-tenant Ella)
- Compute moves, data doesn't: Firestore + Google STT are already cloud (`transcriptor-490222`). Backend → Cloud Run, **single instance (min=max=1)** so in-memory stream keys/session state stay valid (documented pilot limitation), WS/request timeout raised ≥ 3600 s for hour-long streams. Frontend → Firebase Hosting.
- `AUTH_BYPASS=false`; Firebase sign-in (Google) with the named-email allowlist above (week-4 authenticated-tenancy code is the substrate — verify `auth_allowed_emails` config path).
- Pre-hosted must-change list lands here: stream key OUT of the WS query string (move to `Sec-WebSocket-Protocol` subprotocol, mirroring the UI WS ticket pattern, for BOTH browser and companion; scrub any residual key logging incl. the companion banner); TLS via Cloud Run (`wss://`); validate header `session_id` against the WS path.
- Cockpit UX completions (adjudicated in PR #37's final review): companion **hello message** declaring intended sources on connect → gateway can alarm "selected source never produced frames" (>15 s) and drive a **reconectando** state distinct from stopped.
- LGPD posture unchanged (data location same as today; deletion endpoints exist); the formal hosted-gate checklist (`docs/launch/week-4-hosted-gate-checklist.md`) gets executed and recorded as part of deploy, not skipped.

## 3. Sequencing

1. **W1 first** (buildable/testable now on the owner's Mac against the local backend; no cert needed for local dev — TCC grant exists).
2. **W4 backend hardening** in parallel via builder (pure Python); actual deploy is an owner/Claude step with gcloud.
3. **W3 → W2** once the cert is in the keychain (owner action: confirm cert installed).
4. Onboarding polish + pilot dry-run with recruiter #1 (suggest barbara@ or per owner).

## 4. Out of scope

Windows capture (exe still refuses non-simulate — unchanged), auto-update, multi-org tenancy, on-device STT, Chrome-extension work, broad launch (needs its own gate per ADR 0003).

## 5. Risks

- Taps + hardened runtime/entitlement unknowns → resolved empirically at W2 start; SCK fallback always available.
- Cloud Run single-instance restart drops in-memory sessions mid-interview → pilot-acceptable; document "if the app says reconectando > 2 min, restart the session"; durable keys are broad-launch work.
- Deep-link key exposure in local logs → session-scoped, pilot-accepted; revisit with durable keys.
- Allowlist spellings unverified until Firebase config.
- Builder (Gemini) has no session context → every brief must be fully self-contained; verification (tests + diff review) is the Claude-side gate before any commit.
