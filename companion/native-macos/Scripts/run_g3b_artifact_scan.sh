#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/../.."

BASELINE='47fc798885be4d09d983d16ddc14c26a1c90d366'
if ! git cat-file -e "$BASELINE^{commit}" 2>/dev/null; then
  echo "required G3B baseline is unavailable" >&2
  exit 1
fi

is_allowed_path() {
  case "$1" in
    companion/native-macos/Package.swift|\
    companion/native-macos/Sources/TarsNativeCompanion/CompanionContracts.swift|\
    companion/native-macos/Sources/TarsNativeCompanion/SourceIdentity.swift|\
    companion/native-macos/Sources/TarsNativeCompanion/FrameReducer.swift|\
    companion/native-macos/Sources/TarsNativeCompanion/CustodyRing.swift|\
    companion/native-macos/Sources/TarsNativeCompanion/LifecycleCoordinator.swift|\
    companion/native-macos/Sources/TarsNativeCompanion/DeletionCoordinator.swift|\
    companion/native-macos/Sources/TarsNativeCompanion/Diagnostics.swift|\
    companion/native-macos/Sources/TarsNativeCompanion/CaptureSource.swift|\
    companion/native-macos/Sources/TarsNativeCompanion/AVAudioEngineMicrophoneSource.swift|\
    companion/native-macos/Sources/TarsNativeCompanion/ScreenCaptureKitSystemAudioSource.swift|\
    companion/native-macos/Sources/TarsNativeCompanion/GeneratedFixtureSource.swift|\
    companion/native-macos/Sources/TarsNativeCompanion/OfflineCompanionSimulator.swift|\
    companion/native-macos/Tests/TarsNativeCompanionTests/CompanionContractsTests.swift|\
    companion/native-macos/Tests/TarsNativeCompanionTests/SourceIdentityTests.swift|\
    companion/native-macos/Tests/TarsNativeCompanionTests/FrameReducerTests.swift|\
    companion/native-macos/Tests/TarsNativeCompanionTests/CustodyRingTests.swift|\
    companion/native-macos/Tests/TarsNativeCompanionTests/LifecycleTests.swift|\
    companion/native-macos/Tests/TarsNativeCompanionTests/DeletionTests.swift|\
    companion/native-macos/Tests/TarsNativeCompanionTests/RecoveryAndRaceTests.swift|\
    companion/native-macos/Tests/TarsNativeCompanionTests/GeneratedFixtureTests.swift|\
    companion/native-macos/Scripts/run_g3b_offline_guard.sh|\
    companion/native-macos/Scripts/run_g3b_artifact_scan.sh|\
    companion/native-macos/Sandbox/g3b-offline.sb|\
    docs/reviews/2026-08-15-g3b-source-offline-evidence.md|\
    docs/reviews/2026-08-15-g3b-source-owner-attestation.md)
      return 0 ;;
    *) return 1 ;;
  esac
}

committed_paths="$(git diff --name-only "$BASELINE..HEAD")"
if [ -n "$committed_paths" ]; then
  while IFS= read -r path; do
    if ! is_allowed_path "$path"; then
      echo "committed path is outside the approved G3B source map: $path" >&2
      exit 1
    fi
  done <<< "$committed_paths"
fi

evidence_doc='docs/reviews/2026-08-15-g3b-source-offline-evidence.md'
source_commit="$(sed -n 's/^- source implementation commit: `\([^`]*\)`;$/\1/p' "$evidence_doc")"
source_tree="$(sed -n 's/^- source implementation tree: `\([^`]*\)`;$/\1/p' "$evidence_doc")"
if [ -z "$source_commit" ] || [ -z "$source_tree" ] || ! git cat-file -e "$source_commit^{commit}" 2>/dev/null; then
  echo "evidence document does not bind an existing source commit" >&2
  exit 1
fi
actual_source_tree="$(git rev-parse "$source_commit^{tree}")"
if [ "$actual_source_tree" != "$source_tree" ]; then
  echo "evidence source tree binding mismatch: $actual_source_tree != $source_tree" >&2
  exit 1
fi

allowed='^(companion/native-macos/(Package.swift|Sources/TarsNativeCompanion/[^/]+\.swift|Tests/TarsNativeCompanionTests/[^/]+\.swift|Scripts/run_g3b_(offline_guard|artifact_scan)\.sh|Sandbox/g3b-offline\.sb)|docs/reviews/2026-08-15-g3b-source-(offline-evidence|owner-attestation)\.md)$'
paths="$(git status --short --untracked-files=all | sed -E 's/^.. //' | sed '/^$/d')"
if [ -n "$paths" ]; then
  while IFS= read -r path; do
    if [[ "$path" == companion/native-macos/.build/* ]]; then
      continue
    fi
    if ! printf '%s\n' "$path" | rg -q "$allowed"; then
      echo "out-of-scope path: $path" >&2
      exit 1
    fi
  done <<< "$paths"
fi

if find companion/native-macos -type f \( -name '*.wav' -o -name '*.mp3' -o -name '*.m4a' -o -name '*.caf' -o -name '*.aiff' -o -name '*.flac' -o -name '*.dmg' -o -name '*.app' -o -name '*.ipa' -o -name '*.pem' -o -name '*.p12' -o -name '*.key' \) -print -quit | rg -q .; then
  echo "raw-audio, bundle, or credential artifact found" >&2
  exit 1
fi

if rg -n --hidden --glob '!companion/native-macos/.build/**' --glob '!*.md' --glob '!companion/native-macos/Scripts/**' \
  'AIza[0-9A-Za-z_-]{20,}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|gh[pousr]_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+|https?://|URLSession|NWConnection|BlackHole|VB-CABLE|PyAudioWPatch|kAudioHardwarePropertyDefaultInputDevice' \
  companion/native-macos; then
  echo "credential, endpoint, virtual-route, or network reference found" >&2
  exit 1
fi

echo "G3B artifact scan passed"
