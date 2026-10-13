# Spec-Lite · Preferencias recurrentes aprendidas de /sesion

Tarea t_8459486f · módulo `tools/agent-lanes` · estado: **prototipo en rama, pendiente de OK de Oscar** (2 decisiones abajo).

## Problema
Oscar repite el mismo feedback de formato en cada sesión de correcciones (botones con texto, recomendación siempre
visible). Hoy cada sesión se cierra en una tarjeta `[CORRECCIONES]` y el aprendizaje se pierde: los workers y `/preparar`
no lo saben.

## Qué hay hoy (leído del código)
- `/sesion fin` → `sesion.suggest` (claude -p sonnet aislado) → tarjeta; la sesión pasa a `.state/sesiones/<id>.cerrada.json`
  con sus puntos, así que **el histórico ya existe** y se puede reprocesar.
- Los workers reciben `worker.build_prompt` (tarea + decisiones + notas); `/preparar` recibe `card_prepare.build_prompt`.
  Ninguno lleva preferencias generales de Oscar.

## Diseño (prototipo `agent_lanes/preferencias.py`)
1. **Extraer**: al cerrar una sesión, una llamada aislada (mismo patrón que `sesion.suggest`: sin herramientas, sin MCP,
   ≤0,5 $) saca reglas GENERALES de *cómo* quiere las cosas (formato, orden, tono, detalle), no peticiones de un producto.
   Recibe las claves ya conocidas para reutilizarlas y así contar la misma regla entre sesiones. Salida en líneas
   `PREF: clave | workers|preparar|ambos | regla`; lo demás se descarta.
2. **Contar**: `Preferencias.record` guarda en `.state/preferencias.json` la regla como *candidata* con la lista de sesiones
   DISTINTAS donde salió (idempotente por sesión).
3. **Proponer**: una candidata con ≥ `min_sesiones` (2) sesiones es *propuesta*. `render_md()` genera el fichero revisable
   (pendientes / aprobadas / rechazadas con nº de sesiones).
4. **Aprobar**: solo Oscar decide (`decide(clave, aprobar)`); lo rechazado no revive. **Nada se inyecta sin aprobar.**
5. **Inyectar**: `section(destino)` añade al final del prompt un bloque «Preferencias de Oscar» con las aprobadas del
   destino (`workers` → `worker.build_prompt`, `preparar` → `card_prepare.build_prompt`), tope `max_inyectadas`. Sin
   aprobadas = prompt idéntico al de hoy. Un fichero corrupto se ignora (no bloquea a los workers).
6. **Seguridad**: los textos se reducen a una línea de texto plano sin delimitadores `<<<`/`>>>` ni cabeceras; los puntos
   de la sesión van como DATO no confiable; los textos no se escriben en el log. El bloque no puede aflojar hooks ni
   el contrato (va después de la tarea y es solo guía de estilo; los guards siguen mandando).
7. **Agnóstico y configurable**: reglas de *presentación*, sin vertical ni país; ajustes en `preferencias.yaml`
   (`activo`, `min_sesiones`, `max_inyectadas`; defaults si falta). Es un fichero nuevo; `lanes.yaml` intacto.

## Hecho en este prototipo
`preferencias.py`, `preferencias.yaml`, enganche de 1 línea en `worker.build_prompt` y `card_prepare.build_prompt`
(inactivo mientras no haya preferencias aprobadas) y `tests/test_preferencias.py` (14 casos, dobles, sin red).

## Falta (no incluido a propósito: cambia lo que ve Oscar)
- Llamar a `extract` + `record` al final de `/sesion fin` (segunda llamada a claude, ≤0,5 $) o en un `lanes.py preferencias
  --reprocesar` de una vez sobre las `.cerrada.json` existentes.
- Aviso en Telegram con botones [✅ Aprobar] [🚫 Rechazar] por propuesta y `/preferencias` para listarlas/editarlas.
- Sin medición con datos reales: `.state/` está vetado a los workers; calibrar `min_sesiones` con el reproceso.

## Decisiones para Oscar
1. **¿Dónde se aprueba?** (a) botones en Telegram sobre cada propuesta — recomendada, un toque; (b) editando el `.md`
   a mano; (c) PR sobre un fichero versionado en el repo.
2. **¿Cuándo se extrae?** (a) al cerrar cada sesión — recomendada; (b) solo con un comando manual `/preferencias revisar`.
