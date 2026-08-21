#!/usr/bin/env python3
"""
Test runner to validate packaged release artifacts for macOS and Windows.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"


def compute_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def test_artifacts():
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  T.A.R.S. Release Artifacts Validation Suite               ")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    manifest_file = DIST_DIR / "manifest.json"
    assert manifest_file.exists(), f"Missing manifest: {manifest_file}"

    with open(manifest_file, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert "version" in manifest
    assert "git_commit" in manifest
    assert "artifacts" in manifest
    assert len(manifest["artifacts"]) >= 3

    print(f"Manifest Version: {manifest['version']}")
    print(f"Git Commit:       {manifest['git_commit']}")
    print(f"Artifacts Count:  {len(manifest['artifacts'])}\n")

    for art in manifest["artifacts"]:
        rel_path = art["relative_path"]
        expected_sha = art["sha256"]
        expected_size = art["size_bytes"]
        platform = art["platform"]
        arch = art["architecture"]

        file_path = DIST_DIR / rel_path
        assert file_path.exists(), f"Artifact file does not exist: {file_path}"

        actual_size = file_path.stat().st_size
        assert actual_size == expected_size, f"Size mismatch for {rel_path}: expected {expected_size}, got {actual_size}"

        actual_sha = compute_sha256(file_path)
        assert actual_sha == expected_sha, f"SHA256 mismatch for {rel_path}: expected {expected_sha}, got {actual_sha}"

        # Verify platform-specific headers
        if platform == "macos":
            # Verify universal binary architectures using lipo -info or file
            lipo_res = subprocess.run(["lipo", "-info", str(file_path)], capture_output=True, text=True, check=True)
            assert "arm64" in lipo_res.stdout and "x86_64" in lipo_res.stdout, f"Missing universal slices: {lipo_res.stdout}"
            print(f"✓ [{platform.upper()} {arch}] {rel_path} ({actual_size:,} bytes) - Universal Mach-O Verified")
        elif platform == "windows":
            # Check PE header magic (MZ)
            with open(file_path, "rb") as f:
                magic = f.read(2)
            assert magic == b"MZ", f"Invalid PE header magic in {rel_path}"
            print(f"✓ [{platform.upper()} {arch}] {rel_path} ({actual_size:,} bytes) - PE Executable Verified")

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("✓ All release artifacts verified with 100% integrity!")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    try:
        test_artifacts()
    except Exception as e:
        print(f"✗ Artifact validation failed: {e}", file=sys.stderr)
        sys.exit(1)
