# Spec-Lite · Bandeja de decisiones más corta (<16/día) + tarjetas v3

Tarea t_169d4fd5 · módulo `tools/agent-lanes` · estado: **pendiente de OK de Oscar** (2 preguntas abajo).

## Qué hay hoy (leído del código)
- `/decisiones` (`commands.cmd_decisiones`) ya muestra `TOP_DECISIONS` (5) y "N más · ver todas".
- Los recordatorios de 13:00/18:00 (`reminders.py`) solo mandan UN mensaje con contadores + 3 enlaces; no listan
  tarjetas. Lo que crece es el **conjunto** (`oscar_cards` + preguntas + atascadas), no el texto del recordatorio.
- Aplazar (`CardSnoozes`, `.state/card_snooze.json`) solo se hace a mano, 7 d fijos (`SNOOZE_DAYS`).
- Botones de tarjeta: solo letras A/B/C (`card_keyboard_spec`); la respuesta libre es `✍️ Responder` (`CARD_ASK`).

## Medición con datos reales — NO hecha
`.state/` queda vetado a los workers (regla del carril) y vive en el checkout de `main`, no en este worktree.
Propuesta: añadir `python lanes.py bandeja --medir` (solo lectura, imprime hoy / tras agrupar / tras auto-aplazar)
y que Oscar (o el coordinador) lo ejecute una vez; los umbrales se calibran con ese número, no a ojo.

## Diseño propuesto (todo configurable, nada fijo a un vertical)
1. **Agrupar** por origen/épica (marca del tema + prefijo `[DECISIÓN|SEMANA|IDEA]` + `parent`/épica de la tarjeta).
2. **Resumen**: top `bandeja.top` (5) por importancia (bloquea agentes > antigüedad > prioridad) + "N más · ver todas".
3. **Desduplicar**: misma clave (título normalizado + origen) → una tarjeta; las repetidas se enlazan en la principal.
4. **Auto-aplazar** prioridad baja tras `bandeja.autosnooze_dias` con aviso único ("⏳ aplazada sola: baja prioridad").
5. **No repetir aplazadas**: ya ocultas en `/decisiones` y recordatorios; el auto-aplazamiento reutiliza `CardSnoozes`.
6. **Tarjetas v3** (nota del coordinador): botón con texto corto de la opción (`A · Aprobar PR`), respuesta libre
   "A, pero con X" (se anota tal cual como RESPUESTA-OSCAR), `✅ Aceptar todas las recomendadas` en el lote y
   **plazo de veto** (`bandeja.veto_horas`, 48 h) para decisiones reversibles marcadas `reversible: true`: se ejecutan
   solas con la recomendada pasado el plazo, con aviso previo y botón ⛔ Vetar. Solo las marcadas; nunca irreversibles.

## Ajustes: bloqueo
Pedido: "usar el archivo de ajustes ya existente del paquete, sin tocar `lanes.yaml`". El único fichero de ajustes del
paquete es `lanes.yaml` (vetado para el carril); `tools/telegram/people.yaml` es de personas/roles, no de umbrales.
No hay un archivo adecuado → `needs_input`.

## Tests previstos (`tests/test_bandeja_corta.py`, dobles, sin red)
agrupación y desduplicado; top-N + "N más"; auto-aplazar solo baja prioridad y una vez; aplazada no reaparece;
umbrales desde el fichero de ajustes con defaults si falta; botones con texto corto; "A, pero con X"; aceptar lote;
veto: no ejecuta antes del plazo, ejecuta tras él, ⛔ lo cancela, nunca sin `reversible`.

## Riesgos
Auto-ejecutar con veto cambia lo que hace el bot sin pulsación de Oscar: exige su OK explícito y que el flag
`reversible` lo ponga quien crea la tarjeta, no el worker.
