#!/usr/bin/env bash
set -euo pipefail

# Package Windows Native Companion (.NET 8 Self-Contained Single File)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WIN_PROJ="${REPO_ROOT}/companion/native-windows/src/TarsCompanionCLI/TarsCompanionCLI.csproj"
OUT_BASE="${REPO_ROOT}/dist"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  T.A.R.S. Windows Native Companion Packaging (.NET 8)      "
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 1. Windows x64
echo "1. Publishing Windows x64 self-contained single file..."
OUT_X64="${OUT_BASE}/windows-x64"
mkdir -p "${OUT_X64}"
dotnet publish "${WIN_PROJ}" \
  -c Release \
  -r win-x64 \
  --self-contained true \
  -p:PublishSingleFile=true \
  -p:PublishTrimmed=true \
  -p:EnableCompressionInSingleFile=true \
  -o "${OUT_X64}"

(cd "${OUT_X64}" && shasum -a 256 "tars-companion.exe" > "SHA256SUMS")

# 2. Windows ARM64
echo "2. Publishing Windows ARM64 self-contained single file..."
OUT_ARM64="${OUT_BASE}/windows-arm64"
mkdir -p "${OUT_ARM64}"
dotnet publish "${WIN_PROJ}" \
  -c Release \
  -r win-arm64 \
  --self-contained true \
  -p:PublishSingleFile=true \
  -p:PublishTrimmed=true \
  -p:EnableCompressionInSingleFile=true \
  -o "${OUT_ARM64}"

(cd "${OUT_ARM64}" && shasum -a 256 "tars-companion.exe" > "SHA256SUMS")

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✓ Windows binaries packaged successfully:"
echo "Windows x64:   ${OUT_X64}/tars-companion.exe ($(du -h "${OUT_X64}/tars-companion.exe" | cut -f1))"
echo "Checksum:      $(cat "${OUT_X64}/SHA256SUMS")"
echo "Windows ARM64: ${OUT_ARM64}/tars-companion.exe ($(du -h "${OUT_ARM64}/tars-companion.exe" | cut -f1))"
echo "Checksum:      $(cat "${OUT_ARM64}/SHA256SUMS")"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
