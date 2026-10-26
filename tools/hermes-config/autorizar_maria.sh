#!/usr/bin/env bash
# Autoriza a María José Gonçalves a hablar con Hermes. La skill la limita a Gestión y sin ejecutar.
# Su user_id de Telegram ya no está en el repo (es público): pásalo como argumento o en MARIA_TELEGRAM_ID.
#   uso: autorizar_maria.sh <user_id>
set -euo pipefail
MARIA_ID="${1:-${MARIA_TELEGRAM_ID:-}}"
[ -n "$MARIA_ID" ] || { echo "Falta el user_id de María (argumento o MARIA_TELEGRAM_ID)." >&2; exit 1; }
PY="$HOME/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe"
DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONIOENCODING=utf-8
"$PY" "$DIR/add_allowed_user.py" "$MARIA_ID" --apply
"$HOME/AppData/Local/hermes/bin/hermes" gateway restart
echo "LISTO."
