#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"

EXPECTED_SIGN_IDENTITY="Developer ID Application: Travel Advisory LLC (3FLG8W6B95)"
SIGN_IDENTITY="${SIGN_IDENTITY:-${EXPECTED_SIGN_IDENTITY}}"
NOTARY_PROFILE="${NOTARY_PROFILE:-tars-notary}"
APP_NAME="${APP_NAME:-TarsCompanion}"
TEAM_ID="3FLG8W6B95"
BUNDLE_ID="com.ellaexecutivesearch.tarscompanion"
PACKAGED_APP_NAME="TarsCompanion"

APP_BUNDLE="${DIST_DIR}/${PACKAGED_APP_NAME}.app"
APP_EXECUTABLE="${APP_BUNDLE}/Contents/MacOS/TarsCompanionApp"
ENTITLEMENTS="${REPO_ROOT}/companion/native-macos/Resources/TarsCompanionApp.entitlements"
DMG_PATH="${DIST_DIR}/${APP_NAME}.dmg"

RELEASE_MODE="distribution"
TASK11_PROVENANCE_FILE=""
while (($# > 0)); do
    case "$1" in
        --signed-app-only)
            RELEASE_MODE="signed-app-only"
            ;;
        --task11-provenance)
            shift
            if (($# == 0)); then
                printf '%s\n' 'Erro: --task11-provenance requer um arquivo.' >&2
                exit 64
            fi
            TASK11_PROVENANCE_FILE="$1"
            ;;
        *)
            printf 'Erro: argumento desconhecido: %s\n' "$1" >&2
            exit 64
            ;;
    esac
    shift
done

# A fake runner is an offline-test seam.  The default distribution path still
# invokes the real command names exactly as it did before Task 11.
run_release_command() {
    if [[ -n "${TARS_RELEASE_COMMAND_RUNNER:-}" ]]; then
        "${TARS_RELEASE_COMMAND_RUNNER}" "$@"
    else
        "$@"
    fi
}

# Mirror the Python inspector's exact CodeDirectory grammar without invoking
# codesign.  Bash 3.2 cannot safely parse arbitrarily large hex integers, so
# test the hardened-runtime bit by its fifth-from-last hex digit (bit 0x10000)
# instead of relying on arithmetic conversion.
require_hardened_runtime_code_directory() {
    local details="$1"
    local code_directory_count code_directory_line pattern flags flag_length hardened_digit
    code_directory_count="$(printf '%s\n' "${details}" | grep -Ec '^[[:space:]]*CodeDirectory([[:space:]]|$)' || true)"
    [[ "${code_directory_count}" == "1" ]] || return 1
    code_directory_line="$(printf '%s\n' "${details}" | sed -n 's/^[[:space:]]*\(CodeDirectory.*\)$/\1/p')"
    pattern='^CodeDirectory v=([0-9]+) size=([0-9]+) flags=0x([0-9A-Fa-f]+)\(runtime\) hashes=([0-9]+)\+([0-9]+) location=([^[:space:]]+)$'
    [[ "${code_directory_line}" =~ ${pattern} ]] || return 1
    flags="${BASH_REMATCH[3]}"
    flag_length="${#flags}"
    (( flag_length >= 5 )) || return 1
    hardened_digit="${flags:$((flag_length - 5)):1}"
    case "${hardened_digit}" in
        1|3|5|7|9|b|d|f|B|D|F) return 0 ;;
        *) return 1 ;;
    esac
}

# Derive the digest from a disposable copy of a signed executable.  The final
# bundle signature is intentionally allowed to change the Mach-O bytes; the
# signature-neutral payload is what the Task 11 provenance resource seals.
# The stripping operation crosses the same injectable command boundary as the
# public signature checks, and the original signed executable is never edited.
signature_neutral_digest() {
    local executable="$1"
    local digest_tmp digest
    digest_tmp="$(mktemp "${TMPDIR:-/tmp}/tars-task11-executable.XXXXXX")"
    if ! cp -p "${executable}" "${digest_tmp}"; then
        rm -f "${digest_tmp}"
        return 67
    fi
    if ! run_release_command codesign --remove-signature "${digest_tmp}"; then
        rm -f "${digest_tmp}"
        return 67
    fi
    if ! digest="$(shasum -a 256 "${digest_tmp}" | awk '{print $1}')"; then
        rm -f "${digest_tmp}"
        return 67
    fi
    rm -f "${digest_tmp}"
    if [[ ! "${digest}" =~ ^[0-9a-f]{64}$ ]]; then
        return 67
    fi
    printf '%s\n' "${digest}"
}

run_signed_app_only() {
    if [[ "${SIGN_IDENTITY}" != "${EXPECTED_SIGN_IDENTITY}" ]]; then
        printf '%s\n' 'Erro: signed-app-only requer a identidade Developer ID Application esperada.' >&2
        return 65
    fi
    if [[ -z "${TASK11_PROVENANCE_FILE}" || ! -f "${TASK11_PROVENANCE_FILE}" ]]; then
        printf '%s\n' 'Erro: o modo signed-app-only requer --task11-provenance com arquivo existente.' >&2
        return 64
    fi

    # The caller supplies the expected clean provenance, while these values
    # are derived at execution time from the checkout being packaged.  A
    # missing/dirty/mismatched tree cannot be sealed into an eligible app.
    local supplied_head supplied_tree supplied_dirty current_head current_tree
    supplied_head="$(sed -n 's/^head=//p' "${TASK11_PROVENANCE_FILE}")"
    supplied_tree="$(sed -n 's/^tree=//p' "${TASK11_PROVENANCE_FILE}")"
    supplied_dirty="$(sed -n 's/^dirty=//p' "${TASK11_PROVENANCE_FILE}")"
    current_head="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
    current_tree="$(git -C "${REPO_ROOT}" rev-parse 'HEAD^{tree}')"
    if [[ ! "${supplied_head}" =~ ^[0-9a-f]{40}$ || ! "${supplied_tree}" =~ ^[0-9a-f]{40}$ || "${supplied_dirty}" != "false" ]]; then
        printf '%s\n' 'Erro: provenance Task 11 ausente, malformado ou dirty.' >&2
        return 65
    fi
    if [[ "${supplied_head}" != "${current_head}" || "${supplied_tree}" != "${current_tree}" ]]; then
        printf '%s\n' 'Erro: provenance Task 11 não corresponde ao HEAD/tree corrente.' >&2
        return 65
    fi
    if [[ -n "$(git -C "${REPO_ROOT}" status --porcelain --untracked-files=all)" ]]; then
        printf '%s\n' 'Erro: a árvore Task 11 não está limpa.' >&2
        return 65
    fi

    run_release_command bash "${REPO_ROOT}/scripts/package_menubar_app.sh"

    # Packaging is an executable boundary: a concurrent tracked/untracked
    # mutation must invalidate the supplied preflight before any digest,
    # provenance resource, or final signature is written.
    local post_package_head post_package_tree post_package_status
    post_package_head="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
    post_package_tree="$(git -C "${REPO_ROOT}" rev-parse 'HEAD^{tree}')"
    post_package_status="$(git -C "${REPO_ROOT}" status --porcelain --untracked-files=all)"
    if [[ "${post_package_head}" != "${supplied_head}" || "${post_package_tree}" != "${supplied_tree}" || -n "${post_package_status}" ]]; then
        printf '%s\n' 'Erro: a proveniência/árvore mudou durante o empacotamento.' >&2
        return 65
    fi
    if [[ ! -x "${APP_EXECUTABLE}" ]]; then
        printf 'Erro: executável esperado não encontrado: %s\n' "${APP_EXECUTABLE}" >&2
        return 66
    fi

    # The package script may already apply an ad-hoc executable signature.
    # Strip only a disposable copy through the command boundary before the one
    # final bundle signing operation below; verification derives the same
    # signature-neutral digest from another disposable copy.
    local executable_digest provenance_digest provenance_tmp provenance_json
    if ! executable_digest="$(signature_neutral_digest "${APP_EXECUTABLE}")"; then
        printf '%s\n' 'Erro: não foi possível derivar o digest signature-neutral do executável empacotado.' >&2
        return 67
    fi
    provenance_tmp="$(mktemp "${TMPDIR:-/tmp}/tars-task11-provenance.XXXXXX")"
    provenance_json="${APP_BUNDLE}/Contents/Resources/Task11Provenance.json"
    trap 'rm -f "${provenance_tmp}"' RETURN
    mkdir -p "${APP_BUNDLE}/Contents/Resources"
    # This first form is exactly the canonical payload hashed by
    # artifact_provenance_digest() in the Python verifier.
    printf '%s' "{\"dirty\":false,\"executable_sha256\":\"${executable_digest}\",\"head\":\"${supplied_head}\",\"tree\":\"${supplied_tree}\"}" >"${provenance_tmp}"
    provenance_digest="$(shasum -a 256 "${provenance_tmp}" | awk '{print $1}')"
    [[ "${provenance_digest}" =~ ^[0-9a-f]{64}$ ]] || return 67
    # The sealed digest is a field of the canonical payload and is checked
    # independently by the Python preflight; it is not a caller-selected flag.
    printf '%s' "{\"bundle_id\":\"${BUNDLE_ID}\",\"dirty\":false,\"entitlements\":[\"com.apple.security.device.audio-input\"],\"executable_sha256\":\"${executable_digest}\",\"head\":\"${supplied_head}\",\"hardened_runtime\":true,\"provenance_sha256\":\"${provenance_digest}\",\"strict_signature\":true,\"team_id\":\"${TEAM_ID}\",\"tree\":\"${supplied_tree}\"}" >"${provenance_tmp}"
    python3 - "${provenance_tmp}" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
path.write_bytes(
    json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
)
PY
    mv -f "${provenance_tmp}" "${provenance_json}"
    run_release_command codesign --force --options runtime --entitlements "${ENTITLEMENTS}" --sign "${SIGN_IDENTITY}" "${APP_BUNDLE}"
    run_release_command codesign --verify --deep --strict --verbose=2 "${APP_BUNDLE}"
    local details
    details="$(run_release_command codesign -dv --verbose=4 "${APP_BUNDLE}" 2>&1)"
    printf '%s\n' "${details}"
    grep -Fqx -- "Identifier=${BUNDLE_ID}" <<<"${details}" || return 67
    grep -Fqx -- "TeamIdentifier=${TEAM_ID}" <<<"${details}" || return 67
    grep -Fqx -- "Authority=${EXPECTED_SIGN_IDENTITY}" <<<"${details}" || return 67
    require_hardened_runtime_code_directory "${details}" || return 67
    local entitlement_details readback_digest
    entitlement_details="$(run_release_command codesign -d --entitlements :- "${APP_BUNDLE}" 2>&1)"
    local compact_entitlements
    compact_entitlements="$(tr -d '[:space:]' <<<"${entitlement_details}")"
    # Isolate and compare the complete canonical plist payload.  Requiring the
    # exact one-key allowlist rejects false/lookalike values and unexpected
    # extras such as get-task-allow, rather than accepting a matching fragment.
    if [[ "${compact_entitlements}" != *"<plist"* || "${compact_entitlements}" != *"</plist>"* ]]; then
        return 67
    fi
    local entitlement_plist
    entitlement_plist="<plist${compact_entitlements#*<plist}"
    entitlement_plist="${entitlement_plist%%</plist>*}</plist>"
    [[ "${entitlement_plist}" == "<plistversion=\"1.0\"><dict><key>com.apple.security.device.audio-input</key><true/></dict></plist>" ]] || return 67
    if ! readback_digest="$(signature_neutral_digest "${APP_EXECUTABLE}")"; then
        printf '%s\n' 'Erro: não foi possível derivar o digest signature-neutral do executável assinado.' >&2
        return 67
    fi
    [[ "${readback_digest}" == "${executable_digest}" ]] || return 67
    grep -Fq -- "\"executable_sha256\":\"${readback_digest}\"" "${provenance_json}" || return 67
    grep -Fq -- "\"head\":\"${supplied_head}\"" "${provenance_json}" || return 67
    grep -Fq -- "\"tree\":\"${supplied_tree}\"" "${provenance_json}" || return 67
    printf 'Signature-neutral executable SHA-256: %s\n' "${readback_digest}"
    printf '%s\n' "Signed-app-only qualification complete: ${APP_BUNDLE}"
    return 0
}

if [[ "${RELEASE_MODE}" == "signed-app-only" ]]; then
    run_signed_app_only
    exit $?
fi

printf '%s\n' 'Pré-voo de assinatura e notarização...'

if ! security find-identity -v -p codesigning | grep -Fq -- "\"${SIGN_IDENTITY}\""; then
    printf 'Erro: certificado Developer ID ausente: %s\n' "$SIGN_IDENTITY" >&2
    printf '%s\n' 'O Account Holder deve criá-lo em Xcode → Settings → Accounts → Manage Certificates → + → Developer ID Application.' >&2
    exit 2
fi

if ! xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1; then
    printf 'Erro: o perfil de notarização "%s" não está disponível.\n' "$NOTARY_PROFILE" >&2
    printf '%s\n' 'O Account Holder deve criar o perfil com este comando exato:' >&2
    printf '%s\n' 'xcrun notarytool store-credentials "tars-notary" --apple-id <APPLE_ID> --team-id 3FLG8W6B95 --password <app-specific-password>' >&2
    exit 3
fi

printf '%s\n' 'Empacotando o app de release com o script existente...'
bash "${REPO_ROOT}/scripts/package_menubar_app.sh"

if [[ ! -x "${APP_EXECUTABLE}" ]]; then
    printf 'Erro: executável esperado não encontrado: %s\n' "${APP_EXECUTABLE}" >&2
    exit 4
fi

printf '%s\n' 'Assinando o executável (ordem mais profunda primeiro)...'
codesign --force --options runtime --timestamp --entitlements "${ENTITLEMENTS}" --sign "$SIGN_IDENTITY" "${APP_EXECUTABLE}"

printf '%s\n' 'Assinando o bundle do app...'
codesign --force --options runtime --timestamp --entitlements "${ENTITLEMENTS}" --sign "$SIGN_IDENTITY" "${APP_BUNDLE}"

printf '%s\n' 'Verificando assinatura e runtime endurecido...'
codesign --verify --deep --strict --verbose=2 "${APP_BUNDLE}"
CODE_SIGN_DETAILS="$(codesign -dv --verbose=4 "${APP_BUNDLE}" 2>&1)"
printf '%s\n' "${CODE_SIGN_DETAILS}"

if ! grep -Fqx -- "Identifier=${BUNDLE_ID}" <<<"${CODE_SIGN_DETAILS}"; then
    printf 'Erro: a assinatura não informa o Bundle ID esperado (%s).\n' "${BUNDLE_ID}" >&2
    exit 5
fi
if ! grep -Fqx -- "TeamIdentifier=${TEAM_ID}" <<<"${CODE_SIGN_DETAILS}"; then
    printf 'Erro: a assinatura não informa o Team ID esperado (%s).\n' "${TEAM_ID}" >&2
    exit 5
fi
if ! grep -Eq 'flags=.*runtime' <<<"${CODE_SIGN_DETAILS}"; then
    printf '%s\n' 'Erro: a assinatura não informa o hardened runtime (flags=...runtime).' >&2
    exit 5
fi

printf 'Criando DMG UDZO em %s...\n' "${DMG_PATH}"
hdiutil create -volname "${APP_NAME}" -srcfolder "${APP_BUNDLE}" -ov -format UDZO "${DMG_PATH}"
codesign --force --timestamp --sign "$SIGN_IDENTITY" "${DMG_PATH}"

printf '%s\n' 'Enviando o DMG para notarização...'
if NOTARY_OUTPUT="$(xcrun notarytool submit "${DMG_PATH}" --keychain-profile "$NOTARY_PROFILE" --wait 2>&1)"; then
    NOTARY_STATUS=0
else
    NOTARY_STATUS=$?
fi
printf '%s\n' "${NOTARY_OUTPUT}"

SUBMISSION_ID="$(printf '%s\n' "${NOTARY_OUTPUT}" | sed -n 's/^[[:space:]]*id:[[:space:]]*//p' | head -n 1)"
if grep -q 'Invalid' <<<"${NOTARY_OUTPUT}"; then
    printf '%s\n' 'A notarização retornou Invalid; buscando o log da submissão...' >&2
    if [[ -n "${SUBMISSION_ID}" ]]; then
        xcrun notarytool log "${SUBMISSION_ID}" --keychain-profile "$NOTARY_PROFILE" || true
    else
        printf '%s\n' 'Não foi possível extrair o ID da submissão para buscar o log.' >&2
    fi
    if (( NOTARY_STATUS == 0 )); then
        exit 1
    fi
    exit "${NOTARY_STATUS}"
fi
if (( NOTARY_STATUS != 0 )); then
    printf 'Erro: notarytool falhou com código %d.\n' "${NOTARY_STATUS}" >&2
    exit "${NOTARY_STATUS}"
fi

printf '%s\n' 'Aplicando e validando o ticket de notarização...'
xcrun stapler staple "${DMG_PATH}"
xcrun stapler validate "${DMG_PATH}"

DMG_SHA256="$(shasum -a 256 "${DMG_PATH}" | awk '{print $1}')"
if SPCTL_OUTPUT="$(spctl -a -vvv -t install "${APP_BUNDLE}" 2>&1)"; then
    SPCTL_STATUS=0
else
    SPCTL_STATUS=$?
fi

printf '%s\n' 'Resumo final:'
printf 'DMG: %s\n' "${DMG_PATH}"
printf 'SHA-256: %s\n' "${DMG_SHA256}"
printf '%s\n' 'Resultado spctl -a -vvv -t install:'
printf '%s\n' "${SPCTL_OUTPUT}"
if (( SPCTL_STATUS != 0 )); then
    printf 'Erro: spctl rejeitou o app com código %d.\n' "${SPCTL_STATUS}" >&2
    exit "${SPCTL_STATUS}"
fi
