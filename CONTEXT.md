# CONTEXT.md — Mapa maestro de Oscar

> Fuente compartida. Cualquier agente que conecte empieza aquí. Actualizado manualmente.
> **Actualizado: 2026-10-25** (F1-08 del plan del hub: carriles, despliegue, Hermes y personas). La tabla de
> proyectos y el presupuesto son de 27-09; el estado al día de cada proyecto está en `projects/`.

> **PLAN VIGENTE DEL HUB (25-10-2026): [`decisions/2026-10-25-arquitectura-hub-v2.md`](decisions/2026-10-25-arquitectura-hub-v2.md)**
> (cerebro en pildora-01, workers en el PC, kanban y personas en Supabase, fases F0-F5) y
> [`decisions/2026-10-25-personas-roles-permisos.md`](decisions/2026-10-25-personas-roles-permisos.md) (quién puede qué).
>
> **Plan de trabajo (23-10-2026): [`decisions/2026-10-23-plan-workflows-deuda.md`](decisions/2026-10-23-plan-workflows-deuda.md).**
> Trabajo por workflows integrales encadenados (máximo 3 de código a la vez), sin conflictos (prevención al
> lanzar), toda función con sus pantallas y URL, una pasada completa con el sistema de diseño, ahorro de
> tokens y limpieza de ramas y worktrees. Decisiones vivas de Oscar y deuda técnica medida en
> https://claude.ai/artifact/FcnvknBmUPEMEuscHqYevf. Donde este CONTEXT.md diga otra prioridad, manda el plan.

## Identidad
- **Oscar Alcántara** — solopreneur, bootstrapped. CEO. Idioma por defecto: Español (España).
- Modelo de trabajo: multi-agente (Claude Code en carriles y consola, Hermes) con un solo humano decisor: Oscar.
  Equipo con acceso al sistema: María (socia, opera como Oscar), Ángel (CTO Oscar HQ + Signal, acotado por marca),
  Ingerber y Andrea (marketing Píldora). Detalle: `decisions/2026-10-25-personas-roles-permisos.md`.
- Prioridad actual declarada: terminar/cerrar la tanda de **signal + pildora** en oscar-hq,
  dejar todo mergeado + documentado, y **pasar a MigraTeam** (prioridad de enfoque).

## Príncipios de toma de decisión (usados en la sesión de planificación)
- **80/20**: priorizar funcionamiento para vender/gestionar, no pulidos ni CI caro.
- **Coste**: créditos agotados; prefiere trabajar en local y desplegar solo al probar. Evitar
  consumo de recursos en RAM/Cloud. Vigilar presupuestos duros ($10-25 LLM por marca, Railway $12/mes).
- **Sin preguntas triviales**: Oscar responde decisiones vía CLI/UI. No trancar workstreams esperando
  respuestas: avanzar con lo claro y dejar bloqueado solo lo que depende de él.
- **Deploys/merges a master·main**: SIEMPRE requieren confirmación de Oscar (un push despliega solo).

## Presupuesto y spend (señal)
- Railway: **$12 este mes** (crons incluidos). Se investigó lazy-load y crews.
- Créditos de LLM: **agotados** → trabajar en local; desplegar solo al probar.
- Apollo enrich: gasto por lote, tramo a tramo. 2.101 empresas en Píldora.
- Google/dominios: se están actualizando todos los dominios para usarlos correctamente.

## Proyectos · estado resumido
| Proyecto | Estado | Enfoque actual | Notas |
|---|---|---|---|
| **oscar-hq** | Activo | Agentes/crews desacoplados; signal+pildora; brandbrain redundante | H-090/H-092/H-096/H-098/H-102; goals $10-25 cortan en duro; 57 hallazgos pendientes de triar |
| **MigraTeam** | Activo | **Próxima prioridad** tras cerrar signal | Labeling/plantillas; emails con estilo; tagging para generar contenido |
| **Pildora Digital (website)** | Activo | Turnstile newsletter, propuestas, plantillas transaccionales | pildoradigital.com (agencia) vs pildora.ai (plataforma); rebranding a futuro |
| **Scraper (signal engine)** | Activo | Signal Engine / icp_pildora / icp_multiverse / consular_intel | 2.101 empresas, Apollo enrich por lotes |
| **Signal Console (pildora-signal-console / multiverse-console / oscar-console)** | Activo | Unificar en uno solo; visual pobre, sin wizard | Miedo: logins distintos, hay que consolidar |
| **Cartera** | En desarrollo | Poner en línea (login tras pildora) | /home: projecto en git con handoff |
| Otros (NextJobs AutoApply, VPDN, FinTrade) | Pausado | — | |

## Situaciones con riesgo / abiertas
- **Sesiones de signals con 4h esperando decisiones** — no trancar; consolidar preguntas.
- **Runs automáticos en marcha** en la plataforma (posiblemente pruebas) — parar todo y dejar en
  standby para empezar fresh. Brandbrain con muchas páginas que hacen lo mismo, la primera no editable.
- **Logins de consolas** redirigen a páginas equivocadas o a login que no tiene claves → revisar.
- **Open design / hermes / túneles** se perdieron tras reinicio del PC; algunos respaldados en disco E:.

## Convenciones técnicas (resumen; los repos tienen su AGENTS/CLAUDE.md con el detalle)
- Windows + Git Bash: usar `git -C "<ruta>"`.
- Node: en PATH desde la reinstalación del 22-09 (`C:\Program Files\nodejs`).
- Alembic: un único propietario de migraciones por release (Integrador, salvo que el AGENTS.md del repo
  lo fije por claim); verificar `version_num` en Supabase antes de push; nunca borrar migraciones ni
  hacer UPDATE/stamp de `alembic_version` sin plan, backup y OK de Oscar.
- Despliegues: siempre con OK de Oscar. **Oscar HQ (backend) en Dokploy de pildora-01** desde la migración del
  21-22 oct (skill `desplegar-dokploy`; también NextJobs, OpenDesign y Postiz); el dashboard sigue en Vercel (push).
  Railway para Oscar HQ es historia (los CLAUDE.md globales aún lo citan: CF-10, cambio pendiente del OK de Oscar).
  **Scraper/Signal: sin resolver (CF-07)**: Railway sigue desplegando desde la rama base y pildora-01 también lo
  ejecuta; un solo entorno lo decide Oscar.
- Al desplegar: revisar "propagaciones potenciales" y resolver de raíz errores recurrentes.

## Cómo se conectan los agentes (sistema de carriles, 2026-09-27)
Detalle: `decisions/2026-09-27-arquitectura-carriles.md` y `decisions/2026-09-27-telegram-grupos-temas.md`.
- **Hermes = Coordinador.** Recibe los mensajes (DM y grupos), crea tarjetas, pregunta y avisa. No implementa código
  de producto. **Hoy (25-10) sí tiene terminal, ficheros y navegador en Telegram** (`apply_hermes_config.py --check` lo
  avisa). El plan v2 lo acotaba para todos; Oscar quiere que *su* Hermes conserve la consola y está pendiente decidir
  el reparto por perfiles (ver `decisions/2026-10-25-arquitectura-hub-v2.md`). No se tocan sus toolsets sin su OK.
- **Carriles:** `tools/agent-lanes` (runner) toma las tarjetas `ready` del kanban de Hermes asignadas a un carril
  (p. ej. `claude-oscarhq`). Por cada una: worktree + rama `lane/<id>`, `claude -p` con hooks de seguridad,
  verificación mecánica y paso a `review`. Los workers corren con `AGENT_LANES_TASK` (MODO WORKER del
  `~/.claude/CLAUDE.md`) y solo pueden hacer push de su rama.
- 1 sesión interactiva = 1 rol + 1 worktree/rama. Máximo 3 workstreams de código.
- **La Sesión Maestra y `ORDENES_MAESTRO.md` están retirados** (en `archive/`).
- **Codex: en pausa desde el 27-09** (histórico; no hay carril `codex-*`).
- **Modelos:** Sonnet por defecto; Opus solo para diseño/arquitectura y en los subagentes fijados.
- **Estado 25-10** (fuente: `tools/agent-lanes/lanes.yaml`; el README de agent-lanes lo comprueba un test):
  - Carriles: `claude-oscarhq`, `claude-migrateam`, `claude-scraper`, `claude-nextjobs`, `claude-hub` (alias
    `claude-telegram`: «Hub y Telegram» es un solo carril), `claude-ops` y `review`. `max_workers` = 4.
  - Integrador activo en 4 carriles: `claude-oscarhq`, `claude-migrateam`, `claude-hub` y `claude-scraper`.
  - Las decisiones con botones van al tema 🎯 Decisiones del grupo Gestión; María decide ahí como Oscar.
- **Actualización 28-09** (histórico; detalle: `decisions/2026-09-28-ramas-bandeja-monitor.md`):
  - Carriles y repos: `claude-oscarhq` → oscarsao/oscar-hq (`master`; entonces `railway up`, hoy Dokploy);
    `claude-migrateam` → PildoraDigital/OCR-PDF-and-images (**`develop`**, nunca master);
    `claude-scraper` → oscarsao/icp-prospect-engine; `claude-nextjobs` → oscarsao/nextjobs-autoapply.
    `claude-ops` (en construcción) = trabajo operativo sin repo; `claude-hub` (plan D) = este repo.
  - Integrador activo solo en Oscar HQ; respeta dependencias padre→hijo del kanban (#10 no antes que #9).
  - Telegram: un bot por tema (Hermes en Operaciones; C-level en el resto; CMO en Marketing). Los temas los crea
    SIEMPRE el bot de Trabajos (los que crea Hermes le hacen contestar a todo).
  - Monitor en vivo: `tools/monitor/monitor.py` (PC, carriles, Hermes, servicios, chats, alertas).

## Fuentes de verdad
| Qué | Dónde |
|---|---|
| Ejecución de agentes (código y tareas delegables) | Kanban de Hermes (boards oscarhq, migrateam, default, personal); pasa a Supabase (`hub.*`) en F2 |
| Personas y permisos | `tools/telegram/people.yaml` hoy; `ohq_people` en Supabase desde F4 (`decisions/2026-10-25-personas-roles-permisos.md`) |
| Negocio, colaboradores (Andrea), decisiones que dependen de Oscar | Kanban de Píldora (app.pildoradigital.com) |
| Personal | ClickUp |
| Conocimiento: contexto, decisiones, referencias | Este hub |
| Reglas de agentes | `~/.claude/CLAUDE.md` (manda); excepciones en el AGENTS.md de cada repo |

## Backlog de decisiones abiertas para Oscar (para no trancar)
1. Unificar consolas de signal en una sola herramienta → elegir cuál queda.
2. Confir/standby de runs automáticos.
3. Simplificar brandbrain (páginas duplicadas).
4. Cartera: login detrás del login de pildora.
5. Reglas de ICP/apollo configurables desde la UI para no gastar créditos en perfiles dudosos.