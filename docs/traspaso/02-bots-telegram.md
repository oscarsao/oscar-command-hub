# 02 · Los tres bots de Telegram

Fecha de referencia: 21-10-2026. Hay **tres bots distintos** y la documentación antigua a veces mezcla dos de ellos
(ver `README.md` § Discrepancias, D3). Regla vigente: **un mensaje, un bot que responde** (`decisions/2026-09-27-telegram-grupos-temas.md`).

| | **Hermes** | **Bot de Trabajos / carriles** | **Bot del equipo** |
|---|---|---|---|
| Usuario de Telegram | `@oscarsaoBot` (nombre visible «Pildora Digital», verificado 15-10) | `@pildora_carriles_bot` (nombre visible «Pildora - Trabajos») | `@PildoraD_BOT` (nombre visible «Oscar HQ Avisos») |
| Qué es | Agente LLM hermes-agent **v0.21.4** (gateway en el PC de Oscar, `%LOCALAPPDATA%/hermes`) | Código **determinista, sin LLM** del hub: `tools/agent-lanes/agent_lanes/{commands,decisions,notices,…}.py`, dentro del proceso del runner | Bot de **oscar-hq** en Railway (`src/services/telegram_*.py`), LLM con «sombrero» por tema (CEO/CSO/CMO/CFO/CTO) |
| Para qué | Despacho de Oscar: intake de trabajo → tarjetas del kanban, preguntas (`clarify`), memoria, cron; asesor | Panel de control del sistema de carriles: avisos con botones, aprobaciones, lotes, despliegues, estado, salud | Asistente conversacional del equipo: contenido, imágenes, handbook, Brand Brain, estado de tarjetas, `avisar_a_oscar` |
| Dónde contesta | DM de Oscar; en grupos solo con @mención o respuesta, salvo **Gestión t5, t230, t231** donde responde a todo (`config.yaml:2245-2246`: `require_mention: true`, `free_response_topics: "-1003530490339:5,…:230,…:231"`) | DM de Oscar/María/equipo; tema **Integración y despliegues** (Gestión t398); tema de operaciones de cada marca (t5/t230/t231) para avisos de tareas; ignora el texto libre de los grupos | DMs del equipo; grupo Gestión (temas sin Hermes) y grupo Marketing (todos los temas, sombrero CMO) |
| Token (solo nombre de variable) | `TELEGRAM_BOT_TOKEN` en `%LOCALAPPDATA%/hermes/.env` | `CARRILES_BOT_TOKEN` en `tools/agent-lanes/.env` (hay además `TELEGRAM_BOT_TOKEN` = token de Hermes para avisos sin botones) | Cifrado en Supabase `ohq_telegram_configs.bot_token_encrypted` / variables de Railway |

Grupos: **Gestión** `-1003530490339` (Oscar + María; temas t1 Dirección·CEO, t5 Operaciones·General, t6 Ventas·CSO,
t7 Marketing·Decisiones, t230 Operaciones·MigraTeam, t231 Operaciones·Píldora, t355 Finanzas·CFO, **t398 Integración y
despliegues**) y **Marketing** `-1004277259008` (equipo; t1 General, t74 Planificación, t77 MigraTeam·Contenido, t80
Píldora·Contenido, t83 Recursos). Ids verificados el 17-10 (`decisions/2026-10-15-mapa-reglas-sistema.md` §10) y en
`tools/monitor/monitor.py:44-50`.

## 1. Hermes (@oscarsaoBot)

- **Quién puede usarlo:** por DM, Oscar y María (`TELEGRAM_ALLOWED_USERS` del `.env` de Hermes; María añadida con
  `tools/hermes-config/autorizar_maria.sh` → `autorizar_maria.sh <user_id de María>`). Un desconocido recibe un único
  «asistente privado» y silencio 24 h (`gateway.unauthorized_dm_behavior: decline`, `config.yaml:1035`, aplicado el 11-10 con
  `apply_dm_policy.py`). Memoria 11-10/12-10: María tiene «acceso total» como Oscar por DM, salvo código de MigraTeam (regla de
  proceso, no técnica).
- **Qué hace:** registra tarjetas en el kanban con `assignee` = carril (reglas en su skill y SOUL: buscar en los 4 tableros,
  nunca crear por no encontrar, cabecera «Para Oscar:», reparto código → `claude-<repo>`, operativo → `claude-ops`, solo-Oscar →
  `oscar`; `decisions/2026-09-28-ramas-bandeja-monitor.md` §4). Responde preguntas, hace `clarify` con opciones.
- **Herramientas en Telegram (verificado `config.yaml:1139`):** `browser, clarify, file, image_gen, kanban, memory,
  session_search, skills, terminal, todo, tts, vision, web, no_mcp`. **Sí tiene `terminal`** (contrario a lo que dicen
  `CONTEXT.md` y la decisión del 27-09; se volvió a activar el 12-10, backup `config.yaml.bak-20261012-terminal`). La regla «Hermes
  no implementa» es de proceso, no está aplicada en las herramientas.
- `busy_input_mode: queue` (`config.yaml:1646`): los mensajes mientras trabaja se encolan, no interrumpen.
- **Comandos:** los propios de hermes-agent (`/new` para reiniciar la sesión larga del DM y recargar reglas de la skill,
  `/help`…). No se han documentado comandos personalizados en este repo.
- **Cómo responder desde Hermes a una pregunta de un carril:** Hermes debe comentar la tarjeta con el prefijo exacto
  `RESPUESTA-OSCAR: …` con perfil `default`; el hilo `lane-hermes-answers` del runner lo recoge cada 60 s
  (`tools/agent-lanes/README.md` § Responder desde Hermes). Un comentario sin ese prefijo **no cuenta** (el auditor diario lo detecta).

### Límites y bugs conocidos de Hermes

| Bug / límite | Detalle | Qué hacer | Fuente |
|---|---|---|---|
| **Contesta a todo en temas creados por él** | El adaptador ve el mensaje de creación del tema como `reply_to_message`, así que se salta `require_mention`. `ignored_threads` es global y no sirve (los ids 5/6/7 existen en los dos grupos) | **Crear temas siempre con el bot de Trabajos** (`tools/telegram/recrear_temas.py`), nunca con Hermes. Los temas viejos se renombraron «🗄 Archivado» | `recrear_temas.py:1-10`, memoria `feedback_hermes_topic_creator_reply_bug` |
| **Trampa del triage del kanban** | `block_loop_detected`: una tarea bloqueada dos veces por el mismo motivo pasa a `triage`; desde ahí `unblock`/`promote`/`block` fallan («not blocked») y la tarea queda muerta sin aviso | Sacarla con `specify_triage_task` de la API de Hermes (no `hermes kanban specify`, que reescribe título y cuerpo con un LLM). Ya automatizado: botón `[🔄 Reintentar]`/`[🧊 Sacar de triage]` del bot de Trabajos → `HermesCLI.requeue_triage` (`agent_lanes/hermes.py:416-433`); el aviso 🧊 (`aviso.py`) y la cola del integrador (`guard_route.py`) recogen estos casos. Antes, arreglar la causa del bloqueo repetido | memoria `feedback_hermes_kanban_triage_trap`, `README.md` § Aviso 🧊 |
| `clarify` en temas de foro leído como timeout (**t_22de53c2**) | Respuestas de Oscar en temas perdidas; causa en el gateway, **sin verificar** (el código del gateway está fuera del alcance de los workers) | Responder con los botones del bot de Trabajos o en el DM; pendiente arreglar con el log real | `docs/auditoria-aprobaciones-telegram-2026-10-15.md` §3-§4 P4 |
| Buscaba solo en el board `default` y creaba duplicados; asignaba operativo a carriles de código; repetía con `clarify` decisiones que ya tenían botones | Reglas añadidas a la skill el 28-09; requieren `/new` en el DM para cargarlas | Si reaparece: `/new` y revisar la skill | `decisions/2026-09-28-ramas-bandeja-monitor.md` §4 |
| El heartbeat por CLI no extiende el claim | Por eso el runner pide el lease de una vez (`claim --ttl = max_runtime + 300`) | No tocar | `lanes.yaml:63-65` |
| `kanban init lock … not acquired within 10s` | Avisos MEDIA del monitor el 21-10 (contención de SQLite con runner + Hermes + panel) | Vigilar; sin tarjeta | `tools/monitor/.state/alerts.log` 21-10 09:34 |
| `config.yaml` contiene secretos en claro (hash de contraseña del panel, secret del dashboard, API key de ClickUp) | Pendiente rotar | No volcar nunca ese fichero; los scripts `tools/hermes-config/apply_*.py` hacen backup y no imprimen valores | `decisions/2026-10-15-mapa-reglas-sistema.md` §6 |
| Hermes borró un Space de ClickUp en el pasado | Por eso ClickUp no tiene escritura amplia desde Hermes | — | `decisions/2026-09-27-arquitectura-carriles.md` §4 |

Config del gateway: `%LOCALAPPDATA%/hermes/config.yaml` (hay backups `config.yaml.bak-2026MMDD-*`); scripts idempotentes con
dry-run y backup en `tools/hermes-config/` (`apply_w3b_config.py`, `apply_ops_topics.py`, `apply_dm_policy.py`,
`apply_telegram_quiet.py`, `apply_hermes_tuning.py`, `add_allowed_user.py`). Se ejecutan con el Python del venv de Hermes
(`%LOCALAPPDATA%/hermes/hermes-agent/venv/Scripts/python.exe`) y después `hermes gateway restart`. Las versiones de hermes-agent
se fijan en 0.21.4; el runner comprueba al arrancar el contrato de la CLI (`runner.py --check`) y no arranca si cambia.

## 2. Bot de Trabajos / carriles (@pildora_carriles_bot, «Pildora - Trabajos»)

- **Código:** `tools/agent-lanes/agent_lanes/commands.py` (comandos), `decisions.py` (botones/callbacks, `DecisionDesk`),
  `notices.py` (formato de avisos), `collab_intake.py` (DM del equipo), `free_text.py`, `falta.py`, `flujo.py`, `sesion.py`,
  `aviso.py` (🧊), `guard_route.py` (cola del integrador), `reminders.py` (13:00 y 18:00), `brief/` (08:00). Corre **dentro del
  runner**: si el runner está parado, no hay botones ni avisos.
- **Quién puede usarlo** (`people.py`, `people.yaml`, `commands.py:1-5`):
  - **Operadores** = Oscar (`OWNER_TELEGRAM_ID`) + quien tenga `operador: true` en `tools/telegram/people.yaml` → hoy **solo María**
    (decisión 19-10, commit `a41da56` del 20-10): comandos, botones y aprobaciones (`/aprobar`, `/desplegar`, `/lote`, botones
    `int_*`, fusionar, `✅ Migración aplicada`), por DM y en grupo; sus acciones quedan firmadas con su nombre en la tarjeta.
  - **Equipo conocido sin `operador`** (Ingerber, Andrea): solo DM → cada mensaje de texto se convierte en una tarjeta
    `[IDEA de <nombre>] …` asignada a `oscar` en el tablero de su primera marca; recibe «Apuntado t_xxx» con enlace al panel; tope
    20 ideas/hora; solo texto (fotos/audio no); los avisos de vuelta solo llevan id, estado y enlace. Un comando recibe
    `NOT_OWNER` («solo para operadores. Pide a Oscar o a María…», `commands.py:90`).
  - **Desconocidos:** «No tienes acceso a este bot. Pídeselo a Oscar.» como mucho cada 6 h.
  - Alta de una persona: Oscar edita `people.yaml` (`user_id`, `role` ∈ coo/cmo/marketing, `brands`), añade el id a
    `TELEGRAM_ALLOWED_CHATS` del `.env` (para que los avisos vuelvan a su DM) y la persona envía `/start`. **No** añadirla a
    `generic_origins` de `lanes.yaml`.
- **Comandos** (`commands.py:69-87`, registrados con `setMyCommands`; en grupos con privacy mode usar `/cmd@pildora_carriles_bot`):

| Comando | Qué hace |
|---|---|
| `/hoy` | El resumen de las 08:00 ahora (por carril: hecho, bloqueado, espera a Oscar, coste) |
| `/decisiones` | Bandeja: las 5 más importantes (primero lo que bloquea agentes), botones A/B/C/`⭐ Recomendada`/`✍️ Otra`/`🗄 Aparcar`/`💬 Explícame más`, `✅ Aceptar todo lo recomendado`, 🧊 atascadas con `🔄 Reintentar` |
| `/atencion` | Todo lo que espera a Oscar, con botones (sacar de triage, aprobar, responder, archivar) |
| `/integrador` | Cola de bloqueos de guard que requieren consola (ver 04) |
| `/aprobar` | Tareas listas para integrar: `[✅ Aprobar] [🔁 Pedir cambios] [🗄 Aparcar]` |
| `/tareas [migrateam\|pildora\|nextjobs]` · `/tarea t_xxx` | En curso y en cola por carril · ficha de una tarea con su árbol padre/hijas |
| `/preparar t_xxx \| todas` | Claude (Sonnet, ≤0,5 $) redacta contexto y 3 opciones para una tarjeta de decisión |
| `/hazlo <texto>` | Tarea pequeña (prioridad 10, tope 15 min); carril por palabras clave o botones |
| `/estado [marca]` · `/falta [marca]` | En curso / espera a Oscar / pendiente de desplegar · checklist de lo que frena una salida |
| `/desplegar oscarhq` | Ficha de lo fusionado sin desplegar → `[🚀 Desplegar]` → «¿Seguro?» (doble toque) |
| `/promover migrateam` | PR `develop→master` con checks, `alembic_version` de producción por `/health` y doble confirmación |
| `/lote migrateam\|oscarhq` | Monta ya el lote de ramas aprobadas (si no, a las 08:30) |
| `/salud` · `/ram` | Runner, Hermes, servicios, alertas, develop↔master, RAM/CPU · RAM ahora y top 5 procesos |
| `/sesion [nombre]` … `fin \| cancelar \| estado` | Sesión de correcciones agrupadas → una tarjeta `[CORRECCIONES]` |
| `/flujo <texto>` | Cadena de hasta 8 tarjetas propuestas por Claude; se crean al pulsar `[Crear todo]` |
| `/start`, `/ayuda`, `/help` | Lista de comandos |

- **Texto libre de Oscar en un tema** (`free_text` en `lanes.yaml:40`, P1 de la auditoría): si hay exactamente una decisión
  pendiente en ese chat/tema (72 h), el texto cuenta como respuesta con eco y `Deshacer`; con varias, pregunta cuál. Nunca
  promueve/fusiona/despliega. En el DM, un texto suelto sin decisión pendiente se contesta con Haiku sin herramientas
  (`explain.py`), solo informa.
- **Avisos:** destino por prioridad (`lanes.yaml:15-22`): línea `Origen-Telegram` de la tarjeta → tema de operaciones del carril →
  `TELEGRAM_CHAT_ID/THREAD_ID` del `.env`. Fichas de PR/merge/deploy siempre en **t398 Integración** con copia espejo al DM y mensaje
  fijado con lo pendiente. Topes de recorte en `lanes.yaml` → `notices:` (nada se recorta en silencio desde el 17-10, #41).
- **Capturas:** fotos/documentos de Oscar al bot → se adjuntan a la `/sesion` abierta o crean tarjeta `[CAPTURA]` (#21).

### Límites y bugs conocidos del bot de Trabajos

| Límite / bug | Detalle | Fuente |
|---|---|---|
| Sin LLM conversacional: en grupo ignora el texto que no sea comando, reply a un `force_reply` o respuesta a la única decisión pendiente | Diseño («un mensaje, un bot»); P1 ya mitigado con `free_text` | auditoría 15-10 §2, §4 P1 |
| Respuesta a un `force_reply` cuando la decisión ya se tomó con otro botón: se descarta sin aviso | Pendiente (P4-B) | auditoría §3 |
| Mensajes editados (`edited_message`) no se procesan | El poller pide solo `callback_query` y `message` (`telegram.py`) | auditoría §3 |
| Botón `Reintentar` desaparece | PR **#44** abierto (`lane/t_370b903b`, «[BUG] Bot de carriles 'Pildora - Trabajos': botón Reintentar desaparec…») | `gh pr list` 21-10 |
| `getUpdates falló: The read operation timed out` | Cortes de red de Telegram, transitorios; el auditor no los cuenta | `runner.log` 21-10 11:38 |
| Tema **Atención** (@mención + recordatorio 24 h, #48 fusionado el 21-10) | Requiere el bloque `attention_telegram:` en `lanes.yaml`; **el checkout vivo no lo tiene** → función inactiva hasta configurarla y aplicar | `runner.py:106-115`, `grep attention lanes.yaml` 21-10 |
| Con el runner parado no hay bot | Es el mismo proceso | `runner.py` |

## 3. Bot del equipo (@PildoraD_BOT, «Oscar HQ Avisos»)

- **Código:** repo oscar-hq, `src/services/telegram_tools.py`, `telegram_aviso.py`, `telegram_crews.py`, `telegram_context.py`;
  config en Supabase (`ohq_telegram_configs.role_routes`, `ohq_people`, `ohq_system_settings`) editable desde el panel
  (Ajustes → Integraciones → Telegram). Corre en Railway: **funciona con el PC apagado**, pero su copia del kanban (snapshot
  cada 15 min desde el PC) se queda vieja sin avisar.
- **Quién:** cualquier persona activa en `ohq_people` (Oscar fundador, María COO, Andrea marketing, Ingerber CMO) con tope diario
  en USD (`tope_diario_usd`: 20/3/5/10) y auditoría en `ohq_tool_audit`. Sombrero por tema (`role_routes`): Gestión t1 CEO,
  t6 CSO, t7 CMO, t355 CFO; Marketing todo CMO; t5/t230/t231 CTO = Hermes; t398 sin ruta (cae a CEO).
- **Herramientas** (lista blanca por ruta, exclusiones desde el panel): `generar_imagen`, `redactar_texto`, `avisar_a_oscar`
  (crea `[IDEA]` + DM a los chats de `telegram_aviso_oscar_dm`), `leer_handbook`, `consultar_brand_brain`, `ver_web`,
  `estado_tarjeta`, cola de outreach; fase C (`ejecutar_crew`, `crear_tarjeta`, t_69474e51) en marcha.
- **Raíles de código que ninguna configuración abre** (`telegram_tools.py:95-97` `HARD_RAILS`): `enviar`, `publicar`,
  `desplegar`, `fusionar`, `variables_entorno`, `borrar`.
- **Decisiones 17-10** (`mapa-reglas` §9): el bot del equipo es «la puerta de todos»; regla «una voz por mensaje» (calla si el
  mensaje va a otro bot, t_9a20565e); autonomía por riesgo (BAJO: blog/textos lo fusionan Ingerber y María tras revisión
  automática; MEDIO: código sin migración, Oscar fusiona; ALTO: migraciones/producción/variables/dinero/enviar solo Oscar);
  matriz persona × recurso (t_2f6aefdd, migración `20261017_telegram_permissions_matrix.sql` **sin aplicar** según CHANGELOG 17-10).
- **Bugs conocidos:** `generar_imagen` interpretó «nano banana» como contenido, no cargó contexto de marca e inventó un dominio
  (t_d9360c8b, retenida); tarjetas `ready` asignadas a Oscar creadas desde Telegram no le avisaban (t_8e370853); `ruta t148`
  obsoleta en `role_routes`.

## 4. Quién decide qué en Telegram (resumen)

| Acción | Quién | Cómo |
|---|---|---|
| Crear tarjetas/ideas | Cualquiera del equipo (idea → `oscar`), Oscar y María (tareas de carril) | DM a los bots, `/hazlo`, Hermes |
| Responder preguntas de un carril (`needs_input`) | Oscar (botones, `RESPUESTA-OSCAR` vía Hermes); María como operadora | Bot de Trabajos |
| Aprobar PR, fusionar, lote, desplegar Oscar HQ, migración aplicada, promover MigraTeam | **Oscar**; **María** desde el 19/20-10 como operadora (los documentos del 13-10 todavía dicen «solo Oscar») | Botones `int_*`, `/aprobar`, `/desplegar`, `/promover` |
| Cambiar quién tiene acceso | Solo Oscar, a mano (`people.yaml` + `.env`; `ohq_people` en el panel) | Nunca desde el chat |
