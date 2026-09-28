#!/usr/bin/env bash
# Autoriza a María José Gonçalves (5516823836) a hablar con Hermes. La skill la limita a Gestión y sin ejecutar.
set -euo pipefail
PY="$HOME/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe"
DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONIOENCODING=utf-8
"$PY" "$DIR/add_allowed_user.py" 5516823836 --apply
"$HOME/AppData/Local/hermes/bin/hermes" gateway restart
echo "LISTO."
