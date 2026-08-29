#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"

SIGN_IDENTITY="${SIGN_IDENTITY:-Developer ID Application: Travel Advisory LLC (3FLG8W6B95)}"
NOTARY_PROFILE="${NOTARY_PROFILE:-tars-notary}"
APP_NAME="${APP_NAME:-TarsCompanion}"
TEAM_ID="3FLG8W6B95"
BUNDLE_ID="com.ellaexecutivesearch.tarscompanion"
PACKAGED_APP_NAME="TarsCompanion"

APP_BUNDLE="${DIST_DIR}/${PACKAGED_APP_NAME}.app"
APP_EXECUTABLE="${APP_BUNDLE}/Contents/MacOS/TarsCompanionApp"
ENTITLEMENTS="${REPO_ROOT}/companion/native-macos/Resources/TarsCompanionApp.entitlements"
DMG_PATH="${DIST_DIR}/${APP_NAME}.dmg"

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
