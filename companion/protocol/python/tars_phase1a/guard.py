"""Fail-closed process guards for the Phase 1A offline harness."""

import importlib.abc
import os
import sys
from typing import Mapping, Optional, Sequence


class GuardViolation(RuntimeError):
    """Raised when Phase 1A crosses its approved execution boundary."""


_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "azure",
        "backend",
        "boto3",
        "ctypes",
        "firebase_admin",
        "google",
        "googleapiclient",
        "grpc",
        "httpx",
        "requests",
        "urllib3",
    }
)

_FORBIDDEN_ENV_EXACT = frozenset(
    {
        "ALL_PROXY",
        "CLOUDSDK_AUTH_ACCESS_TOKEN",
        "CLOUDSDK_CORE_PROJECT",
        "CLOUDSDK_METRICS_ENVIRONMENT",
        "FIREBASE_CONFIG",
        "GCLOUD_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    }
)

_FORBIDDEN_ENV_PREFIXES = (
    "AWS_",
    "AZURE_",
    "CLOUDSDK_",
    "FIREBASE_",
    "FIRESTORE_",
    "GCE_",
    "GCP_",
    "GCLOUD_",
    "GOOGLE_",
)

_FORBIDDEN_ENV_MARKERS = (
    "API_KEY",
    "AUTHORIZATION",
    "CREDENTIAL",
    "SECRET",
    "TOKEN",
)

_FORBIDDEN_ENV_SUFFIXES = (
    "_API_URL",
    "_BASE_URL",
    "_ENDPOINT",
    "_HOST",
    "_URL",
)

_AUDIT_MUTATIONS = frozenset(
    {
        "os.chmod",
        "os.chown",
        "os.fork",
        "os.forkpty",
        "os.link",
        "os.mkdir",
        "os.posix_spawn",
        "os.putenv",
        "os.remove",
        "os.rename",
        "os.replace",
        "os.rmdir",
        "os.symlink",
        "os.system",
        "os.unsetenv",
        "pty.spawn",
        "subprocess.Popen",
    }
)

_WRITE_FLAGS = (
    os.O_WRONLY
    | os.O_RDWR
    | os.O_APPEND
    | os.O_CREAT
    | os.O_TRUNC
)

_guards_active = False


def forbidden_environment_keys(environment: Mapping[str, str]) -> Sequence[str]:
    """Return sorted environment keys that violate the offline boundary."""

    blocked = []
    for raw_key in environment:
        key = raw_key.upper()
        if key in _FORBIDDEN_ENV_EXACT:
            blocked.append(raw_key)
            continue
        if key.startswith(_FORBIDDEN_ENV_PREFIXES):
            blocked.append(raw_key)
            continue
        if key.endswith(_FORBIDDEN_ENV_SUFFIXES):
            blocked.append(raw_key)
            continue
        if any(marker in key for marker in _FORBIDDEN_ENV_MARKERS):
            blocked.append(raw_key)
    return tuple(sorted(blocked))


def validate_environment(environment: Optional[Mapping[str, str]] = None) -> None:
    """Require the explicit offline marker and reject credential-like state."""

    selected = os.environ if environment is None else environment
    if selected.get("TARS_PHASE1A_MODE") != "offline":
        raise GuardViolation("TARS_PHASE1A_MODE must be exactly 'offline'")

    blocked = forbidden_environment_keys(selected)
    if blocked:
        raise GuardViolation(
            "forbidden Phase 1A environment keys: " + ", ".join(blocked)
        )


class _ForbiddenImportFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # type: ignore[no-untyped-def]
        del path, target
        root = fullname.partition(".")[0]
        if root in _FORBIDDEN_IMPORT_ROOTS:
            raise GuardViolation("forbidden Phase 1A import: " + fullname)
        return None


def _audit_guard(event: str, args: tuple) -> None:
    if event == "open":
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        if isinstance(mode, str) and any(marker in mode for marker in "wax+"):
            raise GuardViolation("filesystem write blocked in Phase 1A")
        if isinstance(flags, int) and flags & _WRITE_FLAGS:
            raise GuardViolation("filesystem write blocked in Phase 1A")
        return

    if event in _AUDIT_MUTATIONS or event.startswith("os.exec"):
        raise GuardViolation("process or filesystem mutation blocked: " + event)


def activate_guards() -> None:
    """Activate environment, import, write, and subprocess guards once."""

    global _guards_active
    if _guards_active:
        return

    validate_environment()
    sys.meta_path.insert(0, _ForbiddenImportFinder())
    sys.addaudithook(_audit_guard)
    _guards_active = True
