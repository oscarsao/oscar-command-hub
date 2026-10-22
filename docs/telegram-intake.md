# Entrada inteligente del bot de carriles (para el equipo)

Regla de Oscar (22-10): **primero resolver o preguntar, luego crear tarea.** Cuando escribes en texto libre al bot de
carriles (DM de un operador), el bot lo clasifica antes de tocar el kanban. Spec: `tools/agent-lanes/specs/entrada-inteligente.md`.

| Qué detecta | Qué hace | ¿Crea tarjeta? |
|---|---|---|
| **Consulta** («¿qué PR hay abiertos?») | Responde con el estado real (`/falta` + `/estado`). Si no tiene el dato, lo dice. | No |
| **Trabajo pequeño** | Primero mira si ya hay una tarjeta parecida (abierta o hecha hace poco) y ofrece **➕ Añadir a la existente**. Si no, hace 2-4 preguntas con botones (⭐ = la recomendada). | Sí, al terminar las preguntas |
| **Trabajo grande** | Propone **🧩 Hacerlo como workflow**: plan, coste estimado y tope. | Solo al pulsar **✅ Aprobar plan y gasto** |
| **Producción / variables / SQL / otro repo** | No es de un carril: **📨 Pasar al integrador** (tarjeta a nombre de Oscar, `[INTEGRADOR]`). | Solo al pulsar el botón |

- **⚡ Créala ya** en cualquier pregunta: crea la tarjeta con lo contestado hasta ese momento.
- **🚫 Cancelar**: no se crea nada.
- Las respuestas quedan en el cuerpo de la tarjeta (`## Respuestas de Oscar`).
- `/workflow <texto>` va directo al plan de un trabajo largo (sin pasar por la clasificación).
- Si el clasificador falla o no está claro, se trata como trabajo pequeño: nunca se pierde una petición.

## Ajustes (sin tocar código)

Todo está en `tools/agent-lanes/triaje.yaml`: `activo` (false = el bot solo responde, como antes), modelo, tope de coste de
cada clasificación (`max_budget_usd`), número de preguntas, umbral de duplicado, ventana de «recientes», prioridades por
respuesta, textos de botones y las propias preguntas. El tope de gasto de los workflows está en `workflows.yaml`.

## Todavía no incluido

- Choques entre tarjetas (t_b28e05bd) y la cola real del integrador (t_6f6f86b2): hoy NO_CARRIL deja una tarjeta a nombre
  de Oscar; cuando se fusionen, se enchufan en `triaje_route` / `_triaje_small`.
- Las tarjetas de un workflow que dependen de otras se crean cuando las anteriores terminen: ese avance automático
  (`workflows.card_done` + `launch`) aún no está conectado al runner.
- Hermes (SOUL.md) lo hace el integrador, fuera de este carril.
