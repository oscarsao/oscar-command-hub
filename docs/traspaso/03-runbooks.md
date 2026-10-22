# 03 · Runbooks

Fecha de referencia: 21-10-2026. Rutas absolutas; Windows 11 + Git Bash; Python siempre `py -3.12`. Nada de lo que hay aquí
se ejecuta sin el OK de Oscar cuando toca producción, `main`/`master`, migraciones, borrados o variables de entorno
(`~/.claude/CLAUDE.md` § AUTONOMÍA). **Checkout vivo** = `C:/Users/oscar/oscar-command-hub` (rama `main`, el que ejecuta el
runner). Nunca `git checkout`/`git stash` ahí: hay 13 worktrees vivos; para trabajar, crear el tuyo en `C:/Users/oscar/dev/_hub-wt/<nombre>`.

## 1. Reiniciar el runner de carriles (reinicio ordenado)

Por qué es delicado: el 27-09 un `schtasks /End` con workers vivos dejó uno huérfano y duplicó otro (docstring de
`agent_lanes/drain.py`); el 11-10 el PR #12 pasó 1034 tests y rompió `main()` del runner (`NameError: STATE_DIR`), el restart
paró el runner viejo y el nuevo murió **en silencio** (pythonw no muestra stderr) durante ~16 min (memoria
`feedback_runner_restart_smoke_test`, arreglado en PR #14).

```bash
LIVE=C:/Users/oscar/oscar-command-hub/tools/agent-lanes

# 0. Estado antes de tocar nada
py -3.12 "$LIVE/lanes.py" status            # runner VIVO/parado, carriles ocupados
tail -5 "$LIVE/.state/runner.log"

# 1. Lint de nombres no definidos (lo que rompió el 11-10) sobre el código que se va a ejecutar
py -3.12 -m ruff check --select F821 C:/Users/oscar/oscar-command-hub/tools/agent-lanes

# 2. Suite completa (≈2,5 min; 1558 tests en verde el 21-10 sobre main b4ea602)
cd C:/Users/oscar/oscar-command-hub && py -3.12 -m pytest tools/agent-lanes/tests -q -p no:cacheprovider

# 3. Prueba de humo en primer plano (~60-90 s): debe llegar a "contrato hermes OK" y "runner: carriles=[...]"
#    OJO: si el runner-servicio sigue vivo, el segundo proceso ve .state/runner.lock y sale con
#    "ya hay un runner vivo; salgo" ANTES de llegar al bucle (runner.py:85-87). Lo que sí comprueba siempre es
#    importaciones + contrato de la CLI de hermes. Para una humo completa hay que hacerlo con el runner parado
#    (es lo que hace el propio restart) o usar al menos `--check`:
cd "$LIVE" && py -3.12 runner.py --check
cd "$LIVE" && timeout 90 py -3.12 runner.py --all --log-file "$TEMP/humo.log" ; tail -5 "$TEMP/humo.log"

# 4. Reinicio ordenado (drain): deja de reclamar, espera a que acaben workers/deploys/gates (hasta --timeout s),
#    `schtasks /End` + `schtasks /Run` de la tarea "agent-lanes runner" y comprueba que arranca uno nuevo.
#    Además hace `git fetch` y `merge --ff-only origin/main` del checkout vivo (no reinicia si hay cambios locales).
cd "$LIVE" && py -3.12 lanes.py restart --drain --timeout 3600
#    (`lanes.py reload` es alias). Nunca `schtasks /End "agent-lanes runner"` a pelo.

# 5. Verificar
py -3.12 "$LIVE/lanes.py" status                 # "runner: VIVO (pid N)" con pid NUEVO
tail -3 "$LIVE/.state/runner.log"                # debe avanzar; buscar "runner: carriles=... max_workers=N"
powershell -NoProfile -Command "Get-ScheduledTaskInfo 'agent-lanes runner' | Select LastRunTime,LastTaskResult"
cat "$LIVE/.state/apply_result.json"             # {ok, sha, reason, at} del último restart
```

Notas:
- Desde Telegram el mismo reinicio es el botón `[🔁 Aplicar (reinicio ordenado)]` que aparece tras fusionar un PR de `claude-hub`.
- Un `.state/drain.json` de más de 2 h se ignora y se borra al arrancar (un restart muerto a medias no bloquea los carriles).
- Si el runner murió igualmente, al arrancar mata a los `claude` huérfanos (`worker_pid` en `.state/tasks/<id>.json`) y bloquea
  sus tareas; `lanes.py status` muestra «huérfano» solo con el runner parado.
- El **vigilante de runner ciego** (`ciego.py`) hace un `restart --drain` automático (máx. 1/hora) si durante 5 min no se
  pueden listar tareas (p. ej. `WinError 4551` de App Control); aviso ALTA al DM.
- `max_workers`: el argumento `--max-workers` del servicio manda sobre `lanes.yaml` (= 1). El servicio **sigue registrado con 2**
  (`runner.log` 20-10: `max_workers=2`). Alinear = reinstalar la tarea sin `-MaxWorkers` (`install-service.ps1`, lo hace Oscar).
- Los hooks que rigen a un worker son los del **checkout vivo** (`render_settings` usa el ROOT del runner): un cambio en
  `contract/` no manda hasta fusionar y aplicar.

## 2. Desplegar oscar-hq (backend en Railway)

Hechos: GitHub **no** está conectado a Railway a propósito (deploy manual mientras la herramienta está en desarrollo;
`C:/Users/oscar/CLAUDE.md` § Oscar HQ). `railway up` devuelve en segundos pero el build (deps Python pesadas) tarda minutos;
`/api/health` responde 200 **con el contenedor viejo**, así que no demuestra nada (memoria
`feedback_railway_deploy_healthcheck_false_positive`, incidente 22-07). Ids del servicio en `lanes.yaml:273-277`
(`railway_project 34da294f…`, `railway_service 8dcda4bf…`, `railway_environment 3236716d…`).

```bash
RW=/c/Users/oscar/AppData/Roaming/npm/node_modules/@railway/cli/bin/railway.exe   # CLI 5.59.0, enlazada en oscar-hq
cd C:/Users/oscar/dev/oscar-hq && "$RW" status                 # Project "Oscar HQ" · service "Oscar HQ" · production
"$RW" deployment list | head -5                                # SUCCESS / FAILED / BUILDING

# Vía recomendada: botón [🚀 Desplegar] del bot de Trabajos (solo tras INTEGRADO; integrator.py hace exactamente esto):
#   worktree limpio del commit fusionado → `railway up --detach` con ids explícitos → sondeo del deployment hasta SUCCESS/FAILED (15 min).

# Vía manual (con OK de Oscar), desde un worktree LIMPIO del commit que se quiere desplegar (sube el directorio actual):
git -C C:/Users/oscar/dev/oscar-hq worktree add C:/Users/oscar/dev/_oh-wt/deploy-<fecha> origin/master
cd C:/Users/oscar/dev/_oh-wt/deploy-<fecha> && "$RW" up --detach \
   --project 34da294f-afdf-47f9-8ea9-5ffe3551e8f0 --service 8dcda4bf-5a6f-49f5-aa75-b0b38f82e9ed \
   --environment 3236716d-c75a-41fe-b426-9953deb7e426
# Esperar hasta que la ÚLTIMA fila sea SUCCESS (no BUILDING ni DEPLOYING):
"$RW" deployment list | head -3
"$RW" logs --build | tail -40        # si FAILED
# Solo después: curl https://api.pildoradigital.com/api/health  (2ª señal, nunca la única)
```

`.railwayignore` (17 líneas) excluye `node_modules`, `.next`, venvs, `output/_system/`, `memory/sessions/`, `.claude/`, PDFs y
«archivos personales sueltos en el root». Build = NIXPACKS; start = `python -m uvicorn src.api.main:app` (`railway.json`,
`Procfile`). Aviso de la CLI el 21-10: **Config as Code (`railway.json`) está deprecado; sigue funcionando hasta 2026-12-01**
(`railway config migrate`) — decisión pendiente de Oscar.

Dashboard (Next.js): se despliega en Vercel al fusionar en `master` (`HANDOFF_PROXIMA_SESION.md` 15-10). Orden al tocar API +
UI: **backend primero** (aditivo) y luego el PR del dashboard.

### Estado el 21-10
`railway status`: `Online · Deploy failed (2h 25m)`. `deployment list`: `3799bcb5… FAILED 09:13:22` tras `4abf67ca… SUCCESS 08:57:16`;
el tráfico lo sirve el SUCCESS anterior. No pude leer el log del fallo con la CLI (`railway logs --deployment <id>` solo
devolvió el aviso de deprecación). Primer paso para quien llegue: `"$RW" logs --build` o el panel de Railway → deployment
`3799bcb5`.

## 3. Deploy atascado o caída de Railway

1. **Diagnóstico sin tocar nada**
   ```bash
   "$RW" status; "$RW" deployment list | head -5; "$RW" logs | tail -60     # oscar-hq
   cd C:/Users/oscar/dev/Scraper && "$RW" status                            # respectful-success (2 servicios)
   curl -s https://api.pildoradigital.com/api/health; curl -s https://api.migrateam.com/health   # MigraTeam expone commit_sha y alembic_version
   tail -40 C:/Users/oscar/oscar-command-hub/tools/monitor/.state/alerts.log      # alertas del monitor (health-*, db-down, db-slow)
   ```
   `/salud` y `/falta <marca>` en el bot de Trabajos resumen lo mismo desde el móvil.
2. **Si la API de Oscar HQ no responde o va lenta, mirar primero Supabase**: el 30-09 y el 01-10 la base (`kkkxptlwknyafbavajhj`,
   Micro, 60 conexiones) se saturó y Oscar HQ estuvo caído de 09:40 a 11:30 (`docs/monitor-db-supabase.md`). El monitor guarda
   fotos en `tools/monitor/db_snapshots/AAAA-MM-DD.jsonl` (mirar los mayores consumidores **antes** de reiniciar la base, porque
   el reinicio borra `pg_stat_statements`). Reinicio del proyecto Supabase = acción de producción → OK de Oscar.
   El 15-10 se encontró una causa de app: 102 handlers `async def` sin `await` bloqueaban el bucle (`src/api/en_hilo.py`);
   quedan ~430 handlers con el mismo patrón (`docs/kb/CHANGELOG.md` 15-10).
3. **Deploy FAILED en Railway**: leer el build log; causas vistas: dependencia con rango abierto (MigraTeam 25-09: SQLAlchemy 2.1
   cambió a `psycopg` v3 → `ModuleNotFoundError`, backend y cron caídos desde 17:57 UTC hasta el hotfix `07e37e3e5` que fijó
   `sqlalchemy<2.1.0`); «incidente Railway» el 15-09 con `signal-worker-pildora` (deploy FAILED hasta relanzar `railway up`,
   CHANGELOG 15-09). Si el fallo es de infraestructura, relanzar el deploy (OK de Oscar); si es de código, hotfix por el flujo
   normal (en MigraTeam: hotfix a `master` se devuelve a `develop` el mismo día).
4. **Deploy que no avanza (BUILDING largo)**: esperar hasta `deploy_timeout_seconds` (15 min, el integrador lo sondea); si sigue,
   `"$RW" deployment list`, cancelar desde el panel y relanzar. Nunca dar por bueno un `/health` verde.
5. **MigraTeam, tras cualquier push a `master`**: verificar los **dos** deploys (Railway y Vercel) y que
   `SELECT version_num FROM alembic_version` coincide con el head del repo (`migrateam/CLAUDE.md:540-550`). Si Railway arranca con
   mismatch de Alembic → **parar y escalar a Oscar**, nunca `stamp`.

## 4. Aplicar migraciones (solo con OK explícito de Oscar)

| Repo | Mecanismo | Quién / cómo | Regla |
|---|---|---|---|
| **oscar-hq** | SQL en `supabase/migrations/*.sql` (123 ficheros el 21-10; última `20261021_signal_catalog_sync_job.sql`). **Manuales**: el integrador las detecta (`manual_migrations: [supabase/migrations/]`) y no da botón de deploy hasta `✅ Migración aplicada` | Oscar (o la sesión interactiva con su OK) aplica el SQL con el MCP de Supabase `apply_migration` (proyecto `kkkxptlwknyafbavajhj`) o el SQL Editor; luego botón `✅ Migración aplicada` en la ficha o `py -3.12 lanes.py integrator migration-applied <task_id>` | Migraciones aditivas e idempotentes; RLS activa en tablas nuevas `ohq_*`; nunca en el panel ni en variables |
| **MigraTeam** | Alembic (`backend/alembic/versions/`). Railway ejecuta `alembic upgrade head` al arrancar (start command). Fusionar en `develop` aplica en **staging**; `master` en producción | Un **único propietario** de `alembic/versions/` por release (claim en `.coordination/claims/`, `migrateam/AGENTS.md` §7); el carril `claude-migrateam` tiene esa ruta **vetada** (`lanes.yaml`); el integrador exige un solo head (análisis estático con `ast`) | Antes de push: `SELECT version_num FROM alembic_version` en el destino. Drift → parar y escalar. **Nunca** `stamp`, `UPDATE alembic_version`, borrar ni editar migraciones aplicadas |
| **Scraper** | SQL en `signal_core` (p. ej. `20261001_signal_core_multiverse.sql`, ya aplicada en prod el 13-10) | Oscar con `!` / scripts que leen la service-role del `.env` sin imprimirla | Ídem |

Herramienta `db-migration-checker` (subagente) **siempre** antes de un push que toque `alembic/versions/`.

## 5. Secretos: dónde viven (solo nombres, nunca valores)

| Dónde | Variables (nombre) | Notas |
|---|---|---|
| `C:/Users/oscar/oscar-command-hub/tools/agent-lanes/.env` (gitignored; 8 claves el 21-10) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_THREAD_ID`, `TELEGRAM_ALLOWED_CHATS`, `KANBAN_BASE_URL`, `CARRILES_BOT_TOKEN`, `INTEGRATOR_ENABLED`, `INTEGRATOR_LANES` | Opcionales reconocidas por el código: `OWNER_TELEGRAM_ID`, `BOARD_GUARD_APPLY`, `BOARD_GUARD_MINUTES`, `HERMES_HOME`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_PROJECT_REF`, `SUPABASE_MONITOR_RO_DSN` (monitor; también puede ir en `tools/monitor/.env`). Los `.ps1` instaladores y `.env` están fuera del alcance de los workers; `guardar_token_bot.py` escribe el token sin imprimirlo |
| `%LOCALAPPDATA%/hermes/.env` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`, `TELEGRAM_HOME_CHANNEL`, `TELEGRAM_HOME_CHANNEL_THREAD_ID` + claves de proveedores | `config.yaml` del mismo directorio **contiene secretos en claro** (hash del panel, secret del dashboard, API key de ClickUp): no volcarlo; pendiente rotar |
| `C:/Users/oscar/dev/oscar-hq/.env` y `dashboard/.env.development` | `API_SECRET_KEY`, `SUPABASE_*`, `ANTHROPIC_API_KEY`, `NEXT_PUBLIC_API_KEY`, `AUTH_*`… | El backend local exige `X-API-Key`: copiar `API_SECRET_KEY` a `NEXT_PUBLIC_API_KEY` del dashboard (memoria `project_oscarhq_local_dev_gotchas`) |
| **Railway** (variables del servicio) | Oscar HQ: lista en `oscar-hq/DEPLOY.md` § Backend (`ANTHROPIC_API_KEY`, `API_SECRET_KEY`, `SUPABASE_*`, `MAILGUN_*`, `RAILWAY_TOKEN`…); Scraper: `APOLLO_API_KEY`, `MIGRATEAM_SUPABASE_*`…; MigraTeam: `DATABASE_URL`, Stripe… | Cambiar una variable = confirmación de Oscar. La clave de Apollo se filtró en junio y se rotó en local el 13-10 (Railway ya tenía la de pago) |
| **Vercel** (env del proyecto) | `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_API_KEY`, `AUTH_SECRET`, `AUTH_USERNAME`, `AUTH_PASSWORD` (dashboard) | Ídem |
| **Supabase** (Oscar HQ) | Tokens de bots cifrados en `ohq_telegram_configs.bot_token_encrypted`; `HANDBOOK_GITHUB_TOKEN` (lectura) | Nunca en el panel en claro |
| Credenciales de CLIs | `gh` (`/c/Program Files/GitHub CLI/gh.exe`, usuario `oscarsao`), Railway, Vercel: en `%APPDATA%`/`%LOCALAPPDATA%` | Raíces protegidas en lectura para los workers ops (`ops_guard.py`) |

Reglas: ningún secreto en commits, logs ni Telegram (el integrador solo publica `archivo:línea` y nombre de regla del secret-scan);
si aparece uno en código → alertar antes de cualquier otra acción (`~/.claude/CLAUDE.md` § SEGURIDAD). Un subagente volcó una vez
todas las env vars de prod a disco (memoria `feedback_agents_no_secrets_to_disk`): no se repite.

## 6. Otras operaciones frecuentes

| Qué | Comando |
|---|---|
| Ver estado de carriles | `py -3.12 C:/Users/oscar/oscar-command-hub/tools/agent-lanes/lanes.py status` |
| Tomar/cerrar una tarea desde una consola interactiva | `/tomar-tarea t_xxx` y `/cerrar-tarea t_xxx` (skills en `~/.claude/skills`) = `lanes.py take|close` |
| Forzar reevaluación de un PR del integrador | `py -3.12 lanes.py integrator reset <task_id>` |
| Prueba en seco del integrador / de un lote | `py -3.12 -m agent_lanes.integrator --dry-run [--lane claude-oscarhq --pr N]` · `--batch 12,15,18` |
| Tarjetas en el tablero equivocado | `py -3.12 lanes.py doctor-boards` (dry-run) / `--apply` |
| Reenviar avisos pendientes con botones | `py -3.12 lanes.py renotify [--lane X] [--task t_id] [--dry-run]` |
| Auditor diario a mano | `py -3.12 tools/agent-lanes/auditor.py --dry-run` |
| Monitor, una foto | `py -3.12 tools/monitor/monitor.py --once` |
| Sacar una tarea de `triage` sin reescribirla | Botón `🔄 Reintentar`/`🧊 Sacar de triage` del bot; o `HermesCLI.requeue_triage` (usa `specify_triage_task` con el venv de Hermes) |
| Reiniciar el gateway de Hermes | `%LOCALAPPDATA%/hermes/bin/hermes gateway restart` (tras cambios de `config.yaml`/`.env`) |
| Panel del kanban caído | Servicio `cloudflared` + tarea "Hermes Dashboard"; reinstalar con `tools/infra/instalar-panel-y-tunel.ps1` (admin) |
| Commit en repos compartidos | Siempre con pathspec: `git add docs/x && git commit -m "…" -- docs/x`; `git diff --cached --stat` antes (memoria `feedback_shared_git_index_pathspec_commit`) |
