---
name: desplegar-dokploy
description: Despliega una app o un compose de pildora-01 (Dokploy) y verifica que corre el código nuevo. Úsala para publicar en el servidor un merge ya aprobado (Oscar HQ, NextJobs, Signal, OpenDesign, Postiz) o para reaplicar variables de entorno. Producción requiere OK previo de Oscar.
---

# Desplegar en Dokploy (pildora-01)

Solo con OK explícito de Oscar para producción (CLAUDE.md global). Nunca imprimas secretos.

## Antes
1. Túnel SSH al panel (en segundo plano si no está abierto):
   `ssh -i ~/.ssh/id_ed25519_hetzner -o ServerAliveInterval=30 -N -L 3300:127.0.0.1:3000 root@2.28.20.203`
   API en `http://localhost:3300/api/...`, cabecera `x-api-key` = contenido de `~/.dokploy-token` (léelo con `tr -d '\r\n '`; nunca lo muestres). Panel web: https://panel.pildoradigital.com.
2. Identifica el recurso con `GET /api/project.all` (proyectos «Pildora» y «MigraTeam»):
   - Oscar HQ: application `ySwFi64yQx178z9dBoKq8`
   - OpenDesign: compose `Fho00pG-1pcD4LvkpBl-f`
   - Postiz: compose `dEmrl3t6UIKVq-wgX-1WV`
   - MigraTeam: backend-prod `2ZFE7_x-2MURejAG3Ey7y`, cron-prod `SUpMj8NqufH2YR6b-QVZ0`, staging `Mfim9xPHZe8OKSKAUFxqb`
3. Anota el SHA que debe quedar desplegado (`git rev-parse origin/<rama>`).

## Elegir la operación (incidente 22-10: se confundieron)
- **Código nuevo** → `POST /api/application.deploy` (o `compose.deploy`) con `{"applicationId": "...", "title": "<PR o motivo>"}`.
- **Solo variables** → `POST /api/application.redeploy` (o `compose.redeploy`). `redeploy` NO trae código nuevo.
- **Cambiar variables:** lee el env justo antes de escribir (read-modify-write: otra sesión puede haberlo tocado), respeta las referencias `${{project.X}}` y guarda con `application.saveEnvironment` / `compose.update`. Variables de proyecto compartidas: `project.all` → `env` del proyecto.

## Esperar y verificar (el health no basta)
1. Sondea `GET /api/application.one?applicationId=...` hasta que el despliegue más reciente (ordena por `createdAt`) tenga tu `title` y `status` = `done`; si es `error`, mira los logs. Usa Monitor, no `sleep` en primer plano.
2. Health: `https://api.pildoradigital.com/api/health` → 200. Ojo: solo prueba que algo responde, no que sea la versión nueva (igual que en Railway).
3. Versión: dentro del contenedor (`ssh -i ~/.ssh/id_ed25519_hetzner root@2.28.20.203 'docker ps --format "{{.Names}}"'`) comprueba un fichero o ruta que solo exista en el commit nuevo.
4. Prueba funcional mínima del cambio (endpoint o pantalla).
5. Si un frontend de Vercel depende del merge: `node "C:/Users/oscar/AppData/Roaming/npm/node_modules/vercel/dist/index.js" ls <proyecto> --prod --scope oscarsaos-projects` → Ready después del merge.

## Al terminar
Informa: recurso, SHA, estado, verificaciones y cómo lo prueba Oscar (URL + pasos). Si eres el integrador, actualiza `tools/agent-lanes/.state/integrador_estado.md`.
