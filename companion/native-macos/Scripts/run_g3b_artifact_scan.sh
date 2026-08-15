#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/../.."

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
