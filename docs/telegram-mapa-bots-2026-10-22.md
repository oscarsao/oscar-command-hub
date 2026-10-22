# Telegram: diagnóstico del bot «Avisos Oscar HQ» y mapa de bots, grupos y DMs (22-10-2026)

Revisión en solo lectura: no se ha enviado ningún mensaje ni se ha cambiado ningún webhook, configuración o código.

**Fuentes:**
- `getMe`, `getWebhookInfo`, `getChatMember` y `getMyCommands` de cada token.
- Tablas `ohq_telegram_messages`, `ohq_telegram_configs`, `ohq_kanban_cards` y `ohq_system_settings` de Supabase de Oscar HQ.
- Logs del servicio `oscar-hq` en pildora-01, solo desde la migración del 22-10; los de Railway no están disponibles.
- `lanes.yaml`, `tools/monitor/monitor.py` y `config.yaml`/`.env` de Hermes (solo nombres, nunca valores).
- `decisions/2026-10-15-arquitectura-bots-equipo.md`.

## 1. El bot «Avisos Oscar HQ» (@PildoraD_BOT)

- Telegram sigue mostrando el nombre «**Agente Píldora Avisos**»: `getMe` devuelve ese `first_name`. El cambio de nombre se hizo en el panel (`ohq_telegram_configs.bot_name = "Oscar HQ Avisos"`), no en @BotFather. Para que el equipo lo vea con el nombre nuevo, hay que cambiarlo también en @BotFather con `/setname`.
- Es el bot conversacional del equipo, el «bot de roles» de Oscar HQ. Responde texto libre como CEO, CMO, COO o CTO y tiene herramientas: generar imagen, `avisar_a_oscar`, handbook y crews. También es el canal de todos los avisos de Oscar HQ:
  - leads (`telegram_lead_alert_chat_ids` = Gestión y DM de Oscar);
  - `avisar_a_oscar` (DM de Oscar);
  - digests de crews, Centro de IA, actividad del panel, informe de fallos y asistente PM.
- **Webhook:** `https://api.pildoradigital.com/api/telegram/webhook`, sin errores ni pendientes. El servidor responde 200 a todos los envíos.

### Problemas encontrados (con evidencia)

| # | Problema | Evidencia | Causa | Arreglo |
|---|---|---|---|---|
| 1 | **Desde la migración el bot ha perdido su configuración de roles, marcas y personas** | El 21-10, en Railway, Andrea en Marketing se atendía como `cmo` y marca `holding`. El 22-10, en pildora-01, todos los mensajes de Marketing (de Andrea y de Oscar) salen como `ceo` y marca `migrateam`. | Al mover el webhook se registró con el secreto de la variable `TELEGRAM_WEBHOOK_SECRET`, que **no coincide** con el de la fila de `ohq_telegram_configs`: se ha comprobado sin mostrar valores, y no coinciden ni el hash ni el texto en claro. El webhook busca primero la config por ese secreto, no la encuentra y cae al **modo antiguo por variables de entorno**. En ese modo no hay `role_routes`, ni guías por tema, ni personas: el rol cae a CEO y la marca a la de reserva. | Volver a registrar el webhook con el secreto de la BD. Desde el panel, Ajustes → Telegram → guardar la config de `pildora_digital_inbound` (`POST /api/telegram/config/{brand}` reutiliza el secreto existente y llama a `setWebhook`). Después, comprobar con un mensaje en Marketing que el registro vuelve a decir `cmo`/`holding`. |
| 2 | **Los DMs de María, Andrea e Ingerber con este bot se ignoran** | En modo antiguo, los chats permitidos salen de la variable `TELEGRAM_ALLOWED_CHAT_IDS` (DM de Oscar, Gestión y Marketing). La lista de la BD, que sí incluye a María, Andrea e Ingerber, no se usa. | La misma del punto 1. | Se arregla con el punto 1. |
| 3 | **El grupo Desarrollo se ignora** | Log del 22-10: `Telegram: ignoring message from non-whitelisted chat -1003731090094`. Angel (`8770041085`) tampoco está en la lista de la BD. | Ni la variable ni la BD incluyen Desarrollo ni a Angel. | Si Angel debe usar este bot, añadir el chat de Desarrollo y su id en el panel (Telegram → chats permitidos). Si Desarrollo es solo para Hermes, dejarlo así. |
| 4 | **Afirma cosas que no ha hecho (alucinaciones)** | (a) 19-10 21:18: a María, que preguntaba por la carga de PDF en NextJobs, le responde «Listo: la imagen ya está en el tema». (b) 21-10 21:29 y 22-10 13:56: dice «He creado la tarjeta **t_2JQKZRMHXBE9V** / **t_2JQKZXGJYBE9V**», y esos ids no existen en `ohq_kanban_cards` ni en ningún tablero de Hermes. (c) 19-10 21:32: «Tarjeta creada (ID: pendiente de confirmación del sistema)». | El modelo (Sonnet 4.5) responde sin resultado real de la herramienta. La regla «nada verificado sin herramienta en el turno» (#113) no cubre los ids de tarjeta. | Que la respuesta tras `crear_tarjeta`/`avisar_a_oscar` la componga el código con el id devuelto, no el modelo, y prohibir citar ids que no vengan de la herramienta. |
| 5 | **Tarjetas duplicadas** | 21-10 20:53: dos tarjetas `[IDEA]` casi iguales en el mismo minuto (`b53fa834…` y `41f63430…`) por «Lo último» y «Avísale a Oscar». | `avisar_a_oscar` no comprueba si ya avisó de lo mismo poco antes. | Deduplicar por autor, chat y similitud del texto en una ventana de unos 10 minutos (ventana en ajustes). |
| 6 | **No lee lo que el equipo le manda** | Andrea comparte Google Docs y el bot responde «He visto los enlaces», pero no puede leerlos. María manda un audio y un PDF: «no puedo reproducir ni analizar archivos de audio» / «no puedo leer archivos PDF». A Oscar: «No tengo acceso al historial… de lo que Andrea haya enviado». | No tiene herramienta para leer Drive, PDF ni voz, y la memoria va por chat (lo del grupo no lo ve en el DM). | Añadir herramientas (PDF y Docs públicos o Drive con permisos, transcripción de voz) y un contexto compartido por marca entre grupo y DM. Encaja con la fase B de la decisión del 15-10. |
| 7 | **Personalidad y destinatario confusos** | 22-10 14:26: Oscar le habla y responde «Andrea, Oscar necesita que…». También usa «Quedo atenta» en femenino, como si fuera Andrea. | Con el modo antiguo (punto 1) no sabe quién escribe: sin directorio de personas. | Se arregla con el punto 1. |
| 8 | **El botón «calidad» se registra mal** | 22-10 14:33: filas con `message_text = reply_text` y `model = None`, de 0,04 $ y 0,0119 $. | El registro del callback copia el texto de entrada en la respuesta. | Menor: registrar la respuesta real del callback. |
| 9 | **Sin menú de comandos** | `getMyCommands` = 0. Los otros dos bots tienen 60 y 27. | Nunca se registraron. | Registrar los comandos útiles (`/marca`, `/rol`, `/imagen`, `/ayuda`) con `setMyCommands` por ámbito (grupos o DM). |
| 10 | **Un tema nuevo sin guía** | Log: `Telegram: tema -1004277259008/275 sin guia (📌 Tareas del equipo)`. | El tema se creó hoy y no tiene `role_routes.threads[275]`. | Asignarle rol o «primary: carriles» en el panel cuando exista el sistema de tareas, para que este bot no conteste ahí. |

Ninguno de los avisos de este bot menciona ya Railway: las URLs se cambiaron en #243. Las alertas viejas de Railway las mandaba el monitor del hub, que ya está reiniciado.

## 2. Mapa de bots

| Bot | Nombre visible | Corre en | Lo usan | Publica en | Para qué sirve | Recomendación |
|---|---|---|---|---|---|---|
| **@PildoraD_BOT** | «Agente Píldora Avisos» (en el panel, «Oscar HQ Avisos») | Oscar HQ, servidor (webhook) | Equipo (Andrea, Ingerber, María) y Oscar | Gestión (leads, roles por tema), Marketing (CMO), DMs, DM de Oscar (`avisar_a_oscar`, Centro de IA, fallos, digests) | **Asistente del equipo + avisos de negocio** | **Mantener** como la única puerta del equipo. Arreglar el punto 1 ya. Renombrar en @BotFather. |
| **@oscarsaoBot** | «Pildora Digital» | Hermes, en el PC (polling) | Oscar; Angel (Desarrollo, respuesta libre); Andrea en grupos (`TELEGRAM_GROUP_ALLOWED_USERS`) | DM de Oscar (canal principal), Gestión temas 5/230/231 (respuesta libre), Desarrollo (respuesta libre), resto con mención | **Coordinador** con herramientas reales: archivos, terminal, kanban, navegador | **Mantener** para Oscar, María y Desarrollo. **Ojo:** `tools/agent-lanes/.env` usa también este token como `TELEGRAM_BOT_TOKEN` (reserva sin botones). Los temas «📌 Tareas del equipo» y la invitación a Angel del 22-10 salieron con este bot, no con el de carriles. |
| **@pildora_carriles_bot** | «Pildora - Trabajos» | Runner de carriles, en el PC (polling) | Oscar, María (operadora), Angel (operador, acotado) | Gestión: tema 5 (Ops·General), 398 (Integración y despliegues), 230/231. DM de Oscar: alertas ALTA del monitor, porque el monitor usa `CARRILES_BOT_TOKEN`. | **Control de carriles:** botones, aprobaciones, `/estado`, `/lote`, `/decisiones` | **Mantener.** **No está en el grupo Desarrollo** (`chat not found`): añadirlo si las tareas del equipo o los avisos de Angel van a salir por él. |
| Monitor | — (no es un bot propio) | PC | — | DM de Oscar, vía el bot de carriles | Alertas ALTA (salud de servicios, RAM, runner) | Sin cambios. Se puede pasar al servidor más adelante (opcional). |

## 3. Mapa de grupos y temas

| Chat | Qué es | Bots dentro (todos admin) | Quién habla | Temas |
|---|---|---|---|---|
| **Gestión Píldora Digital** `-1003530490339` | Dirección: Oscar y María | Avisos, Hermes, Trabajos | Oscar, María | General/Dirección (CEO, Avisos), 5 Ops·General (Hermes libre + Trabajos), 6 Ventas (CSO, Avisos), 7 Mkt·Decisiones (CMO, Avisos), 230 Ops·MigraTeam (Hermes), 231 Ops·Píldora (Hermes), 355 Finanzas (CFO), 398 🚦 Integración y despliegues (Trabajos) |
| **Marketing Píldora Digital** `-1004277259008` | Marketing: Oscar, Ingerber, Andrea | Avisos, Hermes, Trabajos | Andrea, Ingerber, Oscar | General (CMO, Avisos), 74 Planificación, 77 MigraTeam, 80 Píldora, 83 Recursos, **275 📌 Tareas del equipo** (nuevo, sin guía) |
| **Desarrollo Pildora** `-1003731090094` | Desarrollo: Angel (invitado el 22-10, aún no ha entrado) | Avisos (ignora el grupo), Hermes (respuesta libre) | Angel, Oscar | **9 📌 Tareas del equipo** (nuevo) |
| DM de Oscar `6744452215` | — | Los tres | — | Avisos (`avisar_a_oscar`, leads, Centro de IA), Hermes (canal principal), Trabajos (decisiones y alertas ALTA) |
| DMs de María, Andrea e Ingerber | — | Avisos y Trabajos (y Hermes para María) | — | Hoy, **por el punto 1, los DMs con Avisos se ignoran** |

## 4. Solapamientos

- **Los tres bots están en Gestión y Marketing**. Hay respuestas dobles cuando alguien menciona a Hermes en un tema que Avisos también atiende.
- **«Tarjetas»:** existen **tres sistemas que crean tarjetas o avisos para Oscar**:
  - el kanban de equipo de Oscar HQ (`ohq_kanban_cards`, con `avisar_a_oscar` y botones `kq:`, incluida la **asignación a personas**, t_0b31623c);
  - el kanban de Hermes (carriles);
  - las ideas `[IDEA]` de los carriles.

  La nueva tarjeta t_89163bdb («tareas del equipo por Telegram» en el bot de carriles) **duplica** lo que Oscar HQ ya tiene en `kanban_equipo.py` (asignar, avisar con botones Empiezo/Hecho/Bloqueada/Asignar).
- **Avisos para Oscar** llegan por los tres bots al mismo DM.

## 5. Propuesta de simplificación

1. **Una puerta por audiencia.** El equipo habla solo con **Avisos (Oscar HQ)**. Oscar y María usan **Hermes** para conversar y **Trabajos** para botones y aprobaciones de carriles.
2. **Arreglar hoy el webhook de Avisos** (punto 1): recupera roles, marcas, personas y los DMs del equipo.
3. **Tareas del equipo en un solo sitio:** reutilizar el kanban de equipo de Oscar HQ (ya asigna y avisa con botones) en lugar de construir otro en el bot de carriles. Reorientar t_89163bdb a «exponer en Telegram el kanban de equipo de Oscar HQ (/asignar, /mis_tareas) y publicar en el tema 📌 de cada grupo».
4. **Avisos a Oscar por un solo canal.** Los de negocio, por Avisos; los de sistema, por Trabajos. Hermes no avisa: conversa.
5. **Silenciar a Avisos en los temas de Hermes y de carriles** (`primary: hermes` ya existe, más un `primary: carriles`) para que no haya respuestas dobles.
6. **Renombrar en @BotFather:** «Avisos Oscar HQ» (asistente del equipo), «Hermes · Pildora» (en lugar de «Pildora Digital») y «Trabajos · carriles».
7. **Herramientas de lectura** para el bot del equipo: PDF, Docs, voz e historial por marca. Es la fase B del 15-10.

## 6. Decisiones para Oscar

1. **¿Arreglo ya el webhook de Avisos?** (guardar la config del bot desde el panel o volver a registrar el webhook con el secreto de la BD). A) Sí, ahora (recomendado). B) Lo hago yo desde el panel.
2. **Tareas del equipo:** A) Reutilizar el kanban de equipo de Oscar HQ y que Telegram lo exponga (recomendado). B) Seguir con t_89163bdb en el bot de carriles tal como está.
3. **Angel y Desarrollo:** A) Angel habla con Hermes en Desarrollo y Avisos no entra (recomendado). B) Añadir Desarrollo y a Angel también a Avisos.
4. **Nombres en @BotFather:** A) Renombrar los tres como en el punto 6 de la propuesta (lo haces tú en @BotFather; recomendado). B) Dejarlos.
5. **Respuestas dobles:** A) Avisos calla en los temas de Hermes y carriles (recomendado). B) Dejarlo como está.
