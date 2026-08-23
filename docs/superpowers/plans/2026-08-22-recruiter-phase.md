# Recruiter Phase — Roadmap Plan

> Spec: `docs/superpowers/specs/2026-08-22-recruiter-phase-design.md`. Execution model: Antigravity/Gemini builds from detailed per-task briefs (`docs/builder/task-NN-brief.md`, written just-in-time by the Claude designer); Claude verifies (suites + diff review) and commits. This roadmap intentionally lists tasks one-line-each — the DETAIL lives in each brief at dispatch time, keeping the expensive-model footprint low.

## Task ladder

| # | Task | Workstream | Depends on |
|---|---|---|---|
| 01 | `TarsCompanionApp` SwiftPM target: MenuBarExtra shell, `CompanionSessionController` (library), state machine, paste-pairing, tests | W1 | — |
| 02 | Deep link: `parseJoinURL` (library, tested), kAEGetURL/onOpenURL handling, Info.plist template + `scripts/package_menubar_app.sh` (unsigned bundle) | W1 | 01 |
| 03 | Cockpit "Conectar companion" button (deep link) replacing/augmenting `CompanionCommand`, + download pointer | W1/onboarding | 02 |
| 04 | Backend: stream key via WS subprotocol (browser + companion + tests; query-param path removed), key scrubbed from companion banner/logs | W4 | — |
| 05 | Backend: companion hello message (intended sources) → never-produced-frames alarm + `reconnecting` health state; frontend badge mapping | W4 | 04 |
| 06 | Backend: header session_id vs path validation; Cloud Run readiness pass (Dockerfile review, timeouts, health endpoint, env/config docs) | W4 | 04 |
| 07 | Firebase auth ON: allowlist config (5 pilot emails), AUTH_BYPASS=false path verified locally, frontend sign-in flow polish | W4 | 06 |
| 08 | Deploy: Cloud Run (min=max=1, timeout 3600) + Firebase Hosting; execute + record the hosted-gate checklist; live smoke with 2 accounts | W4 | 07 (owner+Claude, not builder) |
| 09 | Signing pipeline: Developer ID + hardened runtime + notarytool + stapled .dmg via script; entitlement testing for taps | W3 | 02, cert installed |
| 10 | `ProcessTapSystemAudioSource` + engine selection + permission probe + zero-buffer watchdog + tests | W2 | 09 |
| 11 | Live re-proof on taps engine (extend `verify_live_system_audio.py` with `--engine taps`), evidence doc | W2 | 10 |
| 12 | Onboarding: recruiter-facing doc rewrite (install .dmg → sign in → conectar), in-app first-run copy, pilot dry-run checklist | onboarding | 03, 08, 09 |

Order of dispatch: 01 → 02 → 03 → 04 → 05 → 06 → 07 → (08 owner/Claude) → 09 → 10 → 11 → 12. Tasks 04–07 may interleave with 01–03 when the working tree is clean between commits (one builder task in flight at a time — same checkout).

## Standing verification gate (every task)

Backend `.venv/bin/python -m pytest backend/tests -q` (≥290) · `swift test` in `companion/native-macos` (≥55) · `cd frontend && npm test` (≥56) + `npm run build` when frontend touched · `dotnet test` when windows touched (15) · diff review of exactly the builder's changes · protected files untouched (`AGENTS.md`, `frontend/AGENTS.md`, `frontend/CLAUDE.md`) · commit by Claude with explicit pathspecs only.
