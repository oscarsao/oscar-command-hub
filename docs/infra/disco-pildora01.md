# t_00d6c8c0 — Disco pildora-01: limpieza automática, avisos, imagen Oscar HQ

Estado: **scripts preparados en el workspace; nada ejecutado en el servidor.** El worker no tiene SSH ni acceso de
lectura a `oscar-command-hub` / `oscar-hq` (fuera de los directorios permitidos), y tocar el servidor requiere OK de Oscar.
No he podido revisar el Dockerfile real ni la skill `desplegar-dokploy`.

## Entregables (copiar a `oscar-command-hub/scripts/infra/`)
- `scripts/infra/docker-cleanup.sh` — `builder prune --keep-storage 5GB -f` + `image prune -a --filter until=48h -f`. Sin volúmenes. Log en /var/log/docker-cleanup.log.
- `scripts/infra/disk-alert.sh` — aviso Telegram al 80 % y 90 % (una vez por nivel y día); `--status` para /salud.
- `scripts/infra/install-pildora01.sh` — instala scripts, timers systemd (limpieza 04:30 diaria, alerta cada 15 min) y `docker swarm update --task-history-limit 2`.
- Secreto: `/etc/pildora/telegram.env` (TG_BOT_TOKEN, TG_CHAT_ID; chmod 600) — lo crea Oscar; no está en estos ficheros.
- Sin probar en servidor (solo escritos). Revisar `df --output` y `systemd` en la distro real de pildora-01.

## /salud
Añadir al comando /salud del bot una llamada por SSH a `disk-alert.sh --status` (o `df -h /`). Requiere editar el bot (no accesible desde este worker).

## Reducir la imagen de Oscar HQ (3,5 GB) — recomendaciones (sin ver el Dockerfile)
1. Multi-stage: construir el venv/wheels en una etapa builder y copiar solo el venv a una final `python:3.x-slim`.
2. Playwright: instalar solo Chromium (`playwright install --with-deps chromium`, sin firefox/webkit); valorar `chromium-headless-shell` o sacar el scraping a un contenedor aparte.
3. `pip install --no-cache-dir`; `apt-get install --no-install-recommends` + `rm -rf /var/lib/apt/lists/*`.
4. `.dockerignore`: `.git`, datos/memoria personales, `node_modules`, `__pycache__`, tests.
5. Auditar dependencias pesadas no usadas (`pip list` con tamaños; `docker history` por capa).
6. Capas: requirements antes que el código, para reutilizar caché y generar menos capas únicas por despliegue.
Ahorro estimado 1–2 GB (hipótesis; medir con `docker history`).

## Texto para la skill desplegar-dokploy
> **Disco de pildora-01 (incidente 23-10).** Cada despliegue deja imagen (~3,5 GB Oscar HQ) y caché BuildKit (`docker system df` la cuenta mal). Mantenimiento automático: timer `docker-cleanup` diario (builder prune 5 GB, image prune >48 h, nunca volúmenes), `task-history-limit 2`, aviso Telegram al 80/90 %. Si el disco >90 %: `docker builder prune -a -f` y `docker image prune -a --filter until=24h -f`. Nunca `docker volume prune` ni `system prune --volumes`. Consulta `/salud` antes de encadenar despliegues.

## Tarjeta de verificación (tras instalar)
- [ ] `systemctl list-timers` muestra docker-cleanup y disk-alert
- [ ] `docker info` / `docker swarm inspect` → Task History Retention Limit: 2
- [ ] Ejecutar `docker-cleanup.sh` a mano; revisar log y `df -h /`
- [ ] Forzar alerta (umbral temporal) y ver el mensaje en Gestión
- [ ] `docker volume ls` idéntico antes/después
- [ ] Apps y dokploy-postgres sanos tras la limpieza
- [ ] /salud muestra el % de disco
