#!/usr/bin/env bash
set -euo pipefail

# Master packaging orchestrator for T.A.R.S. Native Companions (macOS + Windows)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  T.A.R.S. Master Release Packaging (macOS & Windows)       "
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 1. Package macOS
"${SCRIPT_DIR}/package_macos_companion.sh"

# 2. Package Windows
"${SCRIPT_DIR}/package_windows_companion.sh"

# 3. Generate Unified Release Manifest
echo "3. Generating unified release manifest..."
GIT_SHA="$(git rev-parse HEAD)"
BUILD_TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

MAC_SHA="$(shasum -a 256 "${REPO_ROOT}/dist/macos/tars-companion" | cut -d ' ' -f 1)"
MAC_SIZE="$(stat -f%z "${REPO_ROOT}/dist/macos/tars-companion" 2>/dev/null || stat -c%s "${REPO_ROOT}/dist/macos/tars-companion")"

WIN_X64_SHA="$(shasum -a 256 "${REPO_ROOT}/dist/windows-x64/tars-companion.exe" | cut -d ' ' -f 1)"
WIN_X64_SIZE="$(stat -f%z "${REPO_ROOT}/dist/windows-x64/tars-companion.exe" 2>/dev/null || stat -c%s "${REPO_ROOT}/dist/windows-x64/tars-companion.exe")"

WIN_ARM64_SHA="$(shasum -a 256 "${REPO_ROOT}/dist/windows-arm64/tars-companion.exe" | cut -d ' ' -f 1)"
WIN_ARM64_SIZE="$(stat -f%z "${REPO_ROOT}/dist/windows-arm64/tars-companion.exe" 2>/dev/null || stat -c%s "${REPO_ROOT}/dist/windows-arm64/tars-companion.exe")"

cat <<EOF > "${REPO_ROOT}/dist/manifest.json"
{
  "version": "1.0.0",
  "git_commit": "${GIT_SHA}",
  "built_at": "${BUILD_TIMESTAMP}",
  "artifacts": [
    {
      "platform": "macos",
      "architecture": "universal2",
      "targets": ["arm64-apple-macosx", "x86_64-apple-macosx"],
      "relative_path": "macos/tars-companion",
      "size_bytes": ${MAC_SIZE},
      "sha256": "${MAC_SHA}"
    },
    {
      "platform": "windows",
      "architecture": "x64",
      "targets": ["win-x64"],
      "relative_path": "windows-x64/tars-companion.exe",
      "size_bytes": ${WIN_X64_SIZE},
      "sha256": "${WIN_X64_SHA}"
    },
    {
      "platform": "windows",
      "architecture": "arm64",
      "targets": ["win-arm64"],
      "relative_path": "windows-arm64/tars-companion.exe",
      "size_bytes": ${WIN_ARM64_SIZE},
      "sha256": "${WIN_ARM64_SHA}"
    }
  ]
}
EOF

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✓ Master Release Packaging Complete!"
echo "Manifest: ${REPO_ROOT}/dist/manifest.json"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cat "${REPO_ROOT}/dist/manifest.json"
echo ""
