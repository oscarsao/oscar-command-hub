# Auditoría 15-10-2026: aprobaciones Telegram ↔ bot de Trabajos ↔ kanban (t_dddcc232)

Solo diagnóstico: no se ha cambiado código, config ni carriles. Evidencia = archivo:línea de este repo (rama
`lane/t_dddcc232`, base `cf904fa`). **No verificado en vivo**: `%LOCALAPPDATA%\hermes\` (config.yaml, logs, código
del gateway) y el CLI `hermes` están fuera de lo que puede leer un worker; todo lo que dependa de eso va marcado
como *sin verificar*.

## 1. Mapa del flujo hoy

Hay tres bots/canales distintos, y cada uno hace una cosa:

| Canal | Quién es | Entiende texto libre | Para qué se usa en aprobaciones |
|---|---|---|---|
| **Hermes** (DM de Oscar + temas Gestión t5/t230/t231) | LLM, gateway de Hermes | **Sí**, pero en grupos solo en `free_response_topics` (t5, t230, t231); en el resto solo con @mención o respondiéndole (`apply_w3b_config.py:46-49`, `apply_ops_topics.py:16-17`, `decisions/2026-09-27-w3b-mecanismo-temas.md:21-23`). El DM nunca se filtra (`w3b…md:28`). | Intake de trabajo y `clarify` (herramienta incluida en el toolset de Telegram, `apply_hermes_tuning.py:25`). |
| **Bot de Trabajos / carriles** (@pildora_carriles_bot, `CARRILES_BOT_TOKEN`) | Código determinista de este repo, sin LLM | **No** (ver §2) | Avisos `needs_input`/review/integración con botones, `/decisiones`, `/hazlo`, `/sesion`; DM del equipo → tarjeta `[IDEA …]` (`decisions/2026-09-27-telegram-grupos-temas.md:87-97`). |
| **Bot de roles** (Oscar HQ, otro repo) | LLM por rol/tema | Sí, por tema | CEO/CSO/CMO/CFO en Gestión y Marketing. Código fuera de este repo: *sin verificar*. |
| **Panel** kanban.pildoradigital.com | Tarjeta del tablero | n/a | Verdad final de la tarjeta; enlace en cada aviso (`notices.py:288`). |

Rutas por las que una respuesta de Oscar llega al worker:
1. **Botón** del bot de Trabajos → comentario `Respuesta de Oscar: <pregunta> → <resp>` (autor `oscar-telegram`) + `unblock` (`decisions.py:877,907,1080`).
2. **✍️ Responder / Otra / Cambios** → el bot envía un mensaje con `force_reply`; solo vale si Oscar *responde a ese mensaje concreto* (`decisions.py:71,1109-1134`, `_reply` `:534-553`; clave = `chat_id + message_id` del prompt, `:266-278`).
3. **Vía Hermes**: Oscar contesta a Hermes (`clarify`), y Hermes (un LLM) debe escribir en la tarjeta, con perfil `default`, un comentario que **empiece exactamente** por `RESPUESTA-OSCAR:` (`hermes.py:64-70`, README «Responder desde Hermes» `:86-115`). El hilo `lane-hermes-answers` lo recoge cada 60 s (`hermes_answers.py:6-13`).
4. **Panel**: comentar/desbloquear a mano; también como autocuración si todas las preguntas constan respondidas (`hermes_answers.py:15-19,95-132`).

Cuándo Oscar «tiene que ir al panel» (verificado en código):
- Pregunta con más de 5 viñetas o que pasa de 1200 caracteres: el aviso **descarta el resto en silencio** (`notices.py:30-32,164-168`; opciones cortadas a 40 car., `:28,102`). Oscar no ve esas preguntas en Telegram.
- Motivo de bloqueo largo: se guarda `reason[:1500]` (`runner.py:244,418`); si llega a ≥1490 sin cabecera de acciones, la autocuración **falla cerrada** y no desbloquea sola (`hermes_answers.py:104-107`).
- Limitación de guard (`LIMITACION-GUARD:`): no se puede responder, requiere consola (`hermes_answers.py:102-103`).
- Fallos de escritura: «no se pudo aparcar / cerrar la tarjeta (mira el panel)» (`decisions.py:726,748`).
- Tarjetas con opciones fuera de contrato (no 2-4 textos): las opciones se descartan y queda «texto libre» (`notices.py:94,103-104`), que a su vez solo funciona por la ruta 2.

## 2. Por qué «solo un bot responde a algo más que comandos»

Verificado en `DecisionDesk._message` (`decisions.py:421-458`):
- Mensaje de un desconocido en DM → intake de equipo (`collab`), nunca comandos.
- `/comando` → `CommandCenter`.
- Texto → solo se acepta si es **respuesta (reply) a un `force_reply` pendiente** (`:442`, `:534-553`), o un adjunto, o una nota de `/sesion` abierta (`:447-455`).
- Cualquier otro texto de Oscar: **en grupo/tema se ignora sin decir nada** (`_wants_help` exige chat privado, `:460-467`); **en su DM** recibe «Recibí tu mensaje, pero no es un comando y no tengo una respuesta pendiente tuya» + lista de comandos (`:483-485`), con enfriamiento de 30 s (`:469`).

O sea: el bot de Trabajos no tiene el concepto «esta decisión está pendiente en este tema; el texto que escribas va ahí». Si Oscar escribe «sí, adelante» en el tema donde hay un aviso con botones, sin pulsar ni usar *Responder*, el bot no lo asocia. Y en el resto de temas Hermes tampoco contesta salvo mención (W3b), de modo que el texto cae en el vacío. Esto es diseño (`decisions/2026-09-27-telegram-grupos-temas.md:8-9`: «un mensaje, un bot»), no un fallo: la regla evita respuestas dobles, pero deja a Oscar sin interlocutor en la mayoría de temas.

## 3. Alcance de t_22de53c2 y otros puntos donde se pierde una respuesta

**t_22de53c2 (clarify en temas de foro leído como timeout)** — *sin verificar*: el código del gateway de Hermes está fuera del alcance. Lo que sí consta:
- `clarify` está habilitado para Telegram en general (`apply_hermes_tuning.py:25`), y el DM no se filtra (`w3b…md:28`), así que no hay nada en el repo que lo limite a foros por configuración. La clave de sesión de Hermes incluye `chat_id` y `thread_id` (`w3b…md:57-62`), compatible con la hipótesis «la respuesta en un tema llega con otro `thread_id`/formato y la espera de clarify no la casa», pero **es hipótesis**.
- Hipótesis de alcance: solo foros de grupo (el DM no tiene `thread_id`). Para confirmar hace falta el log del gateway del 14-15/10 y la tarjeta.

Otros puntos de pérdida **verificados** en este repo:
- **Respuesta escrita descartada sin aviso**: si Oscar responde a un `force_reply` cuando la decisión ya se había tomado con otro botón, `_reply` solo borra mensajes y devuelve True (`decisions.py:547-549`); su texto se pierde y no se le dice.
- **Relé por LLM**: la ruta 3 depende de que el modelo escriba el prefijo exacto. El propio auditor existe para detectar «comentarios de Hermes con pinta de respuesta sin `RESPUESTA-OSCAR:`» (`auditor.py:23,282`) y el 28-09 ya hubo respuestas apuntadas que el runner no reconocía (`hermes_answers.py:3-4`). Hermes además repetía con `clarify` decisiones que ya tenían botones (`decisions/2026-09-28-ramas-bandeja-monitor.md:37`).
- **Respuesta editada**: el poller pide solo `callback_query` y `message` (`telegram.py:241`); si Oscar edita su respuesta, no se procesa.
- **Equipo sin retorno**: las personas del equipo reciben solo `t_id`, estado y enlace; sin cuerpo ni botones (`telegram-grupos-temas.md:96-97`) y no pueden aprobar nada de producción. Por eso «el equipo no puede interactuar»: está así por diseño y toda decisión pasa por Oscar.
- Menor: `_where` toma `message_thread_id` sin comprobar `is_topic_message` (`decisions.py:496`), a diferencia de `_message` y `CommandCenter._where` (`decisions.py:486`, `commands.py:267-268`). En Gestión (foro) es correcto; en un grupo normal con hilos de respuesta podría dirigir mensajes a un hilo inexistente.

## 4. Fricciones priorizadas

### P1 — Texto libre sin destino (bot de Trabajos ignora o da ayuda; Hermes calla fuera de t5/230/231)
- **Causa raíz**: la única forma de que el texto cuente es contestar con *reply* a un `force_reply` (`decisions.py:442,534`); el resto se ignora (grupo) o se contesta con ayuda (DM). No hay «decisión activa del hilo».
- **Opciones**:
  - A) *Decisión activa por hilo* (recomendada): si en ese chat/tema hay **exactamente una** decisión pendiente (`needs_input` o tarjeta de Oscar) y Oscar escribe texto libre, el bot la trata como respuesta, con eco «Entendido: «…» para t_xxx» y un botón [↩ Deshacer]; con 2+ pendientes pregunta cuál (botones). Es el patrón estándar de ChatOps: *entrada contextual en el hilo* (Slack/Teams responden en el hilo de la petición) más confirmación con deshacer.
  - B) Hacer `force_reply` por defecto en toda pregunta sin opciones, para que Oscar siempre «responda» al prompt (arreglo mínimo, pero sigue exigiendo el reply).
  - C) Unificar en un solo bot con LLM por tema (más cambios y el coste/riesgo de «dos LLM» que la decisión del 27-09 quiso evitar).
- **Recomendación**: A.

### P2 — Dos canales para la misma respuesta, uno basado en un LLM (clarify de Hermes → `RESPUESTA-OSCAR:`)
- **Causa raíz**: la ruta 3 exige formato exacto escrito por un modelo; un fallo = respuesta que el worker no ve y tarjeta bloqueada (`hermes.py:64-70`, `auditor.py:282`). Se solapa con los botones y confunde dónde responder.
- **Opciones**: A) Regla firme: las decisiones con botones **no** se repiten por `clarify`; el relé Hermes→tarjeta pasa a ser una herramienta/skill determinista en vez de un comentario libre (recomendada); B) Hacer que el bot de Trabajos sea el único receptor (junto con P1) y que Hermes, si recibe una respuesta de una decisión pendiente, solo reenvíe «contesta en el tema X»; C) Mantener tal cual con el auditor como red.
- **Recomendación**: A + P1; el auditor se queda como red de seguridad.

### P3 — «Se cortan los textos» → obligado a ir al panel
- **Causa raíz**: tope silencioso de 5 preguntas/1200 car./280 por pregunta/40 por opción (`notices.py:28-32,102,164-168`) y `reason[:1500]` (`runner.py:244,418`). Lo que no cabe desaparece sin marca y la autocuración falla cerrada (`hermes_answers.py:104-107`).
- **Opciones**: A) Marcar siempre el recorte («+2 preguntas más, ver todas») y mandar el resto en un segundo mensaje o con botón [📋 Ver completa] (recomendada; el límite de Telegram es 4096, hoy se usan 1200 en el bloque de preguntas); B) subir el tope a lo que cabe (~3000) y partir mensajes largos; C) pedir al worker que priorice (máx. 3 preguntas) en el contrato.
- **Recomendación**: A, con B como ajuste si A no basta.

### P4 — Respuesta perdida o en foros sin diagnosticar (t_22de53c2 y descartes silenciosos)
- **Causa raíz**: t_22de53c2 en el gateway de Hermes (*sin verificar*) y, en este repo, descartes sin aviso (`decisions.py:547-549`; ediciones no procesadas `telegram.py:241`).
- **Opciones**: A) Arreglar t_22de53c2 con el log real y un test de reproducción en foro (recomendada, es lo que perdió respuestas); B) en el bot de Trabajos, contestar siempre («esa decisión ya estaba tomada: <resultado>», procesar `edited_message`); C) ambas.
- **Recomendación**: C. B es pequeña (<1 h) y puedo abrirla como tarjeta propia; A necesita acceso al gateway.

## 5. Qué falta para cerrar la auditoría
Necesito, redactado y sin secretos: el log del gateway de Hermes (`errors.log` y el de la gateway) de los días 14-15/10 y el `config.yaml` de Hermes (`platforms.telegram` y `clarify`) para confirmar el alcance de t_22de53c2 y qué bots están en cada tema; y el comentario/tarjeta de t_22de53c2. El comentario en la propia tarjeta no se ha podido escribir (el worker no tiene acceso al CLI `hermes`): este documento es el cuerpo del informe.
