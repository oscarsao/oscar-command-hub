# 05 · Glosario y quién decide qué

Fecha de referencia: 21-10-2026.

## 1. Glosario

| Término | Qué significa aquí | Dónde está |
|---|---|---|
| **Carril / lane** | Cola de trabajo consumida por el runner. El `assignee` de una tarjeta del kanban **es** el nombre del carril: `claude-oscarhq`, `claude-migrateam`, `claude-scraper`, `claude-nextjobs`, `claude-hub` (el propio sistema), `claude-ops` (operativo sin repo), `review`. El dispatcher de Hermes los ignora (no son perfiles); solo los consume `agent-lanes` | `tools/agent-lanes/lanes.yaml`, `README.md` § Qué va a cada carril |
| **agent-lanes / runner** | `tools/agent-lanes/runner.py --all`: proceso único (tarea programada «agent-lanes runner») que hace claim, worktree, `claude -p` con hooks, verificación, review, integrador, bot de Trabajos, recordatorios | `tools/agent-lanes/` |
| **Worker** | El `claude -p` que ejecuta una tarea dentro de un carril, con `AGENT_LANES_TASK` (activa el **MODO WORKER** de `~/.claude/CLAUDE.md`), rol `roles/implementador.md`, Sonnet, 3 $ y 2 h de tope | `agent_lanes/worker.py` |
| **MODO WORKER** | Sección de `~/.claude/CLAUDE.md` que se activa con la variable `AGENT_LANES_TASK`: implementador de UNA tarea, solo push de `lane/<id>`, todo lo de «SIEMPRE confirma» → `needs_input` | `~/.claude/CLAUDE.md` |
| **Guard** | Hook `PreToolUse` (`contract/guard.py`) que bloquea al worker: push a ramas que no sean `lane/*`, `--force`, `gh pr merge`, `railway`, `vercel`, `alembic` contra BD no local, `.github/workflows/`, merges que no sean de `origin/<base>`. Para ops, `contract/ops_guard.py` (lista blanca, fail closed). **LIMITACION-GUARD** = el worker declara que no puede seguir por el guard → cola del integrador | `contract/`, `agent_lanes/guard_route.py` |
| **Kanban (de Hermes)** | SQLite local con 4 tableros (`default`, `oscarhq`, `migrateam`, `personal`); claim con TTL, heartbeat, dependencias padre/hijo, estados `todo/ready/running/review/blocked/done/archived/triage`. **Fuente de verdad de la ejecución** | `%LOCALAPPDATA%/hermes/kanban.db`; panel `kanban.pildoradigital.com` |
| **Kanban de Píldora** | Tablero de negocio (app.pildoradigital.com): colaboradores, ventas, decisiones humanas. Sin sincronización bidireccional con el de Hermes | `CONTEXT.md` |
| **Tarjeta / t_xxxxxxxx** | Tarea del kanban (id `t_` + 8 hex). Etiquetas en el título: `[IDEA de X]`, `[DECISIÓN]`, `[SEMANA]`, `[CORRECCIONES]`, `[CAPTURA]`, `[PM]`, `[BUG ALTA]`… | `agent_lanes/notices.py`, `card_options.py` |
| **Hermes** | hermes-agent v0.21.4, bot `@oscarsaoBot`, gateway en el PC. Rol **Coordinador**: intake, kanban, preguntas, avisos; «no implementa» (regla de proceso) | `%LOCALAPPDATA%/hermes` |
| **Bot de Trabajos** (= bot de carriles) | `@pildora_carriles_bot`, «Pildora - Trabajos». Código determinista del hub dentro del runner: botones, comandos, fichas de integración | `agent_lanes/commands.py`, `decisions.py` |
| **Bot del equipo** (= bot de roles) | `@PildoraD_BOT`, «Oscar HQ Avisos». LLM con sombrero por tema, corre en Railway (oscar-hq) | `oscar-hq/src/services/telegram_*.py` |
| **Triage (trampa del)** | Estado del kanban al que Hermes manda una tarea bloqueada dos veces por el mismo motivo (`block_loop_detected`); desde ahí `unblock` falla. Se sale con `specify_triage_task` (botón `🔄 Reintentar` / `🧊 Sacar de triage`) | `agent_lanes/hermes.py:416-433` |
| **needs_input / block kinds** | Tipos de bloqueo: `needs_input` (pregunta a Oscar), `dependency`, `transient` (fallo técnico o verificación), `capability` (falta acceso), `limitacion_guard` | decisión 27-09 §5 |
| **RESPUESTA-OSCAR:** | Prefijo exacto con el que Hermes (autor `default`) u `oscar-telegram` apunta en la tarjeta una respuesta de Oscar para que el runner la reconozca | `README.md` § Responder desde Hermes |
| **APROBADO-OSCAR / INTEGRADO / APLICADO / VALIDADO-OSCAR** | Comentarios-marca en la tarjeta: PR aprobado desde Telegram; PR fusionado (`INTEGRADO <sha>`); reinicio del hub aplicado; tarea ops validada | `agent_lanes/integrator.py` |
| **Review (carril)** | `claude -p` de solo lectura (`AGENT_LANES_ROLE=revisor`) que revisa el diff antes de `done`; máx. 2 rondas | `agent_lanes/review.py`, `roles/revisor.md` |
| **Integrador** | (a) Rol humano/sesión que compone workstreams, pasa el gate combinado y es único propietario de Alembic; (b) `agent_lanes/integrator.py`: fusión determinista de los PR aprobados por Oscar, gates, lotes, deploy con botón. Activo con `INTEGRATOR_ENABLED=true` y carriles en `INTEGRATOR_LANES` (`.env`) | `~/.claude/CLAUDE.md` § ROLES, `lanes.yaml` → `integrator:` |
| **Lote (diario)** | `batch: daily`: las ramas aprobadas de un proyecto se fusionan en orden en `release/lote-<marca>-<fecha>`, un solo PR y un solo botón `[🔀 Fusionar lote]` a las 08:30 o con `/lote` | `docs/lote-diario-integrador.md`, `agent_lanes/batch.py` |
| **Gate** | Comprobación mecánica antes de fusionar: conflictos, `test_cmd`, secret-scan, un solo head de Alembic, migraciones manuales, rutas sensibles, build del dashboard | `integrator.py`, `batch.py` |
| **Ficha 🚦** | Mensaje del bot de Trabajos en el tema Integración (t398) con cabecera por riesgo: 🟢 staging · 🟠 deploy manual · 🔴 producción · ⚙️ sistema · ⏸ en espera · ⛔ gates fallidos | `agent_lanes/integration.py` |
| **Aplicar** | Botón `[🔁 Aplicar (reinicio ordenado)]` tras fusionar un PR de `claude-hub`: `lanes.py restart --drain` en el checkout vivo (fast-forward de `main` + reinicio del runner) | `agent_lanes/drain.py` |
| **Drain / restart --drain** | Reinicio ordenado del runner: deja de reclamar, espera a los workers, reinicia la tarea programada | `lanes.py`, `drain.py` |
| **Runner ciego** | Episodio en que el runner no puede listar tareas (p. ej. `WinError 4551`) durante 5 min; aviso ALTA y restart automático (1/hora) | `agent_lanes/ciego.py` |
| **Worktree** | Checkout adicional de git. Cada tarea de carril vive en `C:/Users/oscar/dev/_lanes/lane-<id>`; cada sesión humana del hub en `dev/_hub-wt/<nombre>`. Nunca `checkout`/`stash` en el checkout raíz de un repo con worktrees vivos | `~/.claude/CLAUDE.md` § ROLES |
| **Checkout vivo** | `C:/Users/oscar/oscar-command-hub` en `main`: el código que ejecuta el runner; sus hooks son los que rigen a los workers | `agent-lanes/README.md` § Carril del sistema |
| **Candado de repos** | Cada carril declara `github: owner/repo`; si el `origin` del repo local no coincide, el carril no reclama | `lanes.yaml`, `git_ops.github_slug_problem` |
| **Guarda de tablero** | `board_guard.py`: recoloca tarjetas abiertas en un tablero que su carril no lee (`hermes kanban create` sin `--board` cae en `default`) | `lanes.py doctor-boards` |
| **Claim (MigraTeam)** | Fichero en `.coordination/claims/` que declara ownership de archivos antes de editar; lo comprueba `scripts/check_claims.py --diff`. Único propietario de `backend/alembic/versions/` por release | `migrateam/AGENTS.md` §7 |
| **CHANGELOG** | Historia de cambios: oscar-hq `docs/kb/CHANGELOG.md` (una entrada por sesión); MigraTeam `docs/kb/archivo/CHANGELOG.md` | ver 04 §5 |
| **LEDGER** | MigraTeam `docs/kb/LEDGER_ESTATUS.md`: «la única lista de lo que está abierto», hallazgos con ID `CATEGORIA-NN-MMDD` y evidencia `archivo:línea` | ver 04 §5 |
| **HANDOFF** | Traspaso entre sesiones: oscar-hq `docs/kb/HANDOFF_PROXIMA_SESION.md` (debería caber en 1 página); MigraTeam `.coordination/handoff-*.md` y `docs/kb/HANDOFF_PROXIMA_SESION.md`. Skill `/handoff` | `~/.claude/CLAUDE.md` § SKILLS |
| **OASP** | Metodología por tamaño: Fast-Track (bugfix <1 h, sin artefacto), Spec-Lite (`specs/<slug>.md`), Pitch (`pitches/<slug>.md`), CTO-360 (`docs/audits/`). Los workers implementan Fast-Track directo y en Spec-Lite/Pitch solo escriben la spec y piden OK | `~/.claude/CLAUDE.md` § METODOLOGÍA, `roles/implementador.md` |
| **Signal / Signal Engine** | Motor de prospección B2B del Scraper: fuentes (jobs, BORME, GitHub, ATS…), señales tipadas con decaimiento, scoring, decisores; cola `ohq_signal_jobs` encolada desde Oscar HQ y ejecutada por `signal-worker-pildora` | `Scraper/signal_core`, `signal_worker/Dockerfile` |
| **signal_core** | Paquete común y configurable (ICP en YAML, proveedores gratuitos y de pago, BudgetGuard, exportadores a `ohq_signal_jobs` y `multiverse.runs`) construido la noche del 28→29-09 (PR #3 del Scraper, 1140 tests) | `Scraper/docs/signal_core/INFORME_2026-09-29.md` |
| **ICP** | Ideal Customer Profile: definición YAML de a quién buscar (ICP Píldora = despachos/pymes; ICP Multiverse = cuentas EE.UU.; `icps/`, `icp_pildora/`, `icp_multiverse/`). Se sincronizan a Oscar HQ (Ajustes › ICPs, 17-10) | `Scraper/icps`, oscar-hq CHANGELOG 17-10 |
| **Multiverse** | Cliente (contrato de servicios + adenda n.º 1 en firma): lead-gen con señales; publicación F4 en producción el 14-10 (336 cuentas, 52 cualificados) | memoria `project_multiverse_aplazado_2026-10-13` |
| **Brand Brain / handbook** | Conocimiento de marca: `pildora-handbook` (SSOT humano, PR-only) → copia manual a `oscar-hq/memory/brands/`; Brand Brain = servicio en oscar-hq | `C:/Users/oscar/CLAUDE.md` § Ecosistema Píldora |
| **Hub (command hub)** | Este repo, `oscar-command-hub`: conocimiento + `tools/` | `README.md`, `AGENTS.md` |
| **Plan D** | «Que el sistema se sostenga sin la sesión de Claude Code»: alertas al DM, carril `claude-hub`, auditor diario, drain, candado de repos | `decisions/2026-09-28-ramas-bandeja-monitor.md` §7 |
| **Fases P/A/B/C/F/E/G/H** | Plan de los bots del equipo (15-10): P config en panel+BD; A carriles en lenguaje llano; B herramientas de lectura; C `ejecutar_crew`/`crear_tarjeta`; F estado operativo en la nube (`ohq_ops_state`); E topología de bots; G mini PC; H blog por PR | `decisions/2026-10-15-arquitectura-bots-equipo.md` §6 |

## 2. Quién decide qué

| Decisión | Quién | Cómo se materializa | Fuente |
|---|---|---|---|
| Push/merge a `main`/`master` de cualquier repo, deploys (Railway `railway up`, Vercel), promoción MigraTeam `develop→master`, `alembic upgrade` contra staging/prod, `DROP/TRUNCATE`, `alembic downgrade`/`stamp`, `git push --force`, borrar archivos no generados, env vars en Vercel/Railway, lanzar CI de deploy a mano | **Oscar** (confirmación explícita, cada vez) | Botones `int_*`/`promo_*` del bot de Trabajos, `/aprobar`, `/desplegar`, `/promover`; en consola, con `!` de Oscar; para agentes → `needs_input` | `~/.claude/CLAUDE.md` § AUTONOMÍA (reescrita el 29-08 tras el push a master que desplegó en 4 min sin CI); `C:/Users/oscar/CLAUDE.md` § AUTONOMÍA («no añade permisos») |
| Lo mismo, delegado | **María** (COO) como **operadora** del bot de Trabajos desde el 19/20-10: mismos comandos y botones, acciones firmadas | `people.yaml` `operador: true` (solo María) | commit `a41da56`; memoria `project_maria_coo_acceso_total` (merge/deploy «siguen con confirmación» → hoy la puede dar María en el bot) |
| Código de MigraTeam | Oscar (María debe pedir permiso) | Regla de proceso, no técnica | memoria 12-10 |
| Contenido (blog, textos, docs, handbook) = riesgo BAJO | Ingerber y María pueden fusionar tras revisión automática y aviso a Oscar | Decidido 17-10; implementación por fases (matriz `t_2f6aefdd`, migración sin aplicar) | `mapa-reglas` §9 (17-10) |
| Código sin migración = riesgo MEDIO | El equipo propone, Oscar fusiona con recomendación | ídem | ídem |
| Gasto en ads, posicionamiento | Oscar (en Gestión · Marketing·Decisiones) | Escalado desde Marketing | `decisions/2026-09-27-telegram-grupos-temas.md` |
| Alta/baja de personas y permisos | Solo Oscar, a mano | `people.yaml` + `.env` (hub); `ohq_people`/`/permisos` (oscar-hq) | `docs/equipo-bots.md`, `mapa-reglas` §8 |
| Cambiar un raíl de código (`HARD_RAILS`, `guard.py`, `ops_guard.py`, `worker-settings.json`) | Oscar, con PR + decisión escrita en `decisions/`; los workers de `claude-hub` no deben tocarlos salvo petición expresa | PR + `decisions/<fecha>-<slug>.md` | `roles/hub.md`, `lanes.yaml` `sensitive_paths` |
| Reinstalar tareas programadas, `.ps1`, `.env`, `.state/` | Oscar en sesión (fuera del alcance de los carriles) | Consola | `agent-lanes/README.md` § Carril del sistema |
| Alembic: crear la revisión, aplicar en staging/prod | **Integrador** (único propietario por release) crea; **Oscar** aprueba aplicar; nunca `stamp` sin evidencia + plan + backup + OK | `db-migration-checker` antes de push | `~/.claude/CLAUDE.md` § ALEMBIC |
| Qué carril recibe una tarea | Hermes (reglas de la skill) o Oscar/María (`/hazlo`, `/flujo`); código → `claude-<repo>`, operativo → `claude-ops`, solo-Oscar/irreversible → `oscar` | Tarjeta con `assignee` | `agent-lanes/README.md` § Qué va a cada carril |
| Decisiones reversibles con recomendada sin respuesta en 48 h | Hoy solo **aviso** (`veto_modo: aviso`); «ejecutar» solo cuando Oscar lo active en `bandeja.yaml` | `bandeja.yaml` | `tools/agent-lanes/bandeja.yaml` |
| Modelo de IA | Sonnet por defecto (sesión y workers); Opus solo diseño/arquitectura y subagentes `security-auditor`/`cto-audit-360`; Haiku para lookups y respuestas informativas del bot | `lanes.yaml` `model: sonnet`; `explain.py` haiku | `~/.claude/CLAUDE.md` § ESCALACIÓN |
| Máximo de trabajo paralelo | 3 workstreams de código (sesiones); `max_workers: 1` worker `claude -p` a la vez (hoy el servicio corre con 2) | `lanes.yaml`, `install-service.ps1` | `~/.claude/CLAUDE.md` § ROLES |

## 3. Reglas de autonomía vigentes (resumen operativo)

**Procede sin preguntar:** editar/crear código en la rama/worktree propios, commits atómicos y push **de la rama propia**,
instalar dependencias, `alembic upgrade head` **solo en local o test aislado**, aplicar el mismo fix a todas las instancias del
patrón.

**Siempre confirmar antes de:** push/merge a `master`/`main`; cualquier despliegue a producción (incluido lanzar CI a mano);
`alembic upgrade` contra staging o producción; `DROP TABLE`/`DROP COLUMN`/`TRUNCATE`; `alembic downgrade` o editar/`stamp`
`alembic_version`; `git push --force`; borrar archivos que no sean generados (y nunca worktrees/ramas ajenos); cambiar env vars
en Vercel o Railway.

**Roles de conversación** (uno por sesión): Coordinador (no edita código) · Implementador (una rama/worktree) · Integrador
(compone, gate combinado, dueño de Alembic) · Revisor (solo lectura). Antes de editar: mostrar repo, rama, upstream, HEAD, SHA
base y `git status --short`; si hay cambios ajenos o detached HEAD → no escribir. Commits siempre con pathspec en repos
compartidos. El handoff nunca se queda sin commitear.

**Para los workers de carril** (MODO WORKER): todo lo anterior se convierte en `status=needs_input` con la pregunta concreta;
sin `/distill`, `/handoff` ni subagentes caros salvo que la tarea lo pida.

Fuente: `~/.claude/CLAUDE.md` (manda); `C:/Users/oscar/CLAUDE.md` y `AGENTS.md` de cada repo no añaden permisos.
