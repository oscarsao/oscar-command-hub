#!/bin/sh
# Instalación en pildora-01 (root, SOLO tras OK de Oscar). Idempotente. Ejecutar desde la carpeta de los scripts.
set -eu
install -m 755 docker-cleanup.sh /usr/local/bin/docker-cleanup.sh
install -m 755 disk-alert.sh /usr/local/bin/disk-alert.sh
cat > /etc/systemd/system/docker-cleanup.service <<'U'
[Unit]
Description=Limpieza Docker (build cache e imagenes antiguas)
[Service]
Type=oneshot
ExecStart=/usr/local/bin/docker-cleanup.sh
U
cat > /etc/systemd/system/docker-cleanup.timer <<'U'
[Unit]
Description=Limpieza Docker diaria
[Timer]
OnCalendar=*-*-* 04:30:00
Persistent=true
[Install]
WantedBy=timers.target
U
cat > /etc/systemd/system/disk-alert.service <<'U'
[Unit]
Description=Aviso de disco por Telegram
[Service]
Type=oneshot
ExecStart=/usr/local/bin/disk-alert.sh
U
cat > /etc/systemd/system/disk-alert.timer <<'U'
[Unit]
Description=Comprobar disco cada 15 min
[Timer]
OnCalendar=*:0/15
[Install]
WantedBy=timers.target
U
systemctl daemon-reload
systemctl enable --now docker-cleanup.timer disk-alert.timer
docker swarm update --task-history-limit 2
echo OK
systemctl list-timers docker-cleanup.timer disk-alert.timer --no-pager
