#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
G3A_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
REPO_ROOT=$(git -C "$G3A_ROOT" rev-parse --show-toplevel)
BASE=65051a8863ec9b3430318b63b091369d668cd1b0

fail() { echo "G3A artifact scan failed: $1" >&2; exit 1; }

git -C "$REPO_ROOT" cat-file -e "$BASE^{commit}" || fail "base unavailable"
changed_paths=$( {
  git -C "$REPO_ROOT" diff --name-only "$BASE"..HEAD
  git -C "$REPO_ROOT" diff --name-only
  git -C "$REPO_ROOT" ls-files --others --exclude-standard
} | sort -u )
printf '%s\n' "$changed_paths" | while IFS= read -r path; do
  [ -n "$path" ] || continue
  case "$path" in
    backend/g3a_gateway/*|backend/tests/g3a_gateway/*|docs/reviews/2026-08-14-g3a-source-offline-evidence.md|docs/reviews/2026-08-14-g3a-source-owner-attestation.md) ;;
    *) fail "out-of-scope changed path: $path" ;;
  esac
done

if grep -R -n -E \
  '^[[:space:]]*(from|import)[[:space:]]+(fastapi|google|firebase_admin|grpc|httpx|requests|urllib3|boto3|azure|socket|ssl|sounddevice|numpy)' \
  "$G3A_ROOT"/*.py "$G3A_ROOT"/../tests/g3a_gateway/*.py >/dev/null 2>&1; then
  fail "forbidden import found"
fi

artifact_paths=$(find "$G3A_ROOT" "$G3A_ROOT/../tests/g3a_gateway" \( \
  -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache \) -o \
  -type f \( -name '*.pyc' -o -name '*.wav' -o -name '*.flac' -o -name '*.mp3' -o -name '*.m4a' -o -name '*.pcm' -o -name '*.raw' \) -o \
  -type l \
\) -print)
[ -z "$artifact_paths" ] || { printf '%s\n' "$artifact_paths" >&2; fail "generated or payload artifact found"; }

printf '%s\n' '{"artifacts":0,"forbiddenImports":0,"outOfScopePaths":0,"phase":"3A-artifact-scan","successful":true}'
