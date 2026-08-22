# T.A.R.S. Native Companion Release Packaging Guide (macOS & Windows)

**Date:** 2026-08-21  
**Governing Architecture:** `docs/architecture/0003-native-capture-launch-boundary.md` (ADR 0003) & `docs/plans/2026-08-13-native-capture-launch-roadmap.md` (Gate G8)  
**Supported Platforms:**
- **macOS:** Universal 2 Mach-O (`arm64-apple-macosx` + `x86_64-apple-macosx`) for macOS 13.0 (Ventura) and later.
- **Windows:** Self-contained trimmed single-file executables (`win-x64` + `win-arm64`) for Windows 11 (Build 22H2+).

---

## 1. Quick Start Packaging Commands

To package all native companion binaries, generate checksums, and produce the unified `dist/manifest.json`:

```bash
./scripts/package_all_companions.sh
```

To run the automated release artifact verification suite:

```bash
.venv/bin/python scripts/test_packaged_artifacts.py
```

---

## 2. Release Artifact Catalog

| Platform | Architecture | Target Triples | Output File | Size | Packaging Strategy |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **macOS** | Universal 2 | `arm64`, `x86_64` | `dist/macos/tars-companion` | ~1.3 MB | `swift build -c release` + `lipo -create` + `strip -S` |
| **Windows** | x64 | `win-x64` | `dist/windows-x64/tars-companion.exe` | ~11.5 MB | `dotnet publish` (.NET 8 self-contained single-file trimmed) |
| **Windows** | ARM64 | `win-arm64` | `dist/windows-arm64/tars-companion.exe` | ~11.6 MB | `dotnet publish` (.NET 8 self-contained single-file trimmed) |

---

## 3. Packaging Scripts Reference

### A. macOS Universal Packaging (`scripts/package_macos_companion.sh`)
- Compiles both Apple Silicon (`arm64-apple-macosx`) and Intel (`x86_64-apple-macosx`) release slices.
- Uses `lipo -create` to merge both architectures into a single Universal binary.
- Strips debug symbols (`strip -S`) for minimal binary footprint.
- Performs ad-hoc code signing (`codesign --force --deep -s -`).
- Emits `dist/macos/SHA256SUMS`.

### B. Windows Self-Contained Packaging (`scripts/package_windows_companion.sh`)
**ATENÇÃO: a captura WASAPI ainda não está implementada — o exe só opera em `--simulate`.**
- Uses .NET 8 CLI with cross-target publishing:
  ```bash
  dotnet publish companion/native-windows/src/TarsCompanionCLI/TarsCompanionCLI.csproj \
    -c Release \
    -r win-x64 \
    --self-contained true \
    -p:PublishSingleFile=true \
    -p:PublishTrimmed=true \
    -p:EnableCompressionInSingleFile=true \
    -o dist/windows-x64
  ```
- Bundles the .NET runtime. **ATENÇÃO: a captura WASAPI ainda não está implementada — o exe só opera em `--simulate`.** No .NET SDK or runtime installation required on the recruiter's machine.
- Emits `dist/windows-x64/SHA256SUMS` and `dist/windows-arm64/SHA256SUMS`.

### C. Master Release Manifest (`dist/manifest.json`)
Emitted on each release build recording:
- `version`: Semantic release version.
- `git_commit`: Exact Git commit SHA.
- `built_at`: ISO 8601 UTC timestamp.
- Array of target artifacts with relative file path, byte length, and SHA-256 digest.

---

## 4. Integrity & Verification Procedures

Before delivering release binaries to recruiter pilot cohorts:
1. Verify SHA-256 checksums:
   ```bash
   cd dist/macos && shasum -a 256 -c SHA256SUMS
   cd ../windows-x64 && shasum -a 256 -c SHA256SUMS
   ```
2. Verify macOS architecture slice support:
   ```bash
   lipo -info dist/macos/tars-companion
   # Expected output: Architectures in the fat file: dist/macos/tars-companion are: x86_64 arm64
   ```
3. Test end-to-end streaming against local gateway:
   ```bash
   # macOS
   ./dist/macos/tars-companion --session-id pilot-test --gateway ws://127.0.0.1:8000/api/stream/native
   ```
