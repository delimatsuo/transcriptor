# Task 09 Report — Developer ID signing, hardened runtime, notarization, and DMG

## Implementation

Implemented the Task 09 release path with the contractual signing context:

- Added the `com.apple.security.device.audio-input` entitlement for the hardened-runtime app.
- Added an executable release script with `SIGN_IDENTITY`, `NOTARY_PROFILE`, and `APP_NAME` overrides; Developer ID and notary-profile preflight gates; deepest-first executable/bundle signing; strict verification; UDZO DMG creation and signing; notary submission with Invalid-result log retrieval; stapling/validation; SHA-256 output; and `spctl` summary output.
- Added the single local-development-only comment requested above the existing ad-hoc signing command.

## Files changed

1. `companion/native-macos/Resources/TarsCompanionApp.entitlements` (created)
2. `scripts/release_menubar_app.sh` (created, executable)
3. `scripts/package_menubar_app.sh` (one comment line added)
4. `docs/builder/task-09-report.md` (created)

## Verification actually run

All commands below ran in `/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/build/worktrees/task09-signing-pipeline` unless noted.

### Bash syntax

Command: `bash -n scripts/release_menubar_app.sh`

Result: exit 0, no output.

### Entitlements plist

Command: `plutil -lint companion/native-macos/Resources/TarsCompanionApp.entitlements`

Result:

```text
companion/native-macos/Resources/TarsCompanionApp.entitlements: OK
```

### Deterministic missing-identity preflight probe

Command: `SIGN_IDENTITY='Task09 Missing Identity Probe' bash scripts/release_menubar_app.sh`

Result: exit 2. The observed output was:

```text
Pré-voo de assinatura e notarização...
Erro: certificado Developer ID ausente: Task09 Missing Identity Probe
O Account Holder deve criá-lo em Xcode → Settings → Accounts → Manage Certificates → + → Developer ID Application.
```

The probe stopped after the identity gate; no package build, signing, DMG, or notary command was reached.

### Swift package tests

Command (from `companion/native-macos`): `swift test`

Result: exit 0; build completed successfully and the suite reported:

```text
Test Suite 'TarsNativeCompanionPackageTests.xctest' passed at 2026-08-29 12:40:49.671.
Executed 79 tests, with 0 failures (0 unexpected) in 0.054 (0.059) seconds
Test Suite 'All tests' passed at 2026-08-29 12:40:49.671.
Executed 79 tests, with 0 failures (0 unexpected) in 0.054 (0.060) seconds
```

### Static and repeat verification

The verifier reran the following checks successfully: `bash -n scripts/release_menubar_app.sh`, `plutil -lint companion/native-macos/Resources/TarsCompanionApp.entitlements`, the deterministic exit-2 missing-identity probe, and `swift test` (79/79 tests, 0 failures). It also ran:

Command: `shellcheck scripts/release_menubar_app.sh scripts/package_menubar_app.sh`

Result: exit 0.

### Standing project gates

The verifier also ran the standing regression suites:

- `/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/.venv/bin/python -m pytest backend/tests -q` → 361 passed in 6.48s.
- After `npm ci`, `npm test` in `frontend/` → 64 passed, 0 failed.

### Mutation-effective Invalid-result fixture

To exercise the failure branch without contacting Apple, the verifier prepended a temporary fake `xcrun` to `PATH`. The fixture made notary history succeed, made submit emit synthetic submission ID `00000000-0000-4000-8000-000000000009` with `status: Invalid` and exit 1, printed `TASK09_FAKE_NOTARY_LOG_FETCHED=00000000-0000-4000-8000-000000000009` from the log command, and would have failed if stapler had been called. The release script completed the real local build/sign/DMG path, fetched that exact synthetic log, exited 1, and did not call stapler. The temporary fixture was removed afterward; no Apple request occurred.

### Available identity and notary-profile preflight

The contractual Developer ID identity was confirmed available. The verifier then ran the real release script with its default identity and profile context:

Command: `bash scripts/release_menubar_app.sh`

Result: exit 3 at the notary-profile preflight, before the release build. The observed actionable output was:

```text
Pré-voo de assinatura e notarização...
Erro: o perfil de notarização "tars-notary" não está disponível.
O Account Holder deve criar o perfil com este comando exato:
xcrun notarytool store-credentials "tars-notary" --apple-id <APPLE_ID> --team-id 3FLG8W6B95 --password <app-specific-password>
```

### Local package and signing checks

The verifier separately ran `bash scripts/package_menubar_app.sh`; the release build and ad-hoc package completed successfully. Manual post-package checks using the installed contractual Developer ID identity also succeeded:

- Deepest-first executable signing and app-bundle signing with hardened runtime, timestamp, and `TarsCompanionApp.entitlements`.
- `codesign --verify --deep --strict --verbose=2 dist/TarsCompanion.app`.
- Code-sign readback showed `Identifier=com.ellaexecutivesearch.tarscompanion`, `TeamIdentifier=3FLG8W6B95`, and `flags=0x10000(runtime)`.
- Entitlement readback showed `com.apple.security.device.audio-input=true` on both the executable and app readbacks.
- UDZO creation and Developer ID signing of `dist/TarsCompanion.dmg`; strict DMG signature verification succeeded.
- DMG SHA-256: `76b00af1916b129e0a40e6940d1fb36a7925597d83a8323b79586ebe2e78cc4f`.
- A read-only DMG mount contained root `TarsCompanion.app`; the mounted app passed strict deep code-sign verification, and the DMG was detached afterward.

The pre-notarization Gatekeeper check was also run:

Command: `spctl -a -vvv -t install dist/TarsCompanion.app`

Result: exit 3 with `rejected`, `source=Unnotarized Developer ID`, and the expected origin. This is expected before notarization and causally isolates the absent notary profile/ticket.

## Real provider steps not run

Only these real provider-bound steps remain unexecuted:

- Successful `xcrun notarytool history --keychain-profile "tars-notary"` preflight.
- `xcrun notarytool submit "dist/TarsCompanion.dmg" --keychain-profile "tars-notary" --wait`, including a real `Invalid` submission-log retrieval if applicable (the mutation fixture exercised only a synthetic local submission/log).
- `xcrun stapler staple "dist/TarsCompanion.dmg"` and `xcrun stapler validate "dist/TarsCompanion.dmg"`.
- Post-notarization Gatekeeper acceptance from `spctl -a -vvv -t install dist/TarsCompanion.app`.

These remain blocked specifically because the owner-created `tars-notary` keychain profile is absent. No Apple submission occurred. The locally generated build and DMG are ignored artifacts and are not deliverables; only the four files listed above are deliverables. No Git command, credential value inspection, or provider action was performed by this writer node.
