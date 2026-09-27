# Informe para Claude — Rediseño del sistema de orquestación multi-agente de Oscar

**Contexto**: este documento lo escribió Hermes (asistente Claude en Telegram, coordinador diario
de Oscar) tras un diagnóstico real de fallas de proceso el 2026-09-27. Objetivo: que Oscar lo lleve
a una sesión de Claude Code con más espacio de razonamiento, y juntos diseñen/implementen la
solución real — esto no es la solución, es el diagnóstico completo + el problema a resolver.

---

## 1. Qué existe hoy (inventario real, verificado)

**Componentes actuales del ecosistema:**
- **Hermes** (este chat, Telegram) — coordinador conversacional. Modelo base deepseek/OpenRouter.
  Puede: kanban propio (`mcp__kanban_*`), lanzar procesos `claude -p "<prompt>" --output-format json`
  en background (una sola pasada, sin estado entre llamadas), lanzar subagentes internos
  (`delegate_task`, aislados, sin memoria de "rol de crew"), terminal/git/lectura de código directa.
- **Claude Code CLI** — sesiones interactivas que Oscar abre él mismo en consolas separadas (fuera
  del alcance de Hermes) más los procesos `-p` que Hermes lanza. Tiene:
  - `.claude/CLAUDE.md` global con roles (Coordinador/Implementador/Integrador/Revisor), reglas
    de autonomía, gates de Alembic, metodología OASP (Fast-Track/Spec-Lite/Pitch/CTO-360),
    escalación de modelos (Haiku default → Sonnet → Opus solo vía subagentes fijos).
  - Subagentes con modelo fijo en su frontmatter: `code-reviewer` (sonnet), `security-auditor`
    (opus), `db-migration-checker` (sonnet), `cto-audit-360` (opus), `sprint-planner` (sonnet).
  - Skills de workflow: `/spec-lite`, `/audit-360`, `/pre-deploy`, `/handoff`, `/distill`,
    `/healthcheck`.
  - Mejora continua: `/distill` escribe a `~/.claude/memory/lessons.jsonl`; telemetría en
    `~/.claude/telemetry/`; `/distill --monthly` propone cambios a CLAUDE.md.
  - **Cada AGENTS.md de repo (MigraTeam en particular) añade reglas propias** de nivel 1
    (jerarquía documental, protocolo multi-conversación, claims de ownership en
    `.coordination/claims/`, checklist de patrón, triage de bugs P0-P3).
- **Codex CLI** — activo, autentica contra la cuenta ChatGPT de Oscar, gasta créditos separados de
  Claude Max. Usado en paralelo a Claude para no chocar en el mismo repo (worktrees).
- **oscar-command-hub** (`C:\Users\oscar\oscar-command-hub`, repo git) — "cerebro compartido":
  `CONTEXT.md` (mapa maestro), `projects/*.md` (estado por proyecto), `decisions/`, `handoffs/`,
  `references/`. Diseñado para que cualquier agente lea esto primero. **Última actualización real:
  2026-09-24** — 3 días desactualizado respecto al trabajo real de esta semana (Signal, NextJobs,
  bots por rol no aparecen ahí todavía).
- **Kanban de Hermes** (`mcp__kanban_*`) — tablero real, con historial de tareas y comentarios.
  Confirmado hoy: se usa como bitácora POSTERIOR (comentarios añadidos después de hacer el trabajo),
  no como registro previo. Tareas quedan en "ready"/"running" sin cerrar formalmente pese a que el
  trabajo real ya terminó — encontrado y corregido manualmente varias veces hoy mismo.
- **Grupos de Telegram con Topics** — 2 grupos (Gestión, Marketing), 6 temas, pensados para que un
  bot de Oscar HQ responda con "rol" distinto (CEO/CMO/CFO/CTO) según el tema. Diseñado hace 2 días
  (PR #34), nunca completado con los IDs reales — quedó con placeholders TODO.

## 2. El problema real, con evidencia (diagnóstico de subagente, 2026-09-27)

Encargamos a un subagente barato leer el historial de esta conversación (session_id
`20260924_110835_a180d27a`) y responder 3 preguntas de proceso. Resultado, con evidencia citada:

### 2.1 Instrucciones que se pierden entre tareas
- Hermes dejó el fix de `role_routes`/bots-Telegram a medias (bloqueado en una pregunta de diseño)
  y saltó a otra cosa sin decirlo explícitamente — el propio Hermes lo admitió cuando Oscar se
  quejó: "Ahorita acabas de dejar parado lo de signal y te acabas de dar cuenta."
- El mapeo de 6 temas de Telegram (PR #34) llevaba ~2 días con placeholders sin completar. Oscar
  tuvo que notarlo él mismo: *"Por qué me preguntas esto si ya sabes los grupos y temas que tengo
  creados? [...] es como si no tuvieras memoria o algo muy raro."*
- Un caso se repitió EL MISMO DÍA con MigraTeam: Oscar mencionó errores en "casi todas las páginas"
  hablando del frontend de **NextJobs**; horas después, tras una compactación de contexto, Hermes
  leyó mal el resumen y lanzó una auditoría completa de **MigraTeam** (equivocada), incluyendo
  instalar un venv y correr la suite completa de pytest — trabajo real desperdiciado. Oscar tuvo que
  corregirlo DOS veces el mismo día antes de que Hermes lo abortara.
- El kanban estaba vacío (`kanban_list(status=blocked/running)` → count:0) mientras había 7 frentes
  de trabajo real activos solo en la conversación — confirma que las tareas no se registraban al
  iniciarse, así que "perderlas" era estructural, no un accidente puntual.

### 2.2 La compactación de contexto pierde fidelidad real
- El bloque de resumen de compactación (56.387 caracteres originales) solo exponía ~2.000 caracteres
  al mecanismo de búsqueda que Hermes usa para recuperar detalle — pérdida de granularidad real, no
  solo teórica.
- Tras compactar, Hermes tuvo que reconstruir el estado real consultando el kanban en vez de confiar
  en el resumen — indicio de que el resumen no capturaba el estado operativo de forma fiable.
- La confusión MigraTeam/NextJobs de arriba nació directamente de una lectura errónea del resumen
  post-compactación, no de un olvido simple.

### 2.3 Hermes implementa en vez de delegar (el patrón que más le preocupa a Oscar)
Evidencia de trabajo pesado hecho DENTRO de la conversación de Telegram, no delegado a un crew:
- `grep`/lectura directa de código de producto, `gh pr view` + parseo, `curl` contra la API de
  Telegram con el token extraído del `.env` real.
- Creación de un `venv`, `pip install -r requirements-dev.txt`, y ejecución de la suite completa de
  tests de MigraTeam (5598 tests) en background — dentro del chat, para un problema que además
  resultó ser irrelevante (ver 2.1).
- Edición directa de código de producto (`icp_pildora/enrich_contacts.py`) y fusión a la rama de
  trabajo del repo Scraper, hecha por Hermes mismo tras encontrar que un subproceso `claude -p` había
  dejado el fix a medias (función definida pero nunca llamada).
- Activación de una fuente de datos en producción (Instagram→Signal) escribiendo directo a Supabase
  vía `service_role` desde un script Python ad-hoc en la terminal de Hermes.

**Admisión explícita de Hermes, causa raíz real:** *"No tengo forma de invocar directamente a 'tus
otros agentes de Claude' — solo puedo lanzar procesos nuevos de Claude Code CLI en modo `-p` de una
sola pasada, sin memoria de crew ni rol persistente. Eso hace que en la práctica yo termine
escribiendo el prompt, revisando el diff, corriendo tests y hasta arreglando lo que el proceso dejó
a medias — soy yo actuando como implementador, disfrazado de 'delegué a un agente'."*

Este es el núcleo del problema de diseño: **no existe ningún canal real entre Hermes y las sesiones
de Claude Code con memoria/rol persistente que Oscar ya tiene definidas en `.claude/CLAUDE.md`**.
Los procesos `-p` que Hermes lanza son anónimos, sin rol, sin memoria entre invocaciones, y sin forma
de "devolverles" el trabajo cuando se quedan sin turnos a medias (pasó 3 veces hoy: NextJobs-auth,
Signal-people-sweep, y el diagnóstico de MigraTeam) — así que Hermes termina de rematarlo él mismo.

## 3. Qué se corrigió ya, de forma inmediata (parche, no solución de fondo)

Se añadieron 5 reglas duras a la skill de Hermes (`oscar-multi-agent-orchestration`), vigentes desde
hoy, **solo para la conversación de Telegram**:
1. Kanban-primero: crear la tarjeta antes de tocar código/terminal no trivial, no después.
2. Prohibido terminal pesado en este chat (pip install, pytest completo, venvs, editar+fusionar
   código de producto) — debe delegarse a un proceso `claude -p` en background o a un subagente.
   Excepción: comandos de solo lectura (`git log/status`, `curl` de verificación, `grep`, logs).
3. Checkpoint explícito al recibir un mensaje nuevo a mitad de tarea: declarar "pauso X" o "esto no
   bloquea X", nunca cambiar de tema en silencio.
4. Tras cada compactación, verificar contra `kanban_list` real antes de actuar; si algo no cuadra
   con el último mensaje real de Oscar, preguntar en vez de asumir.
5. Avisar proactivamente tarjetas `running`/`blocked` con más de 24h sin actividad.

Estas reglas mitigan el SÍNTOMA (Hermes se comporta mejor en esta conversación) pero no resuelven la
CAUSA (no hay canal real hacia crews con memoria persistente).

## 4. Lo que Oscar quiere construir (su visión, en sus palabras de hoy)

> "esta conversación solo debería hacer pocas cosas: apuntar en el kanban, decidir, informarme y
> consultarme. Recordarme si tengo cosas pendientes y carriles o agentes libres, ser proactivo [...]
> pero sobre todo poder gestionar todo los mensajes que envío y procesarlos en las tareas, hacer las
> preguntas necesarias."

Traducido a requisitos de sistema:
1. **Hermes = capa de intake + control, no de ejecución.** Todo mensaje de Oscar se traduce a una
   decisión: ¿es una pregunta que Hermes responde directo? ¿es trabajo que va al kanban y se
   despacha a un agente real? ¿es una decisión que requiere `clarify()`? Nunca "lo hago yo mismo
   aquí mismo" salvo lectura/diagnóstico.
2. **Persistencia real entre Hermes y los crews de Claude Code de Oscar** — un canal donde Hermes
   pueda: (a) asignar trabajo a un rol/crew específico que YA tiene su propio contexto/memoria
   (Coordinador/Implementador/Integrador/Revisor por repo, definidos en cada `AGENTS.md`), (b)
   recibir de vuelta el resultado sin tener que rematar el trabajo él mismo, (c) saber qué "carriles"
   (worktrees/sesiones) están libres u ocupados en cada momento.
3. **El kanban como fuente de verdad única y previa**, no como bitácora posterior — cualquier
   sistema de continuidad de contexto debe apoyarse en el kanban (estructurado, consultable) antes
   que en resúmenes de conversación (con pérdida de fidelidad demostrada).
4. **Manejo explícito de mensajes concurrentes** — Oscar escribe mientras hay trabajo en curso
   constantemente (multitasking real, varios frentes). El sistema necesita una forma de encolar,
   anotar y decidir sin perder ninguno, con feedback inmediato de qué pasó con cada uno.
5. **Los grupos/temas de Telegram como superficie de trabajo de los agentes**, no solo de Hermes —
   Oscar quiere ver a "su bot" y a Hermes interactuando visiblemente en los grupos reales, con
   roles diferenciados por tema (CEO/CMO/CFO/CTO), como parte de la misma arquitectura.

## 5. Preguntas abiertas para la sesión de diseño con Claude

1. ¿Es viable un protocolo de "buzón" persistente (archivo/DB compartido) donde Hermes deposite
   tareas con contexto completo y una sesión de Claude Code (con su rol ya cargado desde AGENTS.md)
   las recoja, trabaje, y devuelva el resultado — sin que Hermes tenga que orquestar cada paso
   intermedio? ¿Reemplaza esto a los procesos `-p` de una sola pasada, o los complementa?
2. ¿El `oscar-command-hub` debería ser ese buzón, o necesita una estructura nueva (p.ej. una cola
   de tareas real, no solo markdown consultado a mano)? Ahora mismo lleva 3 días desactualizado.
3. ¿Cómo se le da a Hermes visibilidad real de "carriles libres" — sesiones de Claude Code que Oscar
   ya tiene abiertas en consolas separadas, sin que Hermes pueda controlarlas directamente (fuera de
   su alcance actual, confirmado)?
4. ¿El sistema de roles/AGENTS.md por repo (Coordinador/Implementador/Integrador/Revisor) debería
   extenderse a que Hermes mismo declare y respete un rol al operar (probablemente Coordinador
   siempre, nunca Implementador) — y cómo se aplica/audita eso automáticamente en vez de por
   disciplina manual?
5. ¿Vale la pena instrumentar el propio Hermes con telemetría similar a `~/.claude/telemetry/`
   (qué % de mensajes terminó en delegación real vs. ejecución directa) para medir si las 5 reglas
   nuevas de la skill realmente se cumplen con el tiempo, en vez de confiar en autoevaluación?

## 6. Archivos y referencias reales para la sesión de Claude

- `C:\Users\oscar\.claude\CLAUDE.md` — reglas globales de Claude Code (roles, OASP, subagentes).
- `C:\Users\oscar\oscar-command-hub\CONTEXT.md` — mapa maestro (desactualizado 3 días).
- `C:\Users\oscar\oscar-command-hub\projects\*.md` — estado por proyecto.
- `dev\migrateam\AGENTS.md` — ejemplo más maduro de gobernanza de repo (jerarquía documental,
  protocolo multi-conversación, claims, checklist de patrón) — modelo a seguir/adaptar.
- Skill de Hermes `oscar-multi-agent-orchestration` (`references/diagnostico-proceso-20260927.md`
  contiene el diagnóstico completo con evidencia citada, mismo contenido que la sección 2 de aquí).
- Kanban de Hermes (`mcp__kanban_*`) — histórico real de tareas, para ver el patrón de "bitácora
  posterior" con ejemplos concretos si hace falta profundizar.

---

**Nota final de Hermes**: esto no es una propuesta cerrada — es el diagnóstico honesto de por qué el
sistema actual no está funcionando como Oscar necesita, más su visión en sus propias palabras. La
sesión de Claude debería usarlo para diseñar la arquitectura real (probablemente un cambio de fondo,
no otro parche de reglas), y Oscar decide qué se construye.
