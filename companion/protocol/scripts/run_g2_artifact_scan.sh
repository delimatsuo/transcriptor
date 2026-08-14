#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROTOCOL_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
REPO_ROOT=$(git -C "$PROTOCOL_ROOT" rev-parse --show-toplevel)
G2_AUTH_BASE=8398fa8b345e326320e54d2a598977e47ee67fa7

fail() { echo "G2-A artifact scan failed: $1" >&2; exit 1; }

git -C "$REPO_ROOT" cat-file -e "$G2_AUTH_BASE^{commit}" || fail "authorization baseline unavailable"
git -C "$REPO_ROOT" diff --check "$G2_AUTH_BASE"..HEAD || fail "committed-range whitespace error"
git -C "$REPO_ROOT" diff --check || fail "working-tree whitespace error"
changed_paths=$( {
  git -C "$REPO_ROOT" diff --name-only "$G2_AUTH_BASE"..HEAD
  git -C "$REPO_ROOT" diff --name-only
  git -C "$REPO_ROOT" ls-files --others --exclude-standard
} | sort -u )
printf '%s\n' "$changed_paths" | while IFS= read -r path; do
  [ -n "$path" ] || continue
  case "$path" in
    companion/protocol/*|docs/plans/2026-08-13-protocol-closure-entry-plan.md|docs/reviews/2026-08-14-g2a-source-offline-evidence.md) ;;
    *) fail "out-of-scope changed path: $path" ;;
  esac
done

if git -C "$REPO_ROOT" grep -n -E \
  '^[[:space:]]*(from|import)[[:space:]]+(backend|google|firebase_admin|grpc|httpx|requests|urllib3|boto3|azure|socket|ssl)([.[:space:]]|$)' \
  -- companion/protocol/python/tars_phase2 >/dev/null 2>&1; then
  fail "production, cloud, or network import found"
fi
if git -C "$REPO_ROOT" grep -n -E '\.package[[:space:]]*\(' \
  -- companion/protocol/swift/Package.swift >/dev/null 2>&1; then
  fail "external Swift dependency found"
fi
if git -C "$REPO_ROOT" grep -n -E 'using[[:space:]]+(System\.Net|System\.Net\.Http)|HttpClient|Socket' \
  -- companion/protocol/csharp >/dev/null 2>&1; then
  fail "C# network API found"
fi

artifact_paths=$(find "$PROTOCOL_ROOT" \( \
  -type d \( -name .build -o -name .swiftpm -o -name __pycache__ -o -name obj -o -name bin \) -o \
  -type f \( -name '*.pyc' -o -name '*.wav' -o -name '*.flac' -o -name '*.mp3' -o -name '*.m4a' -o -name '*.pcm' -o -name '*.raw' -o -name Package.resolved \) -o \
  -type l \
\) -print)
[ -z "$artifact_paths" ] || { printf '%s\n' "$artifact_paths" >&2; fail "generated or payload artifact found"; }

printf '%s\n' '{"artifacts":0,"forbiddenImports":0,"outOfScopePaths":0,"phase":"2A-artifact-scan","successful":true}'
