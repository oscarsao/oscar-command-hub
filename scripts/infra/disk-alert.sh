#!/bin/sh
# Aviso por Telegram al 80 % y 90 % de disco (raíz). Una alerta por nivel y día (anti-spam).
# Requiere /etc/pildora/telegram.env con TG_BOT_TOKEN y TG_CHAT_ID (bot de carriles, grupo Gestión), chmod 600.
# Uso: disk-alert.sh [--status]   (--status imprime el estado y sale; lo reutiliza /salud)
set -u
. /etc/pildora/telegram.env
USED=$(df / --output=pcent | tail -1 | tr -dc '0-9')
FREE=$(df -h / --output=avail | tail -1 | tr -d ' ')
[ "${1:-}" = "--status" ] && { echo "pildora-01 disco: ${USED}% usado, ${FREE} libres"; exit 0; }
LEVEL=0
[ "$USED" -ge 80 ] && LEVEL=80
[ "$USED" -ge 90 ] && LEVEL=90
[ "$LEVEL" -eq 0 ] && exit 0
STAMP=/var/tmp/disk-alert-$LEVEL-$(date +%F)
[ -e "$STAMP" ] && exit 0
touch "$STAMP"
MSG="⚠️ pildora-01: disco al ${USED}% (${FREE} libres). Umbral ${LEVEL}%. Revisar: docker system df -v; /var/log/docker-cleanup.log"
curl -fsS -m 15 "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TG_CHAT_ID}" --data-urlencode "text=${MSG}" >/dev/null
