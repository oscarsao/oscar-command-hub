#!/usr/bin/env bash
# Bloque B (27-09): W3b + tuning de Hermes + reinicio + diagnóstico. Para en el primer fallo.
set -euo pipefail
PY="$HOME/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe"
DIR="$(cd "$(dirname "$0")" && pwd)"
HERMES="$HOME/AppData/Local/hermes/bin/hermes"
export PYTHONIOENCODING=utf-8

echo "== 1/4 W3b (un bot por tema) =="
"$PY" "$DIR/apply_w3b_config.py" --apply
echo "== 2/4 Tuning (Coordinador, no_mcp, cron mínimo, compresión, write_approval) =="
"$PY" "$DIR/apply_hermes_tuning.py" --apply --keep-browser
echo "== 3/4 Reinicio del gateway =="
"$HERMES" gateway restart
echo "== 4/4 Diagnóstico del kanban =="
"$HERMES" kanban diagnostics || true
echo "LISTO. Escribe /new en tu DM con Hermes."
