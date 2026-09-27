#!/usr/bin/env bash
# Temas de operaciones por marca: Hermes responde sin mención en Gestión t5, t230 y t231. Para en el primer fallo.
set -euo pipefail
PY="$HOME/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe"
DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONIOENCODING=utf-8
"$PY" "$DIR/apply_ops_topics.py" --apply
"$HOME/AppData/Local/hermes/bin/hermes" gateway restart
echo "LISTO. Prueba a escribir en Operaciones · MigraTeam sin mencionar a Hermes."
