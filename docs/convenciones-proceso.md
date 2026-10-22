# Convenciones de proceso (causas raíz recurrentes)

Tarea t_5d8af7c1 · 2026-10-22 · revisión de los 5 puntos del diagnóstico de Oscar. Son convenciones de proceso, no
cambios de producto. Cada punto dice qué se decide: **convención** (este documento) o **tarea** (para el kanban).

## 1. Sacar un chat_id de Telegram — convención

Un solo método, no improvisar:

1. **Persona**: que escriba al bot (o reenvíale un mensaje suyo al bot). El `id` que aparece es el de **usuario**
   (positivo). Alternativa: añadir @userinfobot / @RawDataBot un momento a la conversación y quitarlo después.
2. **Grupo / supergrupo**: añadir @RawDataBot temporalmente al grupo, o reenviar al bot un mensaje del grupo. El id del
   grupo es **negativo** y los supergrupos llevan prefijo `100` (ej. `-1003731090094`).
3. **Regla de lectura**: positivo = persona; negativo = grupo. Si un id de grupo no empieza por `-100`, desconfía.
4. Para comprobar de quién es el bot: `getMe`.
5. Quitar el bot auxiliar al terminar y registrar el id en `tools/telegram/people.yaml`, nunca en chats.

## 2. Alcance de acceso de una persona nueva — convención

Antes de escribir nada en `people.yaml` que dé poder real (operador, aprobador, acceso completo) hay que haber hecho
**una pregunta de clarify aislada y explícita**, sola en su mensaje, del estilo:

> ¿X tendrá EXACTAMENTE el mismo acceso que María, sí o no? (si no: ¿qué marcas y qué acciones?)

Nunca se infiere el alcance de un comentario general ni de una conversación con varios temas. La respuesta literal de
Oscar se copia en el commit o la ficha. Mientras no haya respuesta, la persona queda con el mínimo (sin `brands`).

## 3. Un control de acceso necesita prueba, no solo un campo — convención

Un campo de config no es un control hasta que el código lo aplica. Para dar por bueno cualquier diseño o auditoría de
acceso se exige una **prueba concreta documentada**: "usuario X intenta una acción fuera de su alcance → el backend la
rechaza con tal error". En el hub esa prueba debe ser un test en `tools/agent-lanes/tests/`.

Estado actual: el caso de `brands` ya está cubierto (`tests/test_operador_marca.py`: botón y comando de otra marca se
rechazan, Mauredy no ejecuta integrador, ajustes solo para quien opera en todas las marcas). El patrón general
("existe el campo ≠ protege") queda como criterio de revisión: el carril `review` debe pedir esa prueba en cualquier PR
que toque `people.yaml`, permisos o botones de Telegram.

## 4. Navegador / Chrome 9222 — ya decidido; se añade la vía (b)

- (a) **Decidido el 17-oct** (`docs/diagnostico-browser-exec-chrome-9222.md`): Chrome dedicado con acceso directo en
  Inicio. El aviso del monitor si cae el 9222 sigue **pendiente como tarea aparte**.
- (b) **Convención nueva**: para un documento puntual ya identificado (Google Doc, etc.), no depurar el navegador:
  pedir el contenido exportado o pegado. Si el worker detecta que el navegador no responde y el documento es puntual,
  devuelve `needs_input` pidiendo el texto en vez de reintentar.

## 5. Conflictos entre PRs por la espera de aprobación — ya implementado

El lote diario existe (`docs/lote-diario-integrador.md`) y **ya está activo en `claude-migrateam` y `claude-oscarhq`**,
así que "¿aplicarlo a oscar-hq?" está resuelto: sí, ya. No hay trabajo nuevo. `claude-hub` queda fuera (PR a PR) a
propósito: sus cambios gobiernan al propio sistema y Oscar los revisa uno a uno.

## Resumen de decisiones

| # | Decisión | Acción |
|---|----------|--------|
| 1 | Convención | Este documento |
| 2 | Convención | Este documento (clarify aislado antes de tocar `people.yaml`) |
| 3 | Convención + revisión | Ya hay tests de `brands`; el revisor exige prueba en PRs de permisos |
| 4 | Convención + tarea pendiente | Vía (b) aquí; tarea: aviso del monitor si cae el 9222 (acordada el 17-oct) |
| 5 | Ya hecho | Lote diario activo en migrateam y oscarhq |
