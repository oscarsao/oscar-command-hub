# CONTEXT.md — Mapa maestro de Oscar

> Fuente compartida. Cualquier agente que conecte empieza aquí. Actualizado manualmente.
> Versión de referencia: 2026-09-24. Antes de confiar en este doc en una sesión vieja, verifica
> la fecha contra los repos reales si hay drift.

## Identidad
- **Oscar Alcántara** — solopreneur, bootstrapped. CEO. Idioma por defecto: Español (España).
- Modelo de trabajo: multi-agente (Claude Code, Codex, Hermes) con un solo humano decisor: Oscar.
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
- Node no está en PATH: usar rutas absolutas `nvm/v24.5.0/node`.
- Alembic: único propietario de migraciones = Integrador; verificar `version_num` en Supabase
  antes de push; nunca borrar migraciones.
- Deploy Vercel/Railway: confirmar con Oscar. Railway para oscar-hq.
- Al desplegar: revisar "propagaciones potenciales" y resolver de raíz errores recurrentes.

## Cómo se conectan los agentes
- 1 sesión Claude = 1 rol + 1 worktree/rama. Máx 3 workstreams de código.
- Sesiones de Claude se comunican por **cross-session / named pipes**.
- Hermes = asistente integral (este hub). Decisiones finales → Oscar.
- Codex: presente (auth `~/.codex`), binario no en PATH global; **priorizar Claude Max** por ahora.

## Backlog de decisiones abiertas para Oscar (para no trancar)
1. Unificar consolas de signal en una sola herramienta → elegir cuál queda.
2. Confir/standby de runs automáticos.
3. Simplificar brandbrain (páginas duplicadas).
4. Cartera: login detrás del login de pildora.
5. Reglas de ICP/apollo configurables desde la UI para no gastar créditos en perfiles dudosos.