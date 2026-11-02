# Tareas del equipo por Telegram

Asignar y seguir tareas entre personas del equipo (Oscar, Ingerber, Andrea, Angel) desde el bot de carriles.
Las tareas son solo para personas: ningún carril ni worker las ejecuta. Código: `agent_lanes/equipo.py`.

## Mensaje listo para reenviar al equipo

> 📌 **Tareas del equipo en Telegram**
> • `/asignar @persona Título | detalle` → crea la tarea. Opcional: `!alta` (urgente) y `para:dd-mm` (fecha límite).
>   Vale el nombre sin @: `/asignar andrea Revisar los textos del post`.
> • `/mis_tareas` → las tuyas. `/tareas_equipo` → todas las abiertas, por persona.
> • `/hecha t_xxxxxxxx` → la cierras (o con el botón ✅). Solo el asignado, quien la creó u Oscar.
> • Para comentar, responde al mensaje de la tarea o pulsa 💬. Para pasarla a otra persona, ↩️ Reasignar.
> Cada día a las 9:00 sale un recordatorio con lo abierto.

## Cómo funciona

- Tablero «equipo» del kanban de Hermes, separado de los carriles. Cada tarjeta lleva asignado, creador, fecha,
  prioridad y fecha límite.
- Permisos por persona (`people.yaml`, ejemplo en `tools/telegram/people.example.yaml`): entra quien figura con
  `user_id` (más Oscar). `tareas_equipo: false` la deja fuera (no asignable, sin comandos ni avisos; sus tareas pasan a
  Oscar). `grupo_tareas` = grupo (clave de `lanes.yaml` → `tareas_equipo.grupos`) donde se publican sus tareas.
  `username` (opcional) para la mención; sin él se usa un enlace `tg://user?id=`.
- Un desconocido o quien no participa no usa los comandos: siguen el camino de siempre (rechazo / aviso).
- El texto de la tarea es dato: nunca crea tareas de carril ni ejecuta nada.

## Puesta en marcha (Oscar)

1. Crear el tablero una vez: `hermes kanban boards create equipo`.
2. Rellenar `username` / `grupo_tareas` / `tareas_equipo` en el people.yaml REAL (`.state/people.yaml`).
3. Fusionar el PR, `restart --drain` del runner y probar `/asignar @ingerber …` en el grupo de Marketing.

## Ajustes (lanes.yaml, bloque `tareas_equipo`)

`activo`, `tablero`, `recordatorio: {activo, hora}` y `grupos: {nombre: {chat, thread}}`. Quitar el bloque apaga todo.
