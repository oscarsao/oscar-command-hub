# Spec-Lite · Telegram: decidir y cerrar todo sin volver al PC (t_40fe70c3)

Modo OASP: **Spec-Lite (2-3 días)**. No se implementa hasta el OK de Oscar. Son 7 puntos que tocan
`decisions.py`, `commands.py`, `notices.py`, `reminders.py`, `integrator.py` y añaden estado persistente nuevo.

## Qué ya existe (no se rehace)
- `keyboard_spec` (decisions.py): botones por pregunta `⭐ 1) texto`, `✍️ Otra respuesta` (force_reply), `🗄 Aparcar`, `💬 Explícame más`.
- `/decisiones` ya tiene cabecera `ACCEPT_ALL` ("Aceptar todo lo recomendado") y una tarjeta por tarea, máx. 20 (`MAX_CARDS`).
- `notices.is_decision_card` distingue las tarjetas [DECISIÓN]/[SEMANA]/[IDEA] (hoy solo se listan, sin botones propios).
- `integrator.py` emite 🚀 Desplegar / ✅ Migración aplicada con `store.issue` (hoy edita la ficha, no manda mensaje nuevo).
- `reminders.py` tiene slots horarios con ventana; sirve para el lunes 08:00.

## Diseño propuesto, por punto
1. **Opciones escritas.** `questions_block` ya redacta las preguntas: pasar a `A) texto ⭐`, `B) texto`. `keyboard_spec` →
   una fila `[A][B][C]` + `[⭐ Recomendada][✍️ Otra]`. El `OPTION` sigue guardando `index`; la respuesta registrada sale de
   `q["options"][index]` (texto, no letra; ya es así en `_option_answer`, se cubre con test). Con >6 opciones se parte en filas.
2. **"✅ Acepto todas las recomendadas" en tarjeta multi-pregunta.** Reutilizar `_accept_all`/`_group_act` limitado a los
   miembros de la propia tarjeta; solo si todas las preguntas pendientes tienen recomendada (si no, el botón no aparece).
3. **Tarjetas de decisión de Oscar.** Nuevo `keyboard_spec("decision_card")` con acciones `help`, `done`, `snooze`,
   `discard`, `delegate`:
   - 💬 Ayúdame a decidir → crea tarea en `claude-ops` (título "Investigar decisión t_xxx", cuerpo con la ficha), parent = ficha.
   - ✅ Hecho → complete con nota "hecho desde Telegram". 🗑 Descartar → archive.
   - 🗓 Aparcar 2 semanas → `snooze_until` en `.state/snooze.json` (clave tarea → fecha ISO) + tarea a `blocked/parked`
     en el perfil `oscar`; un tick (el de `Reminders`) la devuelve y reenvía la ficha al llegar la fecha.
   - 🤖 Que lo haga un agente → reasigna a claude-ops, o al carril cuyo `repo/tags` coincida (tabla en `lanes.yaml`; si
     hay duda, claude-ops).
4. **Botón nuevo ⇒ mensaje nuevo.** `TaskNotices`/`MessageStore` guarda los tokens de botones por ficha; al editar, si el
   conjunto de acciones gana alguna que no tenía, `send` de un mensaje corto ("🚀 t_xxx lista para desplegar") con ese teclado
   al hilo del tema y al DM, además de editar la ficha. Un solo mensaje por botón nuevo (marca en el store).
5. **`/decisiones` top 5.** Orden por importancia (necesito confirmar criterio, ver pregunta 2). Un mensaje por decisión +
   línea final "N más · ver todas" con botón `Ver todas` (el comportamiento actual, tope 20).
6. **Límite WIP.** `.state/wip.json`: máx. 5 tarjetas activas a nombre de Oscar; al superar, las más antiguas / menos
   prioritarias pasan a aparcadas (mismo mecanismo de snooze) con UN aviso por barrido. **Lunes 08:00**: mensaje
   "Elige tus 3 prioridades" con botones por tarjeta candidata (toggle ☐/☑ hasta 3, luego `Confirmar`); las no elegidas
   siguen activas pero fuera del top 5.
7. **Tests** (dobles, sin red): uno por punto + regresión de needs_input, integración y aparcar.
   Documento `docs/telegram-decisiones-v2.md` con capturas de texto de cada mensaje.

## Rabbit holes
- Callback_data ≤64 B y un token consume una vez: los teclados nuevos usan el mismo `CallbackStore`.
- Crear tarea en claude-ops y reasignar necesita `hermes` CLI con el perfil correcto; solo con dobles en tests.
- Estado (`snooze`, `wip`) vive en `.state/` (no se versiona) y debe ser tolerante a archivo corrupto.
- Reglas de seguridad: nada de tocar hooks ni contrato; no hace falta.

## Preguntas abiertas
Ver `questions` del resultado de la tarea (criterio de importancia, regla del tope de 5, destino de "Que lo haga un agente").
