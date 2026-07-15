#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROTOCOL_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
REPO_ROOT=$(git -C "$PROTOCOL_ROOT" rev-parse --show-toplevel)
GUARD_BASE=9ea95803e92ae740e6078903b2665cf604e1db09

fail() {
  echo "Phase 1A artifact scan failed: $1" >&2
  exit 1
}

git -C "$REPO_ROOT" cat-file -e "$GUARD_BASE^{commit}" \
  || fail "reviewed guard base is unavailable"

if [ -n "$(git -C "$REPO_ROOT" status --short)" ]; then
  fail "implementation worktree is not clean"
fi

git -C "$REPO_ROOT" diff --check "$GUARD_BASE"..HEAD \
  || fail "Phase 1A history contains whitespace errors"

git -C "$REPO_ROOT" diff --name-only "$GUARD_BASE"..HEAD \
  | while IFS= read -r path; do
      case "$path" in
        .gitignore|README.md|companion/protocol/*|docs/*)
          ;;
        *)
          fail "out-of-scope changed path: $path"
          ;;
      esac
    done

if git -C "$REPO_ROOT" grep -n -E \
  '^[[:space:]]*(from|import)[[:space:]]+(backend|google|firebase_admin|grpc|httpx|requests|urllib3|boto3|azure)([.[:space:]]|$)' \
  -- companion/protocol/python/tars_phase1a >/dev/null 2>&1; then
  fail "production, cloud, or network-client import found"
fi

if git -C "$REPO_ROOT" grep -n -E '\.package[[:space:]]*\(' \
  -- companion/protocol/swift/Package.swift >/dev/null 2>&1; then
  fail "external Swift package dependency found"
fi

artifact_paths=$(
  find "$PROTOCOL_ROOT" \
    \( \
      \( -type d \( -name .build -o -name .swiftpm -o -name __pycache__ \) \) -o \
      \( -type f \( \
        -name '*.pyc' -o \
        -name '*.wav' -o \
        -name '*.flac' -o \
        -name '*.mp3' -o \
        -name '*.m4a' -o \
        -name '*.pcm' -o \
        -name '*.raw' -o \
        -name Package.resolved \
      \) \) -o \
      -type l \
    \) \
    -print
)
if [ -n "$artifact_paths" ]; then
  printf '%s\n' "$artifact_paths" >&2
  fail "unexpected generated, audio, dependency, or symlink artifact found"
fi

for relative_path in \
  companion/protocol/schema/protocol-v1.schema.json \
  companion/protocol/fixtures/phase1a-v1.manifest.json \
  companion/protocol/vectors/protocol-v1-vectors.json; do
  git -C "$REPO_ROOT" ls-files --error-unmatch -- "$relative_path" >/dev/null 2>&1 \
    || fail "required tracked input is missing: $relative_path"
  if git -C "$REPO_ROOT" check-ignore -q --no-index -- "$relative_path"; then
    fail "required tracked input is ignored: $relative_path"
  fi
done

printf '%s\n' \
  '{"artifacts":0,"forbiddenImports":0,"outOfScopePaths":0,"phase":"1A-artifact-scan","successful":true}'
