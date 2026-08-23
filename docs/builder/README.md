# Builder protocol (Antigravity agent — read this first, every task)

You are the BUILDER for the T.A.R.S. recruiter phase. A separate designer agent (Claude) writes your task briefs, reviews your work, and owns all commits. Your contract:

1. **Read your brief completely**: `docs/builder/task-NN-brief.md` (NN given to you in chat). It is self-contained: requirements, exact file paths, interfaces, tests, and commands. Follow it exactly — the exact values in it (names, strings, signatures, thresholds) are contractual, not suggestions.
2. **Touch ONLY the files the brief lists.** If you believe another file must change, STOP and write why in your report instead of changing it.
3. **NEVER modify, stage, or delete**: `AGENTS.md`, `frontend/AGENTS.md`, `frontend/CLAUDE.md`. **NEVER run `git add`, `git commit`, `git checkout`, `git restore`, or any git write command** — you edit the working tree only; the designer reviews the diff and commits.
4. **TDD when the brief specifies tests**: write the failing test first, run it, see the expected failure, implement, see it pass. Record both outputs.
5. **Run the verification commands the brief lists** (quote the repo path — it contains a space: `"/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"`). All listed suites must pass before you finish.
6. **Write your report** to `docs/builder/task-NN-report.md`: what you implemented; every file changed; test commands + real output (RED and GREEN); anything you could not do, skipped, or are unsure about — stated plainly. Honesty over polish: an accurate "I couldn't get X working" is a good report; a claimed success that isn't real will be caught in review and costs a full round-trip.
7. **User-facing strings are Brazilian Portuguese**, matching the existing style in the codebase.
8. If a fixes file exists for your task (`docs/builder/task-NN-fixes.md`), it lists review findings: fix exactly those, re-run the covering tests, APPEND a fix section to your report.

Project context in one paragraph (details always come from the brief, not from memory): T.A.R.S. is a real-time interview assistant — Python/FastAPI backend (`backend/`, Google STT v2, Firestore), Next.js cockpit (`frontend/`), native macOS companion (`companion/native-macos`, SwiftPM: library `TarsNativeCompanion` + CLI `tars-companion`) that captures system audio (the remote candidate) and streams 50 ms Int16 PCM frames over an authenticated WebSocket to the backend. The recruiter phase adds a menu-bar app, hosted backend, signing, and a CoreAudio-taps capture engine.
