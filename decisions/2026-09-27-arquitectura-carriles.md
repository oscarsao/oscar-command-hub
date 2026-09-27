# Plan — Sistema de orquestación Hermes + carriles de agentes (v1)

Fecha: 2026-09-27 · Rol de esta sesión: **Coordinador** (no edita código de producto)

## 1. Contexto: tu idea central (con tus palabras)

- *"aquí solo tomamos decisiones y planificamos… abrir varios frentes y que me informaras aquí"* (26-09)
- *"apuntar en el kanban, decidir, informarme y consultarme. Recordarme pendientes y carriles o agentes libres, ser proactivo… gestionar todos los mensajes que envío y procesarlos en las tareas"* (27-09)
- *"encadenar tareas en los agentes correspondientes para ir avanzando de manera autónoma"* (26-09)
- *"necesito ver cómo interactúan tú y mi bot en los grupos… cuándo se empiezan y terminan trabajos"* (27-09)
- *"en vez de apagar fuegos… agentes y crews estándar… que vayan aprendiendo y adaptándose"* (24-09)
- *"desarrollo en base a Loops o Workflows automatizados, no en base a prompting"* (25-09)

Resumido: **Hermes = jefe de gabinete** (intake, kanban, decisiones, avisos). **Agentes = ejecutores con rol y memoria** que trabajan solos, se ven en Telegram, y el sistema mejora con datos.

**Por qué falla hoy**, verificado en código y config:
1. **No hay canal Hermes → trabajador persistente.** Hermes lanza `claude -p` de una pasada. Cuando la tarea queda a medias, la termina él.
2. **Las reglas no están aplicadas en las herramientas, solo en el texto.**
   - Hermes tiene `terminal` y `code_execution` activos en Telegram (`config.yaml` L1128).
   - Los claims de MigraTeam son voluntarios. El 06-09 hubo 5 sesiones y 0 claims.
3. **Una sola conversación eterna** (11.911 mensajes). La compactación pierde el hilo, y así se confundió NextJobs con MigraTeam.
4. **Hay 5 sitios de "pendientes"**: kanban de Hermes, kanban de Píldora, ClickUp, `ORDENES_MAESTRO.md` (sin commitear, parado desde el 25-09) y el command-hub. Ninguno es la fuente de verdad.

**Hallazgo clave:** el kanban de Hermes (v0.21.4) **ya es una cola profesional**:
- claim atómico con TTL, `heartbeat`, `idempotency_key`
- `task_runs` con metadata, dependencias padre/hijo
- estados `review` y `blocked` tipados (`needs_input` avisa a un humano)
- notificador a Telegram
- CLI completa con `--json`

El dispatcher **salta a propósito** los assignees que no son perfiles (`claude-session`, `codex-session`). Espera que un trabajador externo haga `hermes kanban claim`. Esa pieza nunca se construyó. **Este plan la construye, sin tocar el código de Hermes.**

## 2. Estándar de la industria frente a lo que tienes

| Capa | Estándar | Tú hoy | Mejora |
|---|---|---|---|
| Plano de control vs. ejecución | El supervisor no ejecuta; separación por permisos | Hermes coordina *y* ejecuta (terminal activo) | Quitar herramientas de escritura a Hermes en Telegram |
| Cola de trabajo | Cola durable con lease, heartbeat, reintentos, idempotencia | Existe (kanban de Hermes) pero nadie consume `claude-session`/`codex-session` | **Lane runner** que hace claim → ejecuta → devuelve |
| Ejecución duradera | Checkpoint + resume, reintentos acotados | `claude -p` sin estado | `--session-id` guardado en la tarea + `--resume` acotado + `block` |
| Contrato entre agentes | Entrada/salida tipadas (A2A, JSON Schema) | Prompt libre y resultado en prosa | Plantilla de tarea + `--json-schema` en la salida |
| Aislamiento | Un worker = un sandbox o rama | Worktrees a mano | Worktree automático por tarea y claim autogenerado |
| Guardrails | Política aplicada en la capa de herramientas | Reglas en CLAUDE.md que "no vinculan" (incidente 28-08) | Hooks `PreToolUse` en los workers, toolsets restringidos en Hermes |
| Verificación | No fiarse del autoinforme del agente | Hermes revisa el diff a mano | El runner verifica rama/SHA/tests; carril `review` antes de `done` |
| Estado y memoria | Estado en el sistema de tareas, no en el chat | Resúmenes de compactación | Kanban = memoria operativa; sesión de Hermes por día/tema |
| Mensajes concurrentes | Cola de entrada + ack inmediato | `busy_input_mode: interrupt` | `queue`/`steer` + ack "apuntado t_xxx en carril X" |
| Observabilidad | Trazas, coste y resultado por ejecución | Nada agregado | Métricas desde `task_runs` + cumplimiento de Hermes desde `state.db` |
| Aprendizaje | Eval → propuesta → aprobación humana (sin auto-modificar reglas) | `/distill` manual; reglas momentáneas que "se quedan fijas" (tu queja del 25-09) | Retro semanal basada en métricas → PR con propuestas, umbral de recurrencia |

## 3. Arquitectura objetivo

```
 Tú (Telegram DM / grupos)
        │  mensaje
        ▼
 HERMES (Coordinador) ── solo: kanban_*, clarify, lectura, memoria, cron
   1. ack inmediato   2. clasifica: pregunta | trabajo | decisión
   3. trabajo → kanban create (plantilla + lane + idempotency_key)
   4. decisión → clarify con opciones + recomendación
        │
        ▼
 KANBAN HERMES (fuente de verdad de EJECUCIÓN)  boards: oscarhq, migrateam, personal
        │ ready + assignee = lane
        ▼
 LANE RUNNER (nuevo, repo agent-lanes, tarea programada de Windows al iniciar sesión)
   claim → worktree + claim file → heartbeat (hilo propio, 240 s)
   → claude -p / codex exec (rol + hooks + schema + budget)
   → si queda a medias: --resume ≤2 → si no: block(needs_input|transient)
   → verificación mecánica (push, SHA, test focalizado)
   → request-review
        │
        ▼
 CARRIL REVIEW (code-reviewer) → done | blocked
        │ eventos
        ▼
 Notificador kanban → temas de Telegram (Gestión·operaciones-código = inicio/fin/bloqueo)
 Retro semanal → métricas + propuestas (PR) → tú apruebas
```

Tus consolas interactivas también cuentan como carriles: la skill `/tomar-tarea` hace el claim y `/cerrar-tarea` el complete.

## 4. Decisiones (confirmadas por Oscar el 27-09 en esta sesión)

- **Dos superficies en Telegram con papeles distintos:**
  - **DM con Hermes = despacho (solo lectura).** Apunta, decide, pregunta, reparte y avisa. Sin `terminal`/`code_execution`.
  - **Temas de los grupos = carriles directos.** Un mensaje en un tema crea al momento una tarea `ready` para el carril o rol de ese tema, con el contexto del tema (hilo, marca, repo).
    - El trabajador del carril (worktree, hooks, verificación) **publica inicio, avance, preguntas y fin en ese mismo hilo**.
    - La conversación del grupo tampoco ejecuta con terminal: la ejecución siempre la hace el worker del carril. Así hay rama, frenos y trazabilidad.
    - Cada tema tiene **memoria propia** (perfil o sesión por tema). Esto corrige lo de "todos los bots tienen la misma memoria".
  - Mapeo tema → rol → carril (tabla `topic_lanes` en `agent-lanes/lanes.yaml`):
    - Gestión t5 operaciones-código → cto → `claude-<repo>` (se indica el repo en el mensaje o se pregunta con opciones)
    - Gestión t6 negocio-ventas → cso
    - Gestión t7 → cmo
    - Gestión general → ceo
    - Marketing general/t5/t6 → cmo
    - Marketing t7 → coo
    - Los roles de negocio sin repo usan un carril `crew:<rol>` en W4, que llama a los crews de Oscar HQ.
- **Kanban de Hermes = ejecución; kanban de Píldora = negocio.** Confirmado.
- **La Sesión Maestra se sustituye** por el runner + carril review + Integradora por repo. `ORDENES_MAESTRO.md` se commitea una vez y se archiva. Confirmado.

- **Fuentes de verdad:**
  - **Kanban de Hermes** = ejecución de agentes (código y cualquier tarea delegable). Es el único con claim/lease.
  - **Kanban de Píldora** (app.pildoradigital.com) = negocio/humano (Andrea, ventas, decisiones que dependen de ti).
  - **ClickUp** = personal. Sin escritura amplia de Hermes (incidente del borrado del Space).
  - **command-hub** = conocimiento (decisiones, contexto). `projects/*.md` se **genera** a partir del kanban y git, no se mantiene a mano.
  - `ORDENES_MAESTRO.md`: commitear una vez y retirar.
  - **Sin sincronización bidireccional** entre kanbans (fuente de bugs). El que quiera ver los dos los mira en Telegram.
- **La "Sesión Maestra" (Codex + ORDENES_MAESTRO)** del 24-09 se sustituye por runner + carril review + sesión Integradora por repo.
- **Autonomía por modo OASP (tope = rama lista para revisión):**
  - Fast-Track: implementa → push de su rama → review → **tú o el Integrador hacéis el merge**.
  - Spec-Lite/Pitch: la tarea 1 genera la spec → `block needs_input` (tu OK) → tarea hija de implementación.
  - Nunca: merge a master/main, deploy, Alembic contra staging o prod, cambios de CI (tu regla del 27-09).
- **Límite de concurrencia:** 3 carriles de código activos. Tu CLAUDE.md global dice 3 y el AGENTS.md de MigraTeam dice 4; manda el global hasta que lo cambies.

## 5. Contrato (lo fija esta sesión; W1/W2/W3 lo implementan)

**Carriles (assignee):**
- `claude-oscarhq`, `claude-migrateam`, `claude-scraper`, `claude-nextjobs`
- `codex-oscarhq`, `codex-migrateam`
- `review`
- Definidos en `agent-lanes/lanes.yaml`: board, repo, rama base, herramienta, modelo, esfuerzo, `max_budget_usd`, `max_parallel`, archivo de rol, comando de test focalizado.
- Las tareas existentes con `claude-session`/`codex-session` se reasignan.

**Mapeo a columnas existentes** (no se inventa JSON en `body`):

| Dato | Columna |
|---|---|
| carril | `assignee` |
| worktree | `workspace_path` |
| rama `lane/<task_id>` | `branch_name` |
| UUID de sesión de claude (para `--resume`) | `session_id` |
| modelo / esfuerzo | `model_override` / `reasoning_effort` |
| reintentos | `max_retries` |
| ID del mensaje de Telegram (deduplica mensajes concurrentes) | `idempotency_key` |
| resultado estructurado + coste | `task_runs.metadata` |

**Cuerpo de tarea (plantilla que usa Hermes):**
- Objetivo
- Contexto: enlaces a decisiones o mensajes
- Criterios de aceptación
- Fuera de alcance
- Modo OASP
- Test focalizado

**Salida del worker** (`--json-schema`, `agent-lanes/contract/result.schema.json`):
`{status: done|needs_input|failed, summary, branch, head_sha, changed_files[], tests{command, exit_code}, questions[], next_steps[], risks[]}`

**Verificación mecánica del runner:** el runner no se fía del autoinforme.
- comprueba `git ls-remote` de la rama
- comprueba que el SHA existe
- ejecuta **él mismo** el test focalizado

Si algo no cuadra → `block --kind transient`, nunca `done`.

**Bloqueos:**

| Kind | Cuándo |
|---|---|
| `needs_input` | Pregunta de negocio/diseño; el notificador te avisa |
| `dependency` | Espera a una tarea padre |
| `transient` | Fallo técnico o verificación fallida |
| `capability` | Falta un acceso o credencial |

## 6. Workstreams (para sesiones paralelas; máx. 3 de código a la vez)

Cada brief se entrega autocontenido: repo, rama, SHA base, archivos propios y excluidos, criterio de hecho y verificación. Cada workstream abre con su **tarjeta en el kanban de Hermes** (dogfooding).

**W0: Higiene y arranque (esta sesión tras aprobar; Fast-Track, sin código de producto)**
- Commitear los archivos sin trackear de `oscar-command-hub` (`ORDENES_MAESTRO.md` + 3 `decisions/`) y añadir `decisions/2026-09-27-arquitectura-carriles.md` con este plan.
- Crear en el board `oscarhq` las tarjetas W1–W5 con sus briefs.

**W1: Lane runner (Spec-Lite · Sonnet · repo nuevo `C:\Users\oscar\dev\agent-lanes`, Python 3.12)**
- `runner.py`, bucle por carril:
  - `hermes kanban list --assignee L --status ready --json` → `claim`
  - `git worktree add` desde `origin/<base>`
  - claim file en `.coordination/claims/` si el repo lo usa
  - hilo de heartbeat (240 s, TTL 900)
  - `claude -p --session-id <uuid> --name task-<id> --settings contract/worker-settings.json --permission-mode acceptEdits --append-system-prompt-file roles/<rol>.md --json-schema contract/result.schema.json --max-budget-usd N --output-format json`
  - parseo; `--resume` ≤2; verificación; `complete`/`request-review`/`block`
- Adaptador Codex: `codex exec --json`. **Verificar al empezar:** resume de Codex y semántica de `hermes kanban request-review`/`heartbeat`.
- `lanes status`: carriles libres y ocupados (kanban `running` + PIDs + `claude agents`).
- Arranque con una tarea programada de Windows al iniciar sesión (PC-only; con el PC apagado las tareas esperan en `ready`).
- Tests (TDD en la lógica de estado): claim doble, heartbeat mientras claude corre más de 15 min, resume, verificación fallida → `transient`.
- **Hecho cuando:** una tarea real de `oscarhq` viaja `ready → running → review` sin intervención de Hermes.

**W2: Contrato del worker y guardrails (Fast-Track/Spec-Lite · Sonnet · mismo repo, solo `contract/`, `roles/`, `skills/`)**
- `contract/worker-settings.json`: hooks `PreToolUse` (matcher `Bash`, salida con código 2) que bloquean:
  - `git push` a master/main
  - `--force`
  - `gh pr merge`
  - `railway up`
  - `vercel --prod`
  - `alembic` con URL no local
  - editar `.github/workflows/`
- `contract/result.schema.json`, `contract/task-template.md`.
- `roles/implementador.md` y `roles/revisor.md`: resumen del rol + "lee AGENTS.md del repo" + reglas OASP.
- Skills para las consolas interactivas: `/tomar-tarea <id>` y `/cerrar-tarea`, en `~/.claude/skills/`.
- **Hecho cuando:** un `claude -p` de prueba con estos settings intenta `git push origin master` y el hook lo bloquea.

**W3: Hermes como Coordinador (Spec-Lite · Sonnet · `AppData\Local\hermes`: skill + config.yaml)**
- **Backup de `config.yaml` antes de tocarlo.** Nunca imprimir ni volcar secretos: ~66 líneas sensibles (ver memoria `feedback_agents_no_secrets_to_disk`).
- Reescribir la skill `oscar-multi-agent-orchestration`, podando las 937 líneas:
  - protocolo de intake (ack → clasificar → kanban/clarify)
  - plantilla de tarea
  - tabla de carriles
  - `clarify` siempre con opciones + recomendación
  - proactividad: pendientes tuyos, carriles libres, tarjetas de más de 24 h
- Config:
  - **DM (`6744452215`) sin `terminal`/`code_execution`.**
  - Temas de los grupos: intake directo a su carril, también sin terminal.
  - **Verificar al empezar** si hermes-agent permite toolsets por chat o tema. Si no, usar un perfil por grupo; `profiles/pildora-feedback` demuestra que los perfiles funcionan.
  - `busy_input_mode: queue`.
  - Rotación de sesión diaria del DM.
- Intake de temas: mensaje en un tema → `kanban create` con `assignee` según `topic_lanes`, `idempotency_key` = id del mensaje y `chat_id`/`thread_id` en la tarea, para que el runner publique en ese hilo.
- Publicación en hilo: el runner (W1) llama a la API de Telegram con el bot de Hermes para escribir inicio, avance, `needs_input` y fin en el `thread_id` de origen. Las tareas creadas desde el DM publican en Gestión t5.
- **Hecho cuando:** mandas 3 mensajes seguidos mientras Hermes trabaja → 3 acks con su t_id, 0 comandos de escritura en `state.db`.

**W4: Bots por rol y kanban de negocio (después; repo `oscar-hq`, deploy manual con tu OK)**
- Verificar `role_routes` con el mapeo aprobado:
  - Gestión: general→ceo, t5→cto, t6→cso, t7→cmo
  - Marketing: general/t5/t6→cmo, t7→coo
- Memoria por rol/tema (hoy "todos los bots tienen la misma memoria").
- Borrar `telegram_topics.py`, que es código muerto.
- Eventos del kanban de Píldora a los temas de Marketing.

**W5: Observabilidad y autoaprendizaje (cuando W1 lleve ~1 semana de ejecuciones reales)**
- `lanes metrics` sobre `task_runs` de todos los boards:
  - lead time
  - % aprobado en primera review
  - resumes por tarea
  - motivos de bloqueo
  - coste por carril/modelo
- **% de turnos de Hermes con herramientas de escritura**, desde `state.db`. Mide si Hermes cumple, sin depender de su autoevaluación.
- Retro semanal (cron de Hermes) → mensaje en Gestión + **PR de propuestas** a skills/AGENTS.md/lanes.yaml.
  - Solo propone patrones que se repiten ≥2 veces.
  - Nunca se auto-aplica; tú apruebas.
  - Se integra con `/distill` y `lessons.jsonl`.
- Generar `oscar-command-hub/projects/*.md` a partir del kanban y git (se acabó lo de "3 días desactualizado").

**Orden (revisado: primero un esqueleto de punta a punta, después paralelo):**

1. **W0** (esta sesión).
2. **Fase 1, esqueleto que funcione de punta a punta** (1 sesión, 1–2 días):
   - Núcleo de W1 + W2 en `agent-lanes`: un solo carril `claude-oscarhq`, solo DM, sin Codex ni temas.
   - Runner mínimo (claim, worktree, heartbeat, `claude -p`, resume, verificación, `request-review`) + hooks + schema + aviso a Telegram.
   - Mínimo de W3: skill de intake + DM sin terminal.
   - **Criterio de salida:** una tarea real de `oscarhq` pasa por los 8 pasos de la §7 (excepto el 6, que depende de los temas).
3. **Fase 2 en paralelo** (≤3 sesiones, contrato ya validado):
   - W1b: adaptador Codex + más carriles + `lanes status` + tarea programada de Windows.
   - W3b: temas como carriles directos + publicación en hilo + poda de la skill.
   - W4: bots por rol / carriles `crew:<rol>`.
4. **W5** tras ~1 semana de ejecuciones reales.

**Protecciones transversales:**
- Fijar hermes-agent en 0.21.4 y añadir un test de contrato de la CLI (`hermes kanban list/claim/heartbeat/complete/block/request-review --help` y salida `--json`) que corra al arrancar el runner. Si una actualización lo rompe, el runner no arranca y te avisa.
- Token del bot y secretos solo en `agent-lanes/.env` (en `.gitignore`), nunca en el repo ni en logs.

Los 7 frentes que ya tienes abiertos (Signal, NextJobs, Instagram→Signal, ICPs…) **no se tocan aquí**. Cuando W1+W3 funcionen, se convierten en tarjetas y serán las primeras cargas reales del sistema.

## 7. Verificación end-to-end

1. En Telegram: *"arregla X en oscar-hq"* → ack con `t_id` en menos de un minuto, tarjeta `ready` asignada a `claude-oscarhq` con la plantilla completa.
2. El runner la reclama → el tema operaciones-código muestra "empieza t_id".
3. Worker con un turno cortado a propósito → resume → rama empujada → verificación OK → `review`.
4. Carril review → `done` → aviso en Telegram con resumen, rama y test.
5. Tarea con una pregunta de negocio → `blocked needs_input` → Hermes te pregunta con opciones → respondes → vuelve a `ready`.
6. Mientras tanto, mandas 3 mensajes → 3 acks, ninguno se pierde.
7. `lanes status` muestra correctamente los carriles libres y ocupados.
8. Un intento de `git push origin master` del worker queda bloqueado por el hook.
