#!/usr/bin/env bash
set -euo pipefail

# Package macOS Universal Binary for T.A.R.S. Native Companion
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MAC_DIR="${REPO_ROOT}/companion/native-macos"
OUT_DIR="${REPO_ROOT}/dist/macos"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  T.A.R.S. macOS Native Companion Packaging (Universal)    "
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

mkdir -p "${OUT_DIR}"

echo "1. Building Apple Silicon (arm64) release slice..."
(cd "${MAC_DIR}" && swift build -c release --triple arm64-apple-macosx)

echo "2. Building Intel (x86_64) release slice..."
(cd "${MAC_DIR}" && swift build -c release --triple x86_64-apple-macosx)

ARM64_BIN="${MAC_DIR}/.build/arm64-apple-macosx/release/tars-companion"
X86_64_BIN="${MAC_DIR}/.build/x86_64-apple-macosx/release/tars-companion"
TARGET_BIN="${OUT_DIR}/tars-companion"

echo "3. Merging slices into Universal Mach-O binary with lipo..."
lipo -create "${ARM64_BIN}" "${X86_64_BIN}" -output "${TARGET_BIN}"
chmod +x "${TARGET_BIN}"

echo "4. Stripping debug symbols..."
strip -S "${TARGET_BIN}" || true

echo "5. Ad-hoc codesigning executable..."
codesign --force --deep -s - "${TARGET_BIN}" || true

echo "6. Generating SHA256 checksum..."
(cd "${OUT_DIR}" && shasum -a 256 "tars-companion" > "SHA256SUMS")

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✓ macOS Universal binary packaged successfully:"
file "${TARGET_BIN}"
echo "Location: ${TARGET_BIN}"
echo "Checksum: $(cat "${OUT_DIR}/SHA256SUMS")"
echo "Size:     $(du -h "${TARGET_BIN}" | cut -f1)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
