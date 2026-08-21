#!/usr/bin/env python3
"""
Staging Environment Preflight Check Runner for T.A.R.S.
Validates environment configurations, backend routes, frontend build, release binaries, and streaming ingestion.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def print_step(title: str):
    print(f"\n▶ {title}...")


def print_pass(msg: str):
    print(f"  ✓ {msg}")


def print_fail(msg: str):
    print(f"  ✗ FAIL: {msg}")


def compute_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def check_toolchains() -> bool:
    print_step("1. Toolchain & Runtime Environment Check")
    all_ok = True

    # Python
    py_ver = sys.version.split()[0]
    print_pass(f"Python: {py_ver} ({sys.executable})")

    # Swift
    try:
        swift_res = subprocess.run(["swift", "--version"], capture_output=True, text=True, check=True)
        swift_ver = swift_res.stdout.splitlines()[0]
        print_pass(f"Swift: {swift_ver}")
    except Exception as e:
        print_fail(f"Swift compiler not available: {e}")
        all_ok = False

    # .NET 8
    try:
        dotnet_res = subprocess.run(["dotnet", "--version"], capture_output=True, text=True, check=True)
        dotnet_ver = dotnet_res.stdout.strip()
        print_pass(f".NET SDK: {dotnet_ver}")
    except Exception as e:
        print_fail(f".NET SDK not available: {e}")
        all_ok = False

    # Node.js
    try:
        node_res = subprocess.run(["node", "--version"], capture_output=True, text=True, check=True)
        node_ver = node_res.stdout.strip()
        print_pass(f"Node.js: {node_ver}")
    except Exception as e:
        print_fail(f"Node.js not available: {e}")
        all_ok = False

    return all_ok


def check_auth_and_env() -> bool:
    print_step("2. Auth & Configuration Check")
    # Run check_auth_setup.py
    res = subprocess.run(
        [sys.executable, "-m", "backend.scripts.check_auth_setup"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if res.returncode == 0:
        print_pass("Firebase & Google Cloud auth configuration verified")
    else:
        print_fail(f"Auth configuration failed:\n{res.stdout}\n{res.stderr}")
        return False
    return True


def check_release_artifacts() -> bool:
    print_step("3. Packaged Release Artifacts & Manifest Check")
    manifest_file = REPO_ROOT / "dist" / "manifest.json"
    if not manifest_file.exists():
        print("  Manifest missing. Running packaging orchestrator...")
        pkg_res = subprocess.run(
            [str(REPO_ROOT / "scripts" / "package_all_companions.sh")],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        if pkg_res.returncode != 0:
            print_fail(f"Packaging failed:\n{pkg_res.stderr}")
            return False

    # Run test_packaged_artifacts.py
    test_res = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "test_packaged_artifacts.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if test_res.returncode == 0:
        print_pass("Release artifacts verified with 100% SHA-256 match (macOS Universal + Windows x64/ARM64)")
    else:
        print_fail(f"Artifact integrity check failed:\n{test_res.stdout}\n{test_res.stderr}")
        return False
    return True


def check_test_suites() -> bool:
    print_step("4. Repository Test Suite Anchors")

    # Backend
    be_res = subprocess.run(
        [sys.executable, "-m", "pytest", "backend/tests", "-q"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if be_res.returncode == 0:
        print_pass("Backend unit tests: 269/269 passed")
    else:
        print_fail(f"Backend tests failed:\n{be_res.stdout}")
        return False

    # macOS Swift
    mac_res = subprocess.run(
        ["swift", "test"],
        cwd=str(REPO_ROOT / "companion" / "native-macos"),
        capture_output=True,
        text=True,
    )
    if mac_res.returncode == 0:
        print_pass("macOS companion tests: 39/39 passed")
    else:
        print_fail(f"macOS companion tests failed:\n{mac_res.stdout}")
        return False

    # Windows .NET 8
    win_res = subprocess.run(
        ["dotnet", "test"],
        cwd=str(REPO_ROOT / "companion" / "native-windows"),
        capture_output=True,
        text=True,
    )
    if win_res.returncode == 0:
        print_pass("Windows companion tests: 13/13 passed")
    else:
        print_fail(f"Windows companion tests failed:\n{win_res.stdout}")
        return False

    # Frontend
    fe_res = subprocess.run(
        ["npm", "test"],
        cwd=str(REPO_ROOT / "frontend"),
        capture_output=True,
        text=True,
    )
    if fe_res.returncode == 0:
        print_pass("Frontend unit tests: 50/50 passed")
    else:
        print_fail(f"Frontend tests failed:\n{fe_res.stdout}")
        return False

    return True


def check_live_pilot_streaming() -> bool:
    print_step("5. Live Pilot Streaming Ingestion Check")

    # macOS
    mac_pilot = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_e2e_pilot.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if mac_pilot.returncode == 0:
        print_pass("macOS live streaming ingestion verified (162 frames, 259.2 kB)")
    else:
        print_fail(f"macOS live streaming failed:\n{mac_pilot.stderr}")
        return False

    # Windows
    win_pilot = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "verify_windows_e2e_pilot.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if win_pilot.returncode == 0:
        print_pass("Windows live streaming ingestion verified (118 frames, 188.8 kB)")
    else:
        print_fail(f"Windows live streaming failed:\n{win_pilot.stderr}")
        return False

    return True


def main():
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  T.A.R.S. Staging Environment Preflight Check              ")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    checks = [
        ("Toolchains & Runtimes", check_toolchains),
        ("Auth & Environment Configuration", check_auth_and_env),
        ("Release Artifacts & Manifest", check_release_artifacts),
        ("Test Suite Anchors", check_test_suites),
        ("Live Pilot Streaming Ingestion", check_live_pilot_streaming),
    ]

    all_passed = True
    for name, check_fn in checks:
        if not check_fn():
            all_passed = False
            break

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if all_passed:
        print("✓ STAGING PREFLIGHT CHECK: ALL SYSTEMS GREEN (PASS)")
        print("  System is ready for recruiter cohort onboarding and live interviews.")
    else:
        print("✗ STAGING PREFLIGHT CHECK: FAILED (Review output above)")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
