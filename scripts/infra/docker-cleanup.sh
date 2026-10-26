#!/bin/sh
# Limpieza diaria de Docker en pildora-01. NUNCA toca volúmenes.
# Instalar en /usr/local/bin/docker-cleanup.sh (root). Log: /var/log/docker-cleanup.log
set -u
LOG=/var/log/docker-cleanup.log
{
  echo "=== $(date -Is) antes: $(df -h / | awk 'NR==2{print $5" usado, "$4" libres"}')"
  docker builder prune --keep-storage 5GB -f
  docker image prune -a --filter "until=48h" -f
  echo "=== $(date -Is) despues: $(df -h / | awk 'NR==2{print $5" usado, "$4" libres"}')"
} >> "$LOG" 2>&1
