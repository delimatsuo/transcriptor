#!/usr/bin/env bash
set -euo pipefail

# Package macOS Menu Bar App Bundle for T.A.R.S. Native Companion
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MAC_DIR="${REPO_ROOT}/companion/native-macos"
APP_BUNDLE="${REPO_ROOT}/dist/TarsCompanion.app"
CONTENTS_DIR="${APP_BUNDLE}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
INFO_PLIST="${MAC_DIR}/Resources/TarsCompanionApp-Info.plist"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  T.A.R.S. macOS Companion App Bundle Packaging (.app)     "
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo "1. Building TarsCompanionApp release binary..."
(cd "${MAC_DIR}" && swift build -c release --product TarsCompanionApp)

RELEASE_BIN="${MAC_DIR}/.build/release/TarsCompanionApp"

echo "2. Assembling App Bundle structure..."
rm -rf "${APP_BUNDLE}"
mkdir -p "${MACOS_DIR}" "${RESOURCES_DIR}"

echo "3. Copying binary, Info.plist, and PkgInfo..."
cp "${RELEASE_BIN}" "${MACOS_DIR}/TarsCompanionApp"
chmod +x "${MACOS_DIR}/TarsCompanionApp"
cp "${INFO_PLIST}" "${CONTENTS_DIR}/Info.plist"
printf "APPL????" > "${CONTENTS_DIR}/PkgInfo"

echo "4. Ad-hoc codesigning app bundle..."
# Assinatura ad-hoc somente para desenvolvimento local; scripts/release_menubar_app.sh produz o build distribuível.
codesign --force --deep -s - "${APP_BUNDLE}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✓ macOS Menu Bar App packaged successfully:"
echo "Location: ${APP_BUNDLE}"
echo "Codesign verification:"
codesign -dv --verbose=2 "${APP_BUNDLE}" 2>&1
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
