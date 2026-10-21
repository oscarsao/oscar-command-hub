#!/usr/bin/env bash
# Instala el servicio de carriles y muestra el estado. Los workers simultáneos los fija lanes.yaml
# (runner.max_workers), no esta línea: así hay una sola fuente de verdad.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null || pwd)"
powershell -NoProfile -ExecutionPolicy Bypass -File "$DIR/install-service.ps1"
sleep 5
py -3.12 "$DIR/lanes.py" status
