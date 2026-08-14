#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROTOCOL_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
PYTHON_ROOT="$PROTOCOL_ROOT/python"
SWIFT_ROOT="$PROTOCOL_ROOT/swift"
SANDBOX_PROFILE="$PROTOCOL_ROOT/sandbox/phase1a-offline.sb"

run_python() {
  /usr/bin/env -i HOME=/var/empty LC_ALL=C PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 PYTHONPATH="$PYTHON_ROOT" \
    TARS_PHASE1A_MODE=offline /usr/bin/sandbox-exec -f "$SANDBOX_PROFILE" \
    /usr/bin/python3 -m tars_phase2.runner
}

run_swift() {
  scratch=$(/usr/bin/mktemp -d /tmp/tars-g2a-swift.XXXXXX)
  /bin/mkdir -p "$scratch/home" "$scratch/tmp" "$scratch/module-cache"
  set +e
  output=$(
    /usr/bin/env -i HOME="$scratch/home" LC_ALL=C PATH=/usr/bin:/bin:/usr/sbin:/sbin \
      TMPDIR="$scratch/tmp" CLANG_MODULE_CACHE_PATH="$scratch/module-cache" \
      SWIFTPM_MODULECACHE_OVERRIDE="$scratch/module-cache" TARS_PHASE1A_MODE=offline \
      /usr/bin/sandbox-exec -f "$SANDBOX_PROFILE" /usr/bin/swift test \
      --disable-sandbox --package-path "$SWIFT_ROOT" --scratch-path "$scratch/build" 2>&1
  )
  status=$?
  set -e
  /bin/rm -rf "$scratch"
  if [ "$status" -ne 0 ]; then
    printf '%s\n' "$output" >&2
    return "$status"
  fi
  count=$(printf '%s\n' "$output" | /usr/bin/sed -n 's/.*Executed \([0-9][0-9]*\) tests.*/\1/p' | /usr/bin/tail -n 1)
  [ -n "$count" ] || { printf '%s\n' "$output" >&2; return 1; }
  printf '{"successful":true,"testsRun":%s}\n' "$count"
}

run_csharp() {
  dotnet_path=$(command -v dotnet || true)
  if [ -z "$dotnet_path" ]; then
    echo '{"available":false,"successful":false,"reason":"dotnet SDK unavailable"}'
    return 2
  fi
  scratch=$(/usr/bin/mktemp -d /tmp/tars-g2a-dotnet.XXXXXX)
  /bin/mkdir -p "$scratch/home" "$scratch/tmp" "$scratch/nuget" "$scratch/obj" "$scratch/bin"
  nuget_config="$scratch/NuGet.Config"
  /usr/bin/printf '%s\n' \
    '<?xml version="1.0" encoding="utf-8"?>' \
    '<configuration><packageSources><clear /></packageSources></configuration>' \
    >"$nuget_config"
  set +e
  restore_output=$(
    /usr/bin/env -i HOME="$scratch/home" LC_ALL=C PATH=/usr/bin:/bin:/usr/sbin:/sbin \
      TMPDIR="$scratch/tmp" DOTNET_CLI_HOME="$scratch/home" DOTNET_CLI_TELEMETRY_OPTOUT=1 \
      DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 NUGET_PACKAGES="$scratch/nuget" \
      /usr/bin/sandbox-exec -f "$SANDBOX_PROFILE" "$dotnet_path" restore \
      "$PROTOCOL_ROOT/csharp/ProtocolV2Vectors.csproj" --configfile "$nuget_config" \
      --property:BaseIntermediateOutputPath="$scratch/obj/" \
      --property:MSBuildProjectExtensionsPath="$scratch/obj/" \
      --property:BaseOutputPath="$scratch/bin/" 2>&1
  )
  restore_status=$?
  if [ "$restore_status" -ne 0 ]; then
    set -e
    /bin/rm -rf "$scratch"
    printf '%s\n' "$restore_output" >&2
    return "$restore_status"
  fi
  output=$(
    /usr/bin/env -i HOME="$scratch/home" LC_ALL=C PATH=/usr/bin:/bin:/usr/sbin:/sbin \
      TMPDIR="$scratch/tmp" DOTNET_CLI_HOME="$scratch/home" DOTNET_CLI_TELEMETRY_OPTOUT=1 \
      DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
      NUGET_PACKAGES="$scratch/nuget" /usr/bin/sandbox-exec -f "$SANDBOX_PROFILE" "$dotnet_path" run \
      --project "$PROTOCOL_ROOT/csharp/ProtocolV2Vectors.csproj" --no-restore \
      --property:BaseIntermediateOutputPath="$scratch/obj/" \
      --property:MSBuildProjectExtensionsPath="$scratch/obj/" \
      --property:BaseOutputPath="$scratch/bin/" 2>&1
  )
  result=$?
  set -e
  /bin/rm -rf "$scratch"
  printf '%s\n' "$output"
  return "$result"
}

first_python=$(run_python)
first_swift=$(run_swift)
second_python=$(run_python)
second_swift=$(run_swift)
[ "$first_python" = "$second_python" ] || { echo "Python result is not deterministic" >&2; exit 1; }
[ "$first_swift" = "$second_swift" ] || { echo "Swift result is not deterministic" >&2; exit 1; }

set +e
first_csharp=$(run_csharp)
csharp_status=$?
second_csharp=$(run_csharp)
second_csharp_status=$?
set -e
if [ "$csharp_status" -ne 0 ] || [ "$second_csharp_status" -ne 0 ]; then
  printf '%s\n' "$first_csharp" >&2
  printf '%s\n' "$second_csharp" >&2
  echo 'G2-A C# vector evidence is unavailable' >&2
  exit 2
fi
[ "$first_csharp" = "$second_csharp" ] || { echo "C# result is not deterministic" >&2; exit 1; }

printf '{"phase":"2A-offline-guard","python":%s,"swift":%s,"csharp":%s,"successful":true}\n' \
  "$first_python" "$first_swift" "$first_csharp"
