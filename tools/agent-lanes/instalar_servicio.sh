#!/usr/bin/env bash
# Instala el servicio de carriles con 2 workers (1 por repo gracias a max_parallel: 1) y muestra el estado.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null || pwd)"
powershell -NoProfile -ExecutionPolicy Bypass -File "$DIR/install-service.ps1" -MaxWorkers 2
sleep 5
py -3.12 "$DIR/lanes.py" status
