# Paquete de traspaso — sistema Telegram · Oscar HQ · Signal

Para una persona técnica nueva que va a ayudar a Oscar Alcántara a mejorar el sistema de carriles (Telegram → Hermes → kanban →
runner → PR → integrador → despliegue), Oscar HQ y Signal. Fecha de referencia: **21-10-2026**. Redactado desde el worktree
`docs/traspaso-2026-10-21` sobre `main` `b4ea602`, verificando cada afirmación contra código, configuración, git, tareas
programadas y la CLI de Railway; lo que no pude comprobar está marcado **[sin verificar]**.

| Archivo | Contenido |
|---|---|
| [01-mapa-del-sistema.md](01-mapa-del-sistema.md) | Repos y rutas, servicios (Railway, Vercel, Supabase, Hermes, runner, workers de Signal), quién corre dónde, flujo de una petición de punta a punta, dos diagramas Mermaid (flujo e infraestructura) |
| [02-bots-telegram.md](02-bots-telegram.md) | Los tres bots (`@oscarsaoBot` Hermes · `@pildora_carriles_bot` «Pildora - Trabajos» · `@PildoraD_BOT` «Oscar HQ Avisos»): para qué sirve cada uno, quién puede usarlo, comandos, límites y bugs conocidos |
| [03-runbooks.md](03-runbooks.md) | Reiniciar el runner (drain + humo + ruff F821), desplegar oscar-hq (`railway up` + `deployment list`), deploy atascado / caída, migraciones (solo con OK), dónde viven los secretos (sin valores) |
| [04-estado-y-deuda.md](04-estado-y-deuda.md) | Tests que fallan, caídas de Railway, lote diario, atascos por guard, conflicto CHANGELOG/LEDGER, RAM del PC y otros frentes abiertos, con evidencia |
| [05-glosario-y-decisiones.md](05-glosario-y-decisiones.md) | Glosario (carril, guard, lote, triage, LEDGER, signal_core…) y quién decide qué; reglas de autonomía vigentes |

Por dónde empezar: `CONTEXT.md` y `decisions/2026-09-27-arquitectura-carriles.md` (el contrato), `tools/agent-lanes/README.md` y
`lanes.yaml` (la realidad del código), `decisions/2026-10-15-mapa-reglas-sistema.md` (reglas de personas/bots) y
`~/.claude/CLAUDE.md` (las reglas de autonomía que mandan). Reglas de trabajo en este repo: nunca `checkout`/`stash` en
`C:/Users/oscar/oscar-command-hub` (hay 13 worktrees vivos; crea el tuyo en `dev/_hub-wt/`), commits con pathspec, nada de secretos.

## Discrepancias detectadas

Cada fila: afirmación tal como está escrita · fuente · qué encontré el 21-10 · qué habría que corregir. **No he corregido ninguno
de esos archivos**: eso es decisión de Oscar.

| # | Afirmación | Fuente | Qué encontré | Qué corregir |
|---|---|---|---|---|
| D1 | Hermes en Telegram «no tiene terminal ni ejecución de código»; «DM con Hermes = despacho (solo lectura). Sin `terminal`/`code_execution`» | `CONTEXT.md:59-60`; `decisions/2026-09-27-arquitectura-carriles.md` §4; memoria `project_orquestacion_carriles_2026-09-27` | `%LOCALAPPDATA%/hermes/config.yaml:1139` → `platform_toolsets.telegram` incluye **`terminal`** (y `browser`, `file`, `web`…). Backup `config.yaml.bak-20261012-terminal` y memoria 12-10 indican que se reactivó. `decisions/2026-10-15-arquitectura-bots-equipo.md` §1 ya lo describe así | Actualizar `CONTEXT.md` y añadir nota a la decisión del 27-09: «Hermes no implementa» es regla de proceso, no técnica; o volver a quitar `terminal` (decisión de Oscar) |
| D2 | «Solo Oscar aprueba: fusionar, desplegar, migraciones, variables… los botones y comandos responden "Solo Oscar" a cualquier otra persona»; tabla con *Producción* = solo Oscar «sin excepción por rol»; «no hay comandos de consulta por DM todavía» | `docs/equipo-bots.md` (13-10) § Regla de aprobación y § Notas; `decisions/2026-09-27-telegram-grupos-temas.md` § DM del equipo (11-10); `decisions/2026-10-15-mapa-reglas-sistema.md` §3.1 | Commit `a41da56` (20-10, t_2f7c99a3): `people.yaml` `operador: true` para **María** = mismo acceso que Oscar en el bot de Trabajos (comandos, botones, `/aprobar`, `/desplegar`, fusionar, migraciones); `commands.py:90` dice «Pide a Oscar o a María» | Reescribir `docs/equipo-bots.md` (fila de María y regla de aprobación) y añadir nota de fecha en las dos decisiones |
| D3 | `docs/equipo-bots.md` presenta dos bots: «Bot de carriles» y «**Bot de roles / Hermes**» (una sola fila para grupos) | `docs/equipo-bots.md` § Los dos bots | Son **tres** bots distintos: Hermes (`@oscarsaoBot`, PC), bot de carriles (`@pildora_carriles_bot`, PC) y bot del equipo/roles (`@PildoraD_BOT`, Railway). `decisions/2026-10-15-mapa-reglas-sistema.md` §4 y `oscar-hq/docs/TELEGRAM_BOTS_QUIEN_ES_QUIEN.md` los separan | Tabla de tres bots en `equipo-bots.md` (ver `02-bots-telegram.md`) |
| D4 | Carpeta `handoffs/` como registro vivo («`handoffs/` — traspasos entre sesiones/agentes»; «Un traspaso de sesión → `handoffs/<fecha>-<proyecto>.md`») | `README.md` del hub § 2; `AGENTS.md` § Rol y § Reglas de edición | `handoffs/` **no existe** en el repo (sí `archive/`, `decisions/`, `docs/`, `projects/`, `references/`, `specs/`) | Crear la carpeta con el primer handoff o quitar la referencia |
| D5 | «Carril del sistema… **Dependencias (por eso aún no está activo)**: `lanes.py restart --drain` lo aporta `feat/plan-d-runtime`. Mientras `lanes.py` no lo tenga… no hay botón»; «3. El coordinador añade `claude-hub` a `INTEGRATOR_LANES`» | `tools/agent-lanes/README.md:148-156`; comentarios en `lanes.yaml` (bloque `claude-hub` del integrador) | `lanes.py` tiene `restart --drain` desde `d0d4f7e` (28-09); `.env` vivo tiene `INTEGRATOR_LANES`; `.state/apply_result.json` existe; los PR #45-#49 del hub se fusionaron y aplicaron por ese flujo | Quitar la sección «Dependencias» del README y los comentarios obsoletos del `lanes.yaml` |
| D6 | Auditor diario: «Programarlo… `instalar_auditor.ps1`. **No está registrado.**» | `tools/agent-lanes/README.md:178-179` | Tarea «Auditor diario carriles» registrada; última ejecución 21-10 07:40, resultado 0 (`Get-ScheduledTaskInfo`) | Actualizar el README |
| D7 | `runner.max_workers: 1` «1 por defecto: hubo un OOM el 27-09» | `tools/agent-lanes/lanes.yaml:8-9`; `install-service.ps1` (1 por defecto) | El servicio arranca con **`max_workers=2`** (`.state/runner.log` 20-10 04:24 y 17:12: `runner: carriles=[…] max_workers=2`); el README §Otros ya lo admite como pendiente desde el 30-09 | Reinstalar la tarea sin `-MaxWorkers` (Oscar) o subir `lanes.yaml` a 2 a sabiendas; mientras, el RAM al 90 % del 21-10 es consecuencia |
| D8 | Tabla de proyectos: VPDN `C:\tmp\vpdn-marketplace` **Activo**, FinTrade `C:\Users\oscar\fintrade-mvp`, Checker `C:\Users\oscar\migrateam-checker`; tabla GIT con esas rutas | `C:/Users/oscar/CLAUDE.md` § MAPA DE PROYECTOS y § GIT | Ninguna de las tres rutas existe (la cabecera del mismo archivo lo reconoce, pero las tablas siguen sin marcarlo y VPDN consta «Activo») | Marcar «solo en E:/GitHub, no en disco» o quitarlas de las tablas |
| D9 | Vercel deploy: `/c/Users/oscar/AppData/Local/nvm/v24.5.0/node "/c/Users/oscar/AppData/Roaming/npm/node_modules/vercel/dist/index.js" …`; «# Node v24.5.0» | `~/.claude/CLAUDE.md` § ENTORNO (bloque Vercel deploy); `C:/Users/oscar/CLAUDE.md` § ENTORNO | `/c/Users/oscar/AppData/Local/nvm/v24.5.0/node` **no existe**; `node` en PATH es v24.19 (`C:\Program Files\nodejs`). La nota de cabecera ya lo dice, pero el comando sigue con la ruta muerta | Sustituir por `"/c/Program Files/nodejs/node"` en ambos archivos |
| D10 | «Production: Vercel auto-deploy · **Railway auto-deploy** · master»; «Merge to master → Vercel + Railway auto-deploy»; dashboard de producción `dashboard-steel-ten-43.vercel.app` | `oscar-hq/DEPLOY.md` § Environments y § Git Workflow | Railway de Oscar HQ **no** está conectado a GitHub: deploy manual `railway up` (`C:/Users/oscar/CLAUDE.md`, `lanes.yaml` `deploy: railway_up`, memoria). El dominio del dashboard según memoria es `app.pildoradigital.com` **[sin verificar]** | Reescribir `DEPLOY.md` |
| D11 | «Status Oscar HQ: … **5 bugs P0 pendientes (ver HANDOFF)**»; «REALIDAD ACTUAL (Mayo 2026)»; «HANDOFF debe caber en 1 página… si dura >1 semana sin actualizar, está muerto» | `oscar-hq/CLAUDE.md:48-50, 110-111`; cabecera de `docs/kb/HANDOFF_PROXIMA_SESION.md` | El HANDOFF tiene >760 líneas, su «Estado actual» es del 22-09 con un bloque del 15-10 encima; no hay lista de «5 bugs P0» vigente | Cortar el HANDOFF o cambiar la regla; actualizar «REALIDAD ACTUAL» |
| D12 | `docs/kb/archivo/` «← Ledger, ADRs e inventarios»; «para escribir en los ledgers vigentes usa `docs/kb/archivo/CHANGELOG.md`…» seguido de «`docs/kb/LEDGER_ESTATUS.md` es la fuente única… **corrige la línea de arriba**» | `migrateam/CLAUDE.md:385-398` | `LEDGER_ESTATUS.md` vive en `docs/kb/`, no en `archivo/`; el CHANGELOG real es `docs/kb/archivo/CHANGELOG.md` y `docs/kb/CHANGELOG.md` es un stub; el CLAUDE.md contiene su propia fe de erratas en vez de la regla corregida | Decidir el modelo (CHANGELOG = historia, LEDGER = estado) y reescribir ese bloque (ver `04` §5) |
| D13 | Hub: auditoría e2e cita `docs/kb/LEDGER_ESTATUS.md` y `docs/kb/archivo/HALLAZGOS_PENDIENTES.md` como ficheros de **oscar-hq** | `references/2026-09-27-auditoria-e2e-oscar.md:121-123` | En oscar-hq no existe ningún `LEDGER_ESTATUS.md`; esos ficheros son de MigraTeam | Corregir la referencia o anotar que la auditoría miró el repo equivocado |
| D14 | Regla 28-09: «un hotfix directo a master se devuelve a develop **el mismo día**» | `decisions/2026-09-28-ramas-bandeja-monitor.md` §1 | `gh api compare develop...master` (21-10): master ahead 1 / behind 3; el monitor avisa «develop va 1 commit por detrás de master» desde al menos el 21-10 09:52. El hotfix `07e37e3e5` (25-09) sigue sin devolverse | Abrir el PR master→develop (lo hace el integrador con OK de Oscar) |
| D15 | W1: «repo nuevo `C:\Users\oscar\dev\agent-lanes`» | `decisions/2026-09-27-arquitectura-carriles.md` §6 W1 | El runner vive en `oscar-command-hub/tools/agent-lanes` (único código del hub, `AGENTS.md`) | Nota en la decisión |
| D16 | Tema **Atención** con @mención y recordatorio 24 h (PR #48/#43, fusionados) | `tools/agent-lanes/README.md` § Aviso 🧊; commits `b0e9888`, `61c06ee` | El bloque `attention_telegram:` **no está** en el `lanes.yaml` del checkout vivo → la función está inactiva | Añadir el bloque (chat/thread del tema Atención, usuario a mencionar) y aplicar |
| D17 | «Hermes (DM) es solo de Oscar. `TELEGRAM_ALLOWED_USERS` no cambia» | `tools/hermes-config/DM_POLICY_SKILL.md` § Contexto (11-10) | `autorizar_maria.sh` (28-09) añade a María a `TELEGRAM_ALLOWED_USERS`; memoria 11-10 «María acceso total por DM a los bots». Cuántos ids hay en la variable: **[sin verificar]** (no leí el `.env`) | Alinear el texto con la decisión del 11-10 |
| D18 | «El carril `codex-*`», «`codex-oscarhq`, `codex-migrateam`» como carriles del contrato | `decisions/2026-09-27-arquitectura-carriles.md` §5 | No existen en `lanes.yaml`; `CONTEXT.md` ya dice «Codex: EN PAUSA» | Nota en la decisión (coherente, solo histórico) |
| D19 | Memoria: humo del runner = `timeout 90 py -3.12 runner.py --all --log-file /tmp/x.log --max-workers 2` «desde el checkout raíz» antes del restart | memoria `feedback_runner_restart_smoke_test` | Con el runner-servicio vivo, ese comando sale en `runner.py:85-87` («ya hay un runner vivo; salgo») justo después de «contrato hermes OK»: solo prueba importaciones + contrato, no el bucle. La humo completa solo es posible con el runner parado | Precisar la memoria/runbook (ver `03` §1) |
| D20 | «`health_url: https://oscar-hq-production.up.railway.app/api/health`» | `tools/agent-lanes/lanes.yaml:277`; `tools/monitor/monitor.py:51` | `railway status` muestra como URL del servicio `https://api.pildoradigital.com`; ambas pueden ser válidas **[sin verificar]** | Comprobar que las dos resuelven al mismo servicio; unificar |

## Preguntas que no pude resolver

1. **¿A qué se refiere «los 5 tests que fallan de base»?** El hub está en verde (1558/1558) y oscar-hq tiene 68 fallos + 1 error
   de importación en `master`. Hace falta que Oscar diga qué suite y qué 5.
2. **¿Cuál es «la caída de Railway» del encargo?** Documento cinco incidentes (15-09 worker, 25-09 MigraTeam 502, 01-10 Oscar HQ por
   Supabase, 15-10 handlers bloqueantes, 21-10 deploy FAILED). El deploy FAILED de hoy (09:13, `3799bcb5…`) no tiene causa
   identificada: no pude leer su log con la CLI.
3. **¿Qué rama despliega Railway para `icp-prospect-engine` y `signal-worker-pildora`?** `lanes.yaml` dice «desde la rama base»
   (`cambio/multiverse-benchmark-harness`); `railway status` no muestra la rama. Comprobar en el panel de Railway.
4. **¿Cuántas personas tiene `TELEGRAM_ALLOWED_USERS` de Hermes?** No leí el `.env` de Hermes; solo consta que el script de María
   existe. Comprobar con `add_allowed_user.py` (imprime solo ids).
5. **¿`max_workers` debe ser 1 o 2?** La config dice 1, el servicio corre con 2 y la RAM llega al 90 %. Decisión de Oscar antes de
   reinstalar la tarea.
6. **¿Dónde está desplegado el dashboard de Oscar HQ (`app.pildoradigital.com` vs alias de `DEPLOY.md`) y con qué Root Directory?**
   La memoria `project_oscarhq_local_dev_gotchas` dice que el proyecto Vercel intenta empaquetar el backend desde la raíz.
7. **¿El mini PC Minisforum aloja ya algo?** Ninguna evidencia en los repos; la fase G parte de un inventario que no existe.
8. **Modelo CHANGELOG/LEDGER para MigraTeam** (ver D12) y si oscar-hq debe adoptar un ledger.
9. **¿Qué hacer con los 29 worktrees `lane-*` y las ramas locales con commits sin empujar** que el runner conserva en `dev/_lanes`?
   Borrarlos es una acción que requiere confirmación.
10. **Railway Config as Code deprecado (hasta 2026-12-01):** ¿migrar `railway.json` de oscar-hq a `.railway/railway.ts`?
