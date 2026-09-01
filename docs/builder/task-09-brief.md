# Task 09 — Developer ID signing, hardened runtime, notarization, .dmg

Read `docs/builder/README.md` first. Repo: `"/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"` (quote — space in path).

## Why

`scripts/package_menubar_app.sh` currently ad-hoc signs the bundle (`codesign -s -`). An ad-hoc identity is unstable across rebuilds, so macOS will not durably keep the app's Screen Recording permission — proven on 2026-08-23, when a granted permission stopped applying after a rebuild and the app could not capture at all. A Developer ID identity plus notarization fixes that and is also what lets a recruiter download and open the app without Gatekeeper blocking it.

## Team / identity facts (do not guess these)

- Signing identity string: `Developer ID Application: Travel Advisory LLC (3FLG8W6B95)`
- Team ID: `3FLG8W6B95`
- Notarization keychain profile name to use: `tars-notary`
- Bundle ID: `com.ellaexecutivesearch.tarscompanion`

**The certificate and the `tars-notary` profile are created by the owner, not by you.** If they are absent, your script must fail with a clear, actionable message rather than falling back to ad-hoc signing. You will likely NOT be able to run a real signing/notarization end to end — that is expected. Verify everything you can (script logic, missing-credential paths, help output) and say plainly in your report what you could not execute.

## File plan

**Do not create** `companion/native-macos/Resources/TarsCompanionApp.entitlements` in Task 09 and do not add `com.apple.security.device.audio-input` to this signing-only slice. The approved W3 contract requires the CoreAudio-taps entitlement decision to be made by testing, not assumption, and the taps engine does not exist until Task 10. Task 09 therefore signs with hardened runtime and no audio entitlement by default; passing its signing/notarization checks is not audio-capture readiness.

Task 10 owns the later empirical decision under a separate fresh authorization envelope for real audio/device use. On the same supported Mac, permission state, exact source tree, build inputs, signing identity, and audible system-audio fixture, it must compare a hardened-runtime signed taps build without the entitlement against a candidate signed with `com.apple.security.device.audio-input`. Record the exact commit, macOS version, commands, redacted signing identity/team, permission state, fixture, non-zero-frame assertions, and teardown result. Add the entitlement to the release path only if that causal comparison proves it necessary; otherwise omit it. A launch-ready verdict requires the chosen signed build to prove both Process Tap system audio and microphone capture. This brief grants no real-audio, device, permission, signing, or Apple-account authority.

**Create** `scripts/release_menubar_app.sh` (bash, `set -euo pipefail`, `chmod +x`). It must:
1. Before invoking `security`, `xcrun`, the packaging script, `codesign`, `hdiutil`, `spctl`, or any other external command, require the sole positional mode `--live-release`. With no argument, an unknown argument, or extra arguments, print that a fresh owner signing/notarization authorization is required and exit 64 without inspecting the keychain/profile or changing files. After that guard, accept optional env overrides with these defaults: `SIGN_IDENTITY="Developer ID Application: Travel Advisory LLC (3FLG8W6B95)"`, `NOTARY_PROFILE="tars-notary"`, `APP_NAME="TarsCompanion"`.
2. **Preflight, with clear pt-BR errors and non-zero exits:**
   - `security find-identity -v -p codesigning | grep -q "$SIGN_IDENTITY"` — if absent, print that the Developer ID certificate is missing and that the Account Holder must create it (Xcode → Settings → Accounts → Manage Certificates → + → Developer ID Application), then exit 2.
   - `xcrun notarytool history --keychain-profile "$NOTARY_PROFILE"` succeeding — if it fails, print the exact command the owner must run to create the profile (`xcrun notarytool store-credentials "$NOTARY_PROFILE" --apple-id "<APPLE_ID>" --team-id 3FLG8W6B95 --password "<app-specific-password>"`) and exit 3.
3. Build the release app bundle by reusing the existing packaging: call `bash "${REPO_ROOT}/scripts/package_menubar_app.sh"` (it produces `dist/TarsCompanion.app` and ad-hoc signs it — you will re-sign over that).
4. **Re-sign properly**, deepest-first: sign the executable inside `Contents/MacOS/`, then the bundle. For each target, set `TARGET_PATH` to its exact path and run:
   `codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$TARGET_PATH"`
   Do not pass `--entitlements` in Task 09. Then verify with `codesign --verify --deep --strict --verbose=2`, print `codesign -dv --verbose=4` output (must show `TeamIdentifier=3FLG8W6B95` and `flags=...runtime`), and inspect `codesign -d --entitlements :-` output to confirm the app does not contain `com.apple.security.device.audio-input`.
5. **Build a .dmg**: `hdiutil create -volname "$APP_NAME" -srcfolder dist/TarsCompanion.app -ov -format UDZO "dist/${APP_NAME}.dmg"`, then sign the dmg with the same identity (`codesign --force --timestamp --sign "$SIGN_IDENTITY" "dist/${APP_NAME}.dmg"`).
6. **Notarize and staple**: `xcrun notarytool submit "dist/${APP_NAME}.dmg" --keychain-profile "$NOTARY_PROFILE" --wait`, then `xcrun stapler staple "dist/${APP_NAME}.dmg"` and `xcrun stapler validate "dist/${APP_NAME}.dmg"`. Capture the returned submission ID in `SUBMISSION_ID`. If notarytool reports `Invalid`, fetch and print the log (`xcrun notarytool log "$SUBMISSION_ID" --keychain-profile "$NOTARY_PROFILE"`) before exiting non-zero.
7. Print a final summary: dmg path, its SHA-256, and the `spctl -a -vvv -t exec dist/TarsCompanion.app` result.

**Create** `scripts/tests/release_menubar_app_test.sh` (bash, `set -euo pipefail`). It must create its isolated tree with `mktemp -d "${TMPDIR%/}/transcriptor-task09-release-test.XXXXXX"`, exercise the real release script through a PATH-shim directory containing synthetic `security`, `xcrun`, `codesign`, `hdiutil`, and `spctl` executables that append every invocation to a temporary log, and remove only that exact temporary tree in an exit trap without `rm -rf`. Use only synthetic identity/profile values. Prove all of these cases:

1. No arguments and malformed/extra arguments exit 64, print the authorization message, make zero shim calls, and create no artifact.
2. `--live-release` with the fake certificate absent exits 2 with the actionable certificate message; no `xcrun`, packaging, signing, DMG, stapling, or assessment step occurs.
3. `--live-release` with the fake certificate present and fake `notarytool history` failing exits 3 with the selected-profile recovery command and quoted synthetic credential placeholders; no packaging, signing, submission, stapling, or assessment step occurs.
4. The test fails if any protected command (`security`, `xcrun`, `codesign`, `hdiutil`, or `spctl`) resolves outside the shim directory, if any unexpected protected shim is invoked, if `dist/` changes, or if either failure path continues after its protected preflight.

The harness must not invoke the real release script with host commands or inspect any machine-local credential/config state.

**Modify** `scripts/package_menubar_app.sh`: add one comment line above its `codesign --force --deep -s -` call stating that ad-hoc signing is for local development only and that `scripts/release_menubar_app.sh` produces the distributable build. Change nothing else.

## Constraints

- Do NOT modify any Swift source, the Info.plist, or anything under `backend/`, `frontend/`, `docs/` (other than your report).
- Do NOT create an entitlements file or add an audio entitlement in Task 09. That least-privilege decision remains blocked on Task 10's separately authorized empirical signed-capture gate.
- Never invent or hardcode an Apple ID, password, or app-specific password anywhere in the repo.
- Builder verification must not inspect the real keychain/notarization profile, invoke the packaging script, sign, notarize, staple, assess, or contact Apple. Only the synthetic PATH-shim harness may execute release failure paths.
- `dist/` is gitignored — artifacts are not deliverables; the release and packaging scripts are.

## Verification (do what you can; report the rest honestly)

```
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && bash -n scripts/release_menubar_app.sh    # syntax check
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && bash -n scripts/tests/release_menubar_app_test.sh
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && TMPDIR="${TMPDIR:-/private/tmp}" bash scripts/tests/release_menubar_app_test.sh
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && test ! -e companion/native-macos/Resources/TarsCompanionApp.entitlements
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && ! rg -n -- 'codesign .*--(force|sign).*--entitlements|codesign .*--entitlements.*--(force|sign)' scripts/release_menubar_app.sh
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/companion/native-macos" && swift test          # unchanged, 0 failures
```

### Owner-only live gate — not builder verification

Do not run the real release script merely because the certificate or profile exists. A fresh owner authorization must name the exact commit, signing identity/team, notarization profile, intended artifact, allowed Apple-side submission, and evidence/rollback ceiling. Only inside that envelope may the owner run `bash scripts/release_menubar_app.sh --live-release` and record the real notarization result. That result qualifies signing mechanics only; do not claim microphone, Process Tap, or end-to-end audio readiness until Task 10's separately authorized empirical gate passes.

## Report

`docs/builder/task-09-report.md`: files created, the synthetic preflight outputs, confirmation that no real keychain/profile or Apple operation was attempted, confirmation that Task 09 added no audio entitlement, and an explicit list of owner-only steps you did not execute. Record both the live release and Task 10 signed-capture matrix as deferred rather than passed.
