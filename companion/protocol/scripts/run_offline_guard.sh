#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROTOCOL_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
REPO_ROOT=$(git -C "$PROTOCOL_ROOT" rev-parse --show-toplevel)
PYTHON_ROOT="$PROTOCOL_ROOT/python"
SANDBOX_PROFILE="$PROTOCOL_ROOT/sandbox/phase1a-offline.sb"
SCHEMA_RELATIVE="companion/protocol/schema/protocol-v1.schema.json"
MANIFEST_RELATIVE="companion/protocol/fixtures/phase1a-v1.manifest.json"
VECTORS_RELATIVE="companion/protocol/vectors/protocol-v1-vectors.json"
SWIFT_ROOT="$PROTOCOL_ROOT/swift"

for relative_path in "$SCHEMA_RELATIVE" "$MANIFEST_RELATIVE" "$VECTORS_RELATIVE"; do
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

run_python_once() {
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

run_swift_once() {
  scratch=$(/usr/bin/mktemp -d /tmp/tars-phase1a-swift.XXXXXX)
  /bin/mkdir -p "$scratch/home" "$scratch/tmp" "$scratch/module-cache"

  set +e
  swift_output=$(
    /usr/bin/env -i \
      HOME="$scratch/home" \
      LC_ALL=C \
      PATH=/usr/bin:/bin:/usr/sbin:/sbin \
      TMPDIR="$scratch/tmp" \
      CLANG_MODULE_CACHE_PATH="$scratch/module-cache" \
      SWIFTPM_MODULECACHE_OVERRIDE="$scratch/module-cache" \
      TARS_PHASE1A_MODE=offline \
      /usr/bin/sandbox-exec -f "$SANDBOX_PROFILE" \
      /usr/bin/swift test \
        --disable-sandbox \
        --package-path "$SWIFT_ROOT" \
        --scratch-path "$scratch/build" 2>&1
  )
  swift_status=$?
  set -e

  /bin/rm -rf "$scratch"
  if [ "$swift_status" -ne 0 ]; then
    printf '%s\n' "$swift_output" >&2
    return "$swift_status"
  fi

  swift_count=$(
    printf '%s\n' "$swift_output" \
      | /usr/bin/sed -n 's/.*Executed \([0-9][0-9]*\) tests.*/\1/p' \
      | /usr/bin/tail -n 1
  )
  if [ -z "$swift_count" ]; then
    printf '%s\n' "$swift_output" >&2
    echo "Phase 1A could not read the Swift test count" >&2
    return 1
  fi
  printf '{"successful":true,"testsRun":%s}' "$swift_count"
}

first_python_summary=$(run_python_once)
first_swift_summary=$(run_swift_once)
second_python_summary=$(run_python_once)
second_swift_summary=$(run_swift_once)

if [ "$first_python_summary" != "$second_python_summary" ]; then
  echo "Phase 1A guard results were not deterministic" >&2
  echo "first Python:  $first_python_summary" >&2
  echo "second Python: $second_python_summary" >&2
  exit 1
fi

if [ "$first_swift_summary" != "$second_swift_summary" ]; then
  echo "Phase 1A Swift results were not deterministic" >&2
  echo "first Swift:  $first_swift_summary" >&2
  echo "second Swift: $second_swift_summary" >&2
  exit 1
fi

printf '{"phase":"1A-guard","python":%s,"successful":true,"swift":%s}\n' \
  "$first_python_summary" \
  "$first_swift_summary"
