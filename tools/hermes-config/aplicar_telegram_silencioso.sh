#!/usr/bin/env bash
# Telegram sin ruido: Hermes no muestra sus herramientas y limpia las burbujas de progreso. Para en el primer fallo.
set -euo pipefail
PY="$HOME/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe"
DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONIOENCODING=utf-8
"$PY" "$DIR/apply_telegram_quiet.py" --apply
"$HOME/AppData/Local/hermes/bin/hermes" gateway restart
echo "LISTO."
