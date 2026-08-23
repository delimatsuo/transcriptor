#!/usr/bin/env bash
# Local backend launcher with Transcriptor-isolated Google credentials.
#
# This machine's default gcloud config (~/.config/gcloud) is shared with other
# projects (notably Ella ATS) and gets clobbered by their logins — which broke
# Firestore with 403s on 2026-08-23. Transcriptor therefore keeps its OWN
# gcloud config dir. One-time setup (browser sign-in as deli@ellaexecutivesearch.com):
#
#   export CLOUDSDK_CONFIG="$HOME/.config/gcloud-transcriptor" \
#     && gcloud auth application-default login \
#     && gcloud auth application-default set-quota-project transcriptor-490222
#
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export CLOUDSDK_CONFIG="${HOME}/.config/gcloud-transcriptor"
ADC="${CLOUDSDK_CONFIG}/application_default_credentials.json"
if [[ ! -f "${ADC}" ]]; then
  echo "ERRO: ADC isolado não encontrado em ${ADC}." >&2
  echo "Rode o setup único descrito no cabeçalho deste script." >&2
  exit 2
fi
export GOOGLE_APPLICATION_CREDENTIALS="${ADC}"

cd "${REPO_ROOT}"
exec .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port "${PORT:-8000}"
