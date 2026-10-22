# Skills: inventario y propuesta (22-10-2026)

**Estado (22-10, OK de Oscar con botones):** las 5 de esta carpeta (`desplegar-dokploy`, `integrar-lote`, `barrido-carriles`, `tarjeta-carril`, `migracion-supabase`) están **instaladas** en `~/.claude/skills/<nombre>/` del PC de Oscar. Esta carpeta `skills/` es la fuente versionada: si se edita aquí, volver a copiar a `~/.claude/skills/`.

**Carriles:** los workers de agent-lanes son sesiones `claude -p` del mismo usuario de Windows, así que cargan las skills de usuario (`~/.claude/skills`) igual que las sesiones interactivas: no hace falta copiarlas a cada repo. Para que en modo `-p` puedan invocarlas sin permiso interactivo, `lanes.yaml` (defaults.allowed_tools) incluye `Skill(integrar-lote)` y `Skill(tarjeta-carril)`. El guard de cada carril sigue mandando: un worker nunca fusiona a main/master ni despliega aunque la skill lo describa (esos pasos son del integrador).

Borradores pendientes de aprobar: `../skills-borrador/` (no instalados).

## Inventario

Uso medido en los transcripts de Claude Code de los últimos 30 días (1.508 sesiones, contando invocaciones reales de la skill o del subagente, no menciones).

| Pieza | Tipo | Para qué | Usada | Desactualizada |
|---|---|---|---|---|
| audit-360 | skill usuario | Auditoría CTO vía subagente | no | no |
| cerrar-tarea | skill usuario | Cerrar tarea tomada a mano (verificación del runner) | no | no |
| distill | skill usuario | Lecciones a `~/.claude/memory/lessons.jsonl` | no | sí (gotcha de nvm ya resuelto; la memoria real vive en `projects/.../memory`) |
| handoff | skill usuario | HANDOFF.md al cerrar sesión | 1 sesión | no |
| healthcheck | skill usuario | Estado de todos los proyectos | no | **sí** (hace ping a Railway para OHQ y MigraTeam) |
| pre-deploy | skill usuario | Gate advisory antes de push | no | no |
| spec-lite | skill usuario | Spec de 1-3 días | no | no |
| tomar-tarea | skill usuario | Coger tarjeta del kanban desde consola | no | no |
| synced | carpeta | Sincronización de skills (contenido opaco) | — | — |
| code-reviewer | subagente | Revisión de diff | **sí (42)** | no |
| security-auditor | subagente | Auditoría de seguridad | **sí (20)** | no |
| db-migration-checker | subagente | Migraciones Alembic | **sí (15)** | **sí** (habla de «push a Railway») |
| code-quality-reviewer | subagente | Mantenibilidad | sí (3) | no |
| sprint-planner | subagente | Modo OASP y spec | sí (3) | no |
| cto-audit-360 | subagente | Auditoría 360 | no | leve (cita Railway como IaC) |
| propuesta-comercial | subagente | Propuestas a clientes | no | no |
| content-cycle, weekly-analytics, distill, handoff, healthcheck | skills de pildora-cmo-workspace | Rol CMO (Ingerber) | no medible aquí | revisar |
| resend, vercel, cloudflare, sentry-cli | plugins activos | Docs y CLI de esos servicios | sí (vercel, cloudflare) | no |
| Hermes skills (apple, creative, devops/sdlc-review, email, media, productivity/pdf, docx, xlsx, google-workspace…, social-media, web) | skills de Hermes | Catálogo de Hermes | no medido | no tocar sin OK |

**Lectura:** se usan mucho los subagentes y casi nada las skills. Las skills existentes describen el método OASP, pero el trabajo real de estas semanas (desplegar, integrar, migrar, barrer carriles, crear tarjetas, rotar claves) se ha hecho a mano cada vez, con los mismos tropiezos.

## Top 10 propuesto

| # | Nombre | Disparador | Qué evita (incidentes reales) | Dónde | Forma |
|---|---|---|---|---|---|
| 1 | desplegar-dokploy | Publicar un merge aprobado o reaplicar env | deploy vs redeploy confundidos (22-10); health que no prueba la versión | usuario | skill (borrador) |
| 2 | integrar-lote | Oscar aprueba integrar PR | `npm ci` en dashboard pnpm (19 h de rebases falsos); autor agent-lanes bloqueado por Vercel | usuario + hub | skill (borrador) |
| 3 | barrido-carriles | Inicio de sesión, «qué tengo pendiente», tras cada bloque | 37 tarjetas ruidosas, triage del que no se sale, repreguntas | usuario | skill (borrador); luego `/pendiente` del bot (t_d6ff8363) |
| 4 | tarjeta-carril | Crear cualquier tarjeta de carril | 36 bloqueos LIMITACION-GUARD/semana, falta Rama-origen, backticks ejecutados en bash | usuario + hub | skill (borrador) |
| 5 | migracion-supabase | Aplicar migración aprobada de OHQ | prefijo duplicado (20261038), MCP de Supabase caído | usuario | skill (borrador) |
| 6 | rotar-clave | Rotar y propagar una clave (o pasarla a la Bóveda) | rotación manual post-incidente; claves en 5 sitios; .bak con secretos | usuario | skill (cuando la Bóveda esté en master) |
| 7 | verificar-bot-telegram | Un bot no responde o responde raro | webhook con secreto distinto tras migración; token de carriles confundido con el de Hermes | usuario | skill |
| 8 | smoke-produccion | Tras desplegar algo con UI | bugs P0 que solo salen en prod (memoria 26-07) | usuario | skill (Chrome) |
| 9 | alta-persona-equipo | Entra alguien (Angel, Andrea…) | people.yaml válido pero Hermes la ignora; grupos, OHQ user, Drive | usuario | skill |
| 10 | revisar-correo-infra | Diagnóstico de caídas o al empezar el día | errores de Vercel/Railway que solo llegaron por correo | usuario | skill (Gmail MCP) |

## Actualizar o retirar
- **Actualizar:** `healthcheck` (Railway → Dokploy y api.pildoradigital.com), `db-migration-checker` (Railway → arranque en Dokploy; MigraTeam sigue en Railway hasta su corte), `distill` (ruta de memoria real y quitar el gotcha de nvm), `cto-audit-360` (infra actual).
- **Mantener sin cambios:** tomar-tarea, cerrar-tarea, spec-lite, pre-deploy, handoff, audit-360.
- **Retirar:** ninguna por ahora (no estorban); revisar `synced`.

## Borradores pendientes de aprobar (22-10)
En `skills-borrador/` (no instalados): `verificar-bot-telegram`, `smoke-produccion`, `alta-persona-equipo`, `revisar-correo-infra` y `rotar-clave` (solo esqueleto: esperar a que la Bóveda, oscar-hq PR #254, esté en master).

## Actualizadas el 22-10 (fuera del repo, en `~/.claude`, con copia `.bak-20261022`)
`skills/healthcheck` (Railway → Dokploy y api.pildoradigital.com; MigraTeam en Railway hasta su corte), `skills/distill` (sin el gotcha de nvm; lecciones duraderas también a la memoria real `projects/C--Users-oscar/memory/`), `agents/db-migration-checker.md` y `agents/cto-audit-360.md` (host actual).
