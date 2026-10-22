# Tareas del equipo por Telegram

Asignar y seguir tareas entre personas del equipo (Oscar, Ingerber, Andrea, Angel) desde el bot de carriles
(@oscarsaoBot). Las tareas son solo para personas: ningún carril ni worker las ejecuta.

## Mensaje listo para reenviar al equipo

> 📌 **Tareas del equipo en Telegram**
> Ahora podemos asignarnos tareas entre nosotros desde Telegram, y las veremos todos.
>
> • `/asignar @persona Título | detalle` → crea la tarea. Opcional: `!alta` (urgente) y `para:dd-mm` (fecha límite).
>   Ejemplo: `/asignar @ingerber Crear la app de LinkedIn para Postiz | necesito el acceso a Postiz !alta para:28-10`
>   Vale el nombre sin @: `/asignar andrea Revisar los textos del post`.
> • `/mis_tareas` → las tuyas.
> • `/tareas_equipo` → todas las abiertas, por persona.
> • `/hecha t_xxxxxxxx` → la cierras (también con el botón ✅ Hecha del aviso). Solo la cierra quien la tiene asignada,
>   quien la creó u Oscar.
> • Para comentar, responde al mensaje de la tarea o pulsa 💬 Comentar. Para pasarla a otra persona, ↩️ Reasignar.
>
> Cada persona ve sus tareas en el tema «📌 Tareas del equipo» de su grupo. Si el bot te puede escribir por privado
> (haz `/start` al bot una vez), te llega también por DM. Cada día a las 9:00 sale un recordatorio con lo abierto.

## Cómo funciona

- Tablero «equipo» del kanban de Hermes, separado de los carriles. Cada tarjeta lleva asignado, creador, fecha,
  prioridad y fecha límite.
- Publicación: en el tema «📌 Tareas del equipo» del grupo de la persona asignada (`grupo_tareas` en
  `tools/telegram/people.yaml`; los grupos están en `lanes.yaml` → `tareas_equipo.grupos`). Si el asignado no tiene
  grupo (Oscar), en el chat donde se pidió. Si se pidió desde otro grupo, ahí queda una confirmación breve.
- Menciones: `@username` si la persona lo tiene en people.yaml (`username:`); si no, enlace `tg://user?id=` (notifica
  en los grupos donde es miembro).
- María no participa (`tareas_equipo: false`): no se le puede asignar y sus tareas pasan a Oscar.
- El texto de la tarea es dato: nunca crea tareas de carril ni ejecuta nada.

## Puesta en marcha (Oscar)

1. Crear el tablero una vez: `hermes kanban boards create equipo`.
2. Fusionar el PR y reiniciar el runner: `restart --drain` y el smoke test habitual.
3. Probar con la primera tarea real, en el grupo de Marketing o en un DM con el bot:
   `/asignar @ingerber Crear la app de LinkedIn para Postiz`. Comprobar que sale en el tema 📌 de Marketing con
   mención a Ingerber y botones, que `/tareas_equipo` la lista y que Ingerber puede cerrarla con ✅.
4. Ingerber nunca ha escrito al bot: su DM fallará hasta que le dé `/start`; mientras tanto basta el grupo.
   Angel recibirá la mención en Desarrollo solo cuando entre al grupo; hasta entonces, DM si ha hecho `/start`.

## Ajustes (lanes.yaml, bloque `tareas_equipo`)

`activo`, `tablero`, `recordatorio: {activo, hora}` y `grupos: {nombre: {chat, thread}}`. Quitar el bloque apaga todo.
