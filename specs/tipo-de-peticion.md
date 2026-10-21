# Spec-Lite · Tipo de petición: bug o feature, preguntas por tipo y modo OASP (t_a1a2513a, entrega B del pitch)

Fuente: `pitches/seguimiento-peticiones.md` §4.5, §5 (B1, B2), §6, §7. Modo OASP: Spec-Lite. No toca runner, hooks, contrato, `.env`, `.state/` ni workflows.

## Contrato (B1 lo fija; A1 lo respeta)
- Campos del registro que B lee/escribe, y SOLO esos: `tipo` (`bug`|`feature`|`workflow`|null), `tipo_confirmado_por`, `urgencia` (P0-P3, solo bug), `modo_oasp` (`fast-track`|`spec-lite`|`pitch`, solo feature).
- Interfaz mínima hasta que A1 esté mergeado (`tipo_peticion.RegistroPeticiones`): `crear(proyecto, titulo, origen) -> "req_<n>"` y `actualizar(req_id, **campos)` con los cuatro campos. Sin registro (None) el flujo funciona igual y solo escribe en la tarjeta. No hay otro registro.
- Cuerpo de la tarjeta: `req: req_<n>` (si hay registro), `Tipo: bug · Urgencia: P1` (feature: `Tipo: feature · Modo OASP: spec-lite`) y una sección `## Respuestas` con pregunta y respuesta.
- Todo configurable: `tools/agent-lanes/tipo_peticion.yaml` (urgencias, modos, preguntas, pistas de detección, textos de los botones). Sin el fichero valen los valores por defecto del módulo.

## Flujo (`/pedir <texto>`, botones en el hilo donde se pide)
1. `[🐞 Arreglar algo que falla] [✨ Crear algo nuevo]`. Si el texto lo delata, el bot PROPONE («parece un fallo, ¿lo confirmas?») con los mismos botones; sin pista, pregunta. El tipo no se fija hasta pulsar; `tipo_confirmado_por` = quien pulsa.
2. Preguntas del tipo (máx. 4 bug / 3 feature), de una en una, formato telegram-decisiones-v2: opciones A/B, ⭐ Recomendada, ✍️ Otra (force_reply), «Acepto todas las recomendadas» solo si todas las pendientes la tienen. Las ya respondidas en el texto original no se repiten.
   - bug: cómo reproducirlo, dónde, qué esperaba, urgencia (P0 prod roto … P3). Prioriza: la urgencia sube la prioridad de la tarjeta.
   - feature: objetivo, quién lo usa, criterio de «hecho». Luego el bot propone Fast-Track / Spec-Lite / Pitch (según la respuesta de alcance y la longitud) y se confirma con botón.
3. Se crea la tarjeta (carril por palabras clave como /hazlo; si no hay, tarjeta [IDEA] para oscar) con tipo y respuestas, y se escribe en el registro.

## Tests (`tests/test_tipo_peticion.py`, dobles, sin red)
- Detección propone y NO fija; sin pulsar no hay tarjeta ni registro. Pulsar fija tipo y confirmador.
- Preguntas por tipo y tope; las respondidas en el texto se saltan; «Acepto todas» solo con recomendadas.
- Urgencia → prioridad; feature → propuesta de modo + confirmación; cuerpo de la tarjeta; registro con dobles (solo 4 campos).
- Callback de otro botón/cancelar; `/pedir` sin texto; config del YAML sobreescribe valores.
