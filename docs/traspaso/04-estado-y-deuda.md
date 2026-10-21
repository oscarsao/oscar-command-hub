# 04 · Estado actual y deuda conocida

Fecha de referencia: 21-10-2026. Cada punto lleva la evidencia que encontré; donde la afirmación del encargo no cuadra con la
evidencia lo digo. Comandos de solo lectura ejecutados el 21-10 entre las 11:00 y las 12:30.

## 1. Los «5 tests que fallan de base»

**No encontré ningún lugar donde haya exactamente 5 tests fallando.** Lo que hay:

| Suite | Resultado 21-10 | Detalle |
|---|---|---|
| **Hub** `py -3.12 -m pytest tools/agent-lanes/tests -q` (worktree de `main` b4ea602) | **1558 passed, 0 failed** (142 s) | Es el `test_cmd` del carril `claude-hub` y el gate del integrador: está en verde |
| **oscar-hq** `py -3.12 -m pytest tests -q --continue-on-collection-errors` (master `5ec7134b`) | **68 failed, 3402 passed, 1 error** (194 s) | `ERROR tests/test_telegram_aviso.py`: `ImportError: cannot import name '_create_card' from partially initialized module 'src.services.telegram_aviso' (circular import)` vía `telegram_tools → telegram_crews`. Fallos: al menos 27 en `tests/test_telegram_tools.py` (botones de imagen, P1/P2 de Telegram, `_scrub` del token), 1 en `tests/test_tools_registry.py::TestCLevelTools::test_ceo_has_most_tools`, 1 en `tests/test_api_health.py::test_recent_logs_endpoint` (`assert 403 == 200`). La lista completa no quedó guardada: reproducir con el comando de la izquierda |
| oscar-hq en los gates del hub | `lanes.yaml:102`: «pytest de oscar-hq no es usable como gate hoy (`test_api_health` falla en master por entorno; tarjeta aparte)» | Por eso `claude-oscarhq` solo usa `checks.py` (diff --check + compilación) y, en el lote, `tsc --noEmit` + `vitest` del dashboard |
| Documentación | `oscar-hq/docs/kb/HANDOFF.md:48` (29-08): «Suite completa oscar-hq: "3 errors" de import por `crewai`/`crewai_tools` ausentes en el venv — preexistentes»; `CHANGELOG.md:2003`: `test_api_health.py` esperaba `system=="Oscar HQ"` y la API devolvía «Píldora Digital» | Nada dice «5» |

Lectura: la deuda real es que **la suite de oscar-hq no está en verde en `master`** (68 + 1 error el 21-10, con dependencia
del entorno: Supabase/claves) y por eso no se usa como gate. Hace falta decidir si se arregla la suite (circular import de
`telegram_aviso`, 403 en `/api/health/recent-logs`) o se marca lo que depende de entorno.

## 2. La caída de Railway

Hay **varios incidentes** distintos, no uno; el encargo no especifica cuál:

| Fecha | Servicio | Qué pasó | Evidencia |
|---|---|---|---|
| 15-09 | Railway `signal-worker-pildora` | Deploy `547f06fb` FAILED «incidente Railway»; Oscar relanzó `railway up` desde `Scraper` → SUCCESS 21:00 | `oscar-hq/docs/kb/CHANGELOG.md:690-691` |
| **25-09, 17:57 UTC** | **MigraTeam producción** (backend + cron) | 502: el build tomó SQLAlchemy 2.1 (rango abierto en `requirements.txt`; el lock no lo usa el build) → `ModuleNotFoundError: No module named 'psycopg'` en `alembic/env.py`. Hotfix `07e37e3e5` fija `sqlalchemy<2.1.0` directo a `master` | `git -C migrateam show 07e37e3e5` |
| 30-09 y **01-10 09:40-11:30** | **Oscar HQ API** (Railway) por **Supabase saturada** (Micro, 60 conexiones) | Caída de ~2 h; causa no confirmada porque el reinicio borró `pg_stat_statements`. Resultado: sonda `dbwatch.py` cada 5 min, alertas AVISO/ALTA, `db_snapshots/` | `docs/monitor-db-supabase.md` |
| 15-10 | Oscar HQ API | El servidor dejaba de contestar (también `/api/health`, UI «Motor Offline») por 102 handlers `async def` sin `await`; parche `en_hilo.py`; **quedan ~430 handlers** iguales | `oscar-hq/docs/kb/CHANGELOG.md:28-36` |
| **21-10 09:13** | Oscar HQ (Railway) | Deployment `3799bcb5…` **FAILED** tras un SUCCESS a las 08:57; `railway status` = «Online · Deploy failed (2h 25m)»; el tráfico lo sirve el contenedor anterior. No pude leer el log del fallo con la CLI | `railway status` / `deployment list` 21-10 |

Consecuencias ya incorporadas al sistema: `/api/health` nunca como única señal (`lanes.yaml:277`), sondeo del deployment
concreto, `health-*` entre las claves de caída del monitor que saltan el silencio nocturno (`monitor.py:57-58`), alerta de
«develop por detrás de master» tras hotfix directo (`alerts.log` 21-10: «MigraTeam: develop va 1 commit por detrás de master»;
`gh api compare develop...master` = master ahead 1, behind 3 → el hotfix del 25-09 **sigue sin devolverse a develop**,
contra la regla del 28-09).

## 3. Flujo de lotes (lote diario del integrador)

Documentado en `docs/lote-diario-integrador.md`; implementado en `agent_lanes/batch.py`; activo en `claude-oscarhq` y
`claude-migrateam` (`lanes.yaml` `batch: daily`, `batch_time` por defecto 08:30); `claude-hub` y `claude-scraper` van PR a PR.

Estado real el 21-10 (`runner.log` 11:21-11:38): el integrador tiene en `batch_pending` las tarjetas `t_ba84e80d`, `t_8c8654f4`,
`t_ccbb934d`, `t_f05fa7dd` (y `t_970ced99` salió porque su PR #193 ya estaba merged). Es decir: hay ramas aprobadas esperando al
lote de mañana o a un `/lote <marca>`.

Deuda y límites conocidos:
- Montar un lote ejecuta la suite del carril (hasta 30 min) en el hilo del runner; `/lote` lo hace en hilo aparte.
- El gate del lote de oscar-hq incluye `npm ci` + `tsc` + `vitest` del dashboard (`batch_frontend_cmd`, #46 del 20-10) porque
  «#172 dijo tests OK con el dashboard roto».
- Una rama excluida (conflicto, ruta vetada, secreto, Alembic con dos heads) sale con motivo llano y botones
  `[🔧 Arreglar] [🔁 Rebasar] [⏭ Dejar para el siguiente lote]` (#38); si el combinado falla se aísla la culpable.
- Hijo con padre no integrado espera al lote siguiente.
- El resumen fijado del tema Integración aún no lista las ramas en cola/lote.
- El hub no usa lote y por eso **colisiona**: PRs seriados sobre los mismos ficheros gigantes (`commands.py` ~940 líneas,
  `decisions.py` ~915, `integrator.py` ~1700) y el hueco «aprobado → fusionado» de horas (`docs/diagnostico-fusiones-flujo-carriles.md`,
  01-10). Recomendaciones de ese diagnóstico aún abiertas: lote también para el hub (probar en seco), resync con `main` al
  cerrar, respetar dependencias al reclamar, partir los ficheros «dios». Lo que sí se hizo después: conflictos al fusionar
  devueltos al carril y reevaluación (#12, 11-10), dependencias padre→hijo en el integrador (`deps.py`), reconciliación (#47).

## 4. Atascos por guard («LIMITACION-GUARD»)

Qué es: el worker choca con `contract/guard.py` (no puede hacer push a otra rama, merge, `gh pr merge`, `railway`, `vercel`,
`alembic` remoto, tocar `.github/workflows/`…) o con una ruta vetada del carril, y devuelve
`LIMITACION-GUARD: el worker no puede hacerlo con el guard del carril`. El runner lo bloquea con `kind=limitacion_guard`.

Evidencia de que ocurre a diario (`tools/monitor/.state/alerts.log`, 21-10): `t_ece5a0ef` 09:18, `t_93cd7849` 09:25,
`t_a6b0f257` 11:40 (los tres «bloqueada (limitacion_guard)»); `.state/integrador_cola.json` cuenta **9 tarjetas** que han pasado
por la cola (`veces`: t_200419e0, t_6120b5bc, t_02d93f84, t_6933661e, t_f8cb71d9, t_798aaced, t_ece5a0ef, t_93cd7849, t_a6b0f257).
El último caso (`t_a6b0f257`, claude-migrateam): el worker no cambió nada porque necesitaba rebasar/fusionar ramas `lane/*` de
otras tareas (#245, #193, #180), cosa que el guard prohíbe (`git merge` solo de `origin/<base>` o la rama nombrada en la tarea).

Cómo se trata hoy (PR #49 / `t_f8f58acb`, fusionado el 21-10, `agent_lanes/guard_route.py`): cada bloqueo de guard va a la **cola
del integrador** (`/integrador`), se anuncia en el tema Atención con @mención del integrador (tarjeta, qué hace falta, comando exacto),
y si a las 2 h nadie lo resolvió escala a Oscar con botones «Que lo resuelva el integrador» (reasigna a `oscar-e2`) y «Aplazar 24 h».
Un bloqueo que Hermes pasó a `triage` por repetirse también entra. **Nunca ejecuta nada**: lo resuelve una persona en consola
(`/tomar-tarea`, rebase a mano, o cambiar la tarea).

Deuda: (a) el bloque `attention_telegram` de `lanes.yaml` **no está configurado** en el checkout vivo, así que la @mención en el
tema Atención no está activa; (b) muchos atascos son tareas de «rebasar/fusionar la rama X» que por definición un worker con guard
no puede hacer: conviene que Hermes/`/hazlo` no las asigne a un carril de código sino a `oscar`/integrador; (c) ramas locales con
commits sin empujar que el runner conserva («limpieza pendiente: rama local lane/t_ece5a0ef conservada: no coincide con origin»,
`runner.log` 11:24) se acumulan en `dev/_lanes` (29 worktrees `lane-*` el 21-10).

## 5. Conflicto entre CHANGELOG y LEDGER

Es un problema de **MigraTeam** (en oscar-hq no existe ningún LEDGER: la referencia a `docs/kb/LEDGER_ESTATUS.md` en
`references/2026-09-27-auditoria-e2e-oscar.md:123` del hub apunta a un fichero que no está en oscar-hq).

| Fichero (MigraTeam) | Qué dice de sí mismo | Último commit |
|---|---|---|
| `docs/kb/CHANGELOG.md` | Stub: «Movido a `docs/kb/archivo/CHANGELOG.md` el 2026-08-29» | `6e1a3880b` 02-09 |
| `docs/kb/archivo/CHANGELOG.md` | CHANGELOG real (una entrada por intervención; última «PROMOCIÓN 24/09») | `5546c2d1c` 25-09 |
| `docs/kb/LEDGER_ESTATUS.md` | «Ledger de estatus — la única lista de lo que está abierto», verificado contra `master@af10418d` (04-09) y re-verificado contra `develop@6cb6f429` (09-09) | `5546c2d1c` 25-09 |
| `docs/kb/HALLAZGOS_PENDIENTES.md` → `archivo/HALLAZGOS_PENDIENTES.md` | «⚠️ HISTORIA — su columna de estado ya no es fiable» | 02-09 / 05-09 |

El conflicto está en las **reglas**, no solo en los ficheros:
- `migrateam/CLAUDE.md:385` dibuja `docs/kb/archivo/` como «← Ledger, ADRs e inventarios», pero el ledger vigente
  (`LEDGER_ESTATUS.md`) está en `docs/kb/`, no en `archivo/`.
- `CLAUDE.md:388-389` dice «para escribir en los ledgers vigentes usa `docs/kb/archivo/CHANGELOG.md`, `DECISIONES_ARQUITECTURA.md`…»
  y acto seguido (`:393-395`) «`LEDGER_ESTATUS.md` es la fuente única… **corrige la línea de arriba**, que listaba
  `HALLAZGOS_PENDIENTES.md` como ledger vigente». Es decir, el propio CLAUDE.md contiene una corrección sobre sí mismo en vez
  de la regla corregida; el checklist (`:358`) exige actualizar `docs/kb/archivo/CHANGELOG.md` y `AGENTS.md:544` prohíbe a los
  implementadores editar `CHANGELOG.md` salvo ownership explícito.
- Oscar HQ tiene el modelo opuesto: `docs/kb/CHANGELOG.md` activo (una entrada por sesión, última 17-10) y
  `HANDOFF_PROXIMA_SESION.md` como estado; sin ledger.
- Mi lectura: hace falta una decisión de Oscar que fije **un** modelo (CHANGELOG = historia, LEDGER = estado abierto/cerrado con
  IDs) y reescribir `CLAUDE.md` de MigraTeam sin la autocorrección; el commit `1075c9afc` del 25-09 («resuelve 2 contradicciones de
  gobernanza») no cerró esta.

## 6. La RAM del PC

Hechos (21-10): **15,8 GB** de RAM total, 82 % en uso a las 12:00 (`psutil`), con 6 procesos `claude` vivos. Alertas del monitor
el mismo día: «RAM al 89 % (14,0/15,8 GB)» 09:43, «RAM al 90 % (14,2/15,8 GB)» 10:44, «7 procesos claude abiertos (¿workers
colgados?)» 09:18, 09:56 y 11:39 (`tools/monitor/.state/alerts.log`).

Historia: OOM el **27-09** con varias sesiones abiertas → `runner.max_workers: 1` en `lanes.yaml` y candado de instancia única
(`service.py`). Pero la tarea programada se registró con `install-service.ps1 -MaxWorkers 2` y **sigue arrancando con 2**
(`runner.log` 20-10 04:24 y 17:12: `max_workers=2`); alinearlo requiere reinstalar la tarea (Oscar). El aviso continuo de RAM se
sustituyó el 11-10 (#8) por `/ram` bajo demanda e informe cada 4 h al DM (`monitor.py:68`, `commands.py:1212`). Cada worker es un
`claude -p` (Sonnet) más Hermes, el panel, el monitor, Chrome y las sesiones interactivas de Claude Code.

Salida decidida (15-10): mover el plano de control al **mini PC Minisforum AI X1 Pro-470 (64 GB RAM)** comprado el 24-09
(`decisions/2026-09-24-compra-minisforum-x1-pro-470.md`), fase G, con la restricción de que todo usa la **cuenta de Claude de
Oscar** (login, no API key) y el control debe correr en una sola máquina a la vez
(`decisions/2026-10-15-arquitectura-bots-equipo.md` §7bis). No hay evidencia de que el mini PC aloje nada todavía.

## 7. Otros frentes abiertos que verás enseguida

- **Worktrees acumulados**: 29 `lane-*` en `C:/Users/oscar/dev/_lanes` (oscar-hq tiene 11 registrados en `git worktree list`),
  13 en `dev/_hub-wt`, 1 detached en `dev/_wt/hub-integrador`. El runner conserva las ramas con commits sin empujar.
- **PRs abiertos** (21-10, `gh pr list`): hub #44 (bug botón Reintentar); oscar-hq 6; Scraper #33 (borrador, `signal_person_id`).
- **Migraciones de oscar-hq sin aplicar** citadas en el CHANGELOG 17-10: `20261017_telegram_permissions_matrix.sql`,
  `20261021_signal_catalog_sync_job.sql` (rama `feat/sync-catalogo-icp-ui`).
- **Railway Config as Code deprecado** (`railway.json`/`railway.toml`, aviso de la CLI 21-10; sigue funcionando hasta 2026-12-01).
- **Hermes con `terminal` en Telegram** (`config.yaml:1139`) contra la decisión del 27-09; la regla «Hermes no implementa» es
  de proceso.
- **Personas duplicadas**: `tools/telegram/people.yaml` (hub) vs `ohq_people` (Supabase); `people.yaml` dice Andrea → Píldora y
  `ohq_people` «sin marca» (`mapa-reglas` §2). Fase F (`t_b5e31b70`) debe retirar `people.yaml`.
- **`pildora-website` sin carril, sin CI ni protección de rama** (push a `main` = producción): decisión pendiente.
- **Smart App Control / WinError 4551** (29-30/09: 24 h de runner ciego sin que nadie lo supiera) → vigilante `ciego.py` (#39);
  causa raíz sin concluir.
- **t_22de53c2** (`clarify` de Hermes en foros) sin diagnosticar; P2 (relé determinista Hermes→tarjeta) y P4 retenidas
  (`docs/auditoria-aprobaciones-telegram-2026-10-15.md`).
- **Instaladores `.ps1`/`.vbs` fuera del alcance de los carriles** (el integrador bloquea ejecutables): se cambian solo en sesión
  con Oscar.
