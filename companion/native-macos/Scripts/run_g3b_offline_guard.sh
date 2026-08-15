#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if rg -n --glob '*.swift' --glob '*.sh' \
  'URLSession|URLRequest|NWConnection|URL\(|WebSocket|Process\(|BlackHole|VB-CABLE|PyAudioWPatch|kAudioHardwarePropertyDefaultInputDevice|\.wav|\.mp3|\.m4a|\.caf|\.aiff|\.flac' \
  Sources Tests; then
  echo "forbidden live/network/audio artifact reference found" >&2
  exit 1
fi

if rg -n --glob '*.swift' 'AVAudioEngineMicrophoneSource|ScreenCaptureKitSystemAudioSource' Tests; then
  echo "offline tests may not invoke native adapters" >&2
  exit 1
fi

export SWIFT_DETERMINISTIC_HASHING=1
export NO_PROXY='*'
export no_proxy='*'
export HTTP_PROXY=''
export HTTPS_PROXY=''
export ALL_PROXY=''

run_tests() {
  if command -v sandbox-exec >/dev/null 2>&1; then
    sandbox-exec -f "$ROOT/Sandbox/g3b-offline.sb" swift test --disable-sandbox
  else
    swift test --disable-sandbox
  fi
}

run_tests
run_tests
echo "G3B offline guard passed twice"
