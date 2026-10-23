# Spec-Lite · Avisos «listo para revisar» agrupados + silencio (t_c1988acc)

Origen: petición de Oscar del 23-10 (tema Gestión · Operaciones General, t5). Estado: **pendiente de tu OK, nada implementado**.

## Problema
Cada tarjeta que termina manda su propio aviso con notificación. Los que suenan hoy:
- `ReviewRunner.notify("done", alert=True)` (`agent_lanes/review.py:300`, «lista para merge»): ficha en Integración + copia al DM, con ✅ Aprobar. `renotify.py:477` lo reemite.
- Carril ops (`agent_lanes/runner.py:623`, «pendiente de tu validación», `alert=True`, con botones).
(La cadena «listo para revisar» no existe en el código: se toma por estos dos avisos.)

## Diseño
1. **Módulo nuevo `agent_lanes/avisos_revision.py`** (nombre no estándar). Estado en `.state/avisos_revision.json`:
   `{silencio: {chat: epoch | "siempre"}, mudas: [task_id], pendientes: [{tid, chat, titulo, resumen, desde}]}`.
2. **Punto de corte único**: dentro de `ReviewRunner.notify` cuando `state == "done" and alert` (cubre review y renotify) y en el aviso ops de `runner.py:623`.
3. **Resumen agrupado**: los avisos se acumulan y salen en UN mensaje cuando hay 3 tarjetas **o** la más antigua cumple 2 h, lo que pase antes. Una línea por tarjeta: `t_xxx · título · resumen corto` (`for_oscar`, si no el resumen del worker). Lo emite un ticker como `ParadasTicker`; se edita en sitio (patrón `panel_vivo`) mientras llegan más.
4. **Ficha por tarjeta (recomendado)**: se sigue creando y editando, pero con `alert=False` (sin sonido), así ✅ Aprobar sigue disponible; el único mensaje que notifica es el resumen. Alternativa: solo resumen con botones (se pierde la ficha con Aprobar; queda `/aprobar` y `/tarea`).
5. **Botones** del resumen y de los avisos sueltos: `[🔕 1 h] [🔕 3 h] [🔕 Hasta mañana]` y, por tarjeta, `[🙈 No avisar de t_xxx]`. Acciones con prefijo propio, despachadas desde `DecisionDesk._act`. «Hasta mañana» = las `avisos.hora_parte` (9:00 Madrid) del día siguiente.
6. **Durante un silencio** los avisos quedan en `pendientes` y salen como un único resumen al terminar (nada se pierde); `/tareas` y `/aprobar` siguen mostrándolo todo. La tarjeta muda no vuelve a avisar nunca, ni por renotify.
7. **Comando** `/avisos off [1h|3h|mañana|siempre]`, `/avisos on` y `/avisos` (estado): mismo estado que los botones. Distinto de `/silenciar` (alertas del monitor).
8. **`/ayuda`**: añadir `("avisos", …)` a `COMMANDS` (alimenta `help_text()` y el menú de Telegram). Auditoría hecha: los 34 `cmd_*` de `commands.py` ya están en `COMMANDS`; se añade un test que lo vigile.
9. **Configurable desde `/ajustes`** (grupo Avisos): `avisos.revision_lote` (3) y `avisos.revision_ventana_horas` (2).

## Pruebas (dobles, sin red)
- 3 tarjetas terminan en <2 h → se envía 1 mensaje, no 3.
- Silencio de 1 h → no sale nada hasta que el reloj falso pasa la hora; entonces 1 resumen.
- «No avisar de t_x» → esa tarjeta no vuelve a notificar (tampoco por renotify); las demás sí.
- `/avisos off|on` y botones comparten estado; `COMMANDS` cubre todos los `cmd_*`.

## Fuera de alcance
Hooks/contrato, `lanes.yaml` (salvo ajustes por `/ajustes`), cualquier `.bat/.ps1`.
