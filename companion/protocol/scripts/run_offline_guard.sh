#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROTOCOL_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
REPO_ROOT=$(git -C "$PROTOCOL_ROOT" rev-parse --show-toplevel)
PYTHON_ROOT="$PROTOCOL_ROOT/python"
SANDBOX_PROFILE="$PROTOCOL_ROOT/sandbox/phase1a-offline.sb"
SCHEMA_RELATIVE="companion/protocol/schema/protocol-v1.schema.json"
MANIFEST_RELATIVE="companion/protocol/fixtures/phase1a-v1.manifest.json"

for relative_path in "$SCHEMA_RELATIVE" "$MANIFEST_RELATIVE"; do
  if git -C "$REPO_ROOT" check-ignore -q --no-index -- "$relative_path"; then
    echo "Phase 1A tracked input is ignored: $relative_path" >&2
    exit 1
  fi
  if [ ! -f "$REPO_ROOT/$relative_path" ]; then
    echo "Phase 1A tracked input is missing: $relative_path" >&2
    exit 1
  fi
  if ! git -C "$REPO_ROOT" ls-files --error-unmatch -- "$relative_path" >/dev/null 2>&1; then
    echo "Phase 1A input is not tracked in the Git index: $relative_path" >&2
    exit 1
  fi
done

run_once() {
  /usr/bin/env -i \
    HOME=/var/empty \
    LC_ALL=C \
    PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PYTHONPATH="$PYTHON_ROOT" \
    TARS_PHASE1A_MODE=offline \
    /usr/bin/sandbox-exec -f "$SANDBOX_PROFILE" \
    /usr/bin/python3 -m tars_phase1a.runner
}

first_summary=$(run_once)
second_summary=$(run_once)

if [ "$first_summary" != "$second_summary" ]; then
  echo "Phase 1A guard results were not deterministic" >&2
  echo "first:  $first_summary" >&2
  echo "second: $second_summary" >&2
  exit 1
fi

printf '%s\n' "$first_summary"
