# Spec-Lite · /sesion — correcciones agrupadas

**Problema.** Oscar dicta feedback en varios mensajes sueltos; cada uno chocaba con la ayuda del #16 o acababa como tarea aparte.
**Appetite.** Menos de un día. Módulo nuevo `agent_lanes/sesion.py` + `cmd_sesion` en `commands.py` + un hook en `decisions.py`.

## Comportamiento
- `/sesion [nombre]` abre una sesión (una sola a la vez). Un primer token de marca (`migrateam|pildora|nextjobs`) o el tema
  desde donde se escribe fija la marca; sin marca, tablero `default`.
- Con la sesión abierta, el texto libre del dueño **en su DM** se anota como punto numerado en
  `.state/sesiones/<id>.json` y se contesta "📝 Punto N anotado", en vez de la ayuda del #16. Comandos, respuestas a un
  `force_reply` pendiente y mensajes de colaboradores (collab_intake) se procesan antes y no se tocan.
- `/sesion fin [tablero]` crea UNA tarjeta `[CORRECCIONES] <nombre>` (asignada a `oscar`, clave idempotente
  `sesion-<id>`) en el tablero del carril de la marca (o el indicado, validado contra los tableros conocidos).
  Cuerpo: puntos numerados + «Preguntas de clarificación» + «Mejoras propuestas según estándar de mercado», generadas por
  `claude -p --model sonnet` (sin herramientas ni MCP, ≤0,5 $, puntos como dato no confiable; patrón de card_prepare.py).
  Si Claude falla, la tarjeta se crea igualmente con los puntos y esas secciones marcadas como no generadas.
- `/sesion cancelar` descarta; `/sesion estado` cuenta los puntos.
- Caducidad: 12 h. Se detecta al siguiente mensaje o comando del dueño (no hay temporizador): el fichero pasa a
  `<id>.caducada.json` (no se borra) y se avisa. Tope 100 puntos × 2000 caracteres.

## Fuera de alcance / decisiones
- No se ejecuta nada: la tarjeta es para Oscar/coordinador, no encola un worker. Sin cambios en `lanes.yaml`,
  `contract/` ni `runner.py`.
- Los puntos nunca van al log (el log de updates sigue sin texto).

## Pruebas
`tests/test_sesion.py`: ciclo completo, no rompe ayuda/force_reply/collab, caducidad, tablero, fallo de Claude, parser.
