# Spec-Lite — Entrada inteligente (triaje antes de crear tarjeta) · t_8ba969b9

Decisión de Oscar (22-10): «primero resolver o preguntar, luego crear tarea»; recomendar workflow en tareas largas.
Tamaño: feature de varios días (triaje LLM + máquina de preguntas + duplicados + /workflow) → Spec-Lite, no Fast-Track.

## Estado verificado
- `workflows.py` (preguntas → plan → aprobar → tarjetas `req_<n>`) y `workflows.yaml` existen; solo `cola_integrador.py` los usa. No hay `/workflow`.
- `/flujo` (`flujo.py`, `commands.cmd_flujo`) ya llama a `claude -p --model sonnet --tools ""` con tope de coste: es el patrón a copiar para el triaje.
- Puntos que hoy crean tarjeta con texto libre: DM de Oscar/operadores y `/idea` (`commands.py`), `/flujo`, `collab_intake.py`.
- t_6f6f86b2 (NO_CARRIL → integrador) y t_b28e05bd (choques) no están en `main`: el triaje debe enchufarse a ellos cuando estén fusionados.

## Diseño
1. `agent_lanes/triaje.py`: `clasificar(texto) -> CONSULTA | TRABAJO_PEQUEÑO | TRABAJO_GRANDE | NO_CARRIL` (modelo inyectable `classifier=`, como `flow_planner`). Fallo o salida inválida → TRABAJO_PEQUEÑO (comportamiento actual; nunca se pierde una petición).
2. `triaje.yaml` (datos, editable desde la UI de ajustes): modelo, `max_budget_usd`, nº de preguntas (2-4), textos de prompt y botones, umbral de duplicado, ventana de «recientes» (días), `activo`.
3. CONSULTA: `consulta.py` reúne datos reales (integrador_estado.md, kanban, `gh pr list`, estado del runner) y responde con un segundo `claude -p` SIN herramientas sobre ese contexto; sin dato → «no lo sé». No crea tarjeta.
4. TRABAJO_PEQUEÑO: 2-4 preguntas con botones (recomendada primero; alcance, marca/repo, prioridad, quién, criterio de hecho) + «Créala ya»; las respuestas van al cuerpo de la tarjeta. Estado en `.state/triaje.json`.
5. TRABAJO_GRANDE: botón «Hacerlo como workflow» → `workflows.py` con plan y coste estimado; nada se crea hasta «Aprobar plan y gasto». Comando `/workflow <texto>` directo.
6. NO_CARRIL: se deriva a la cola del integrador (t_6f6f86b2), sin tarjeta de carril.
7. Antes de crear: choques (t_b28e05bd) y duplicados contra abiertas/hechas recientes; botón «Añadir a la existente» (comentario en la tarjeta).
8. `/ayuda` + `docs/telegram-intake.md` con el flujo.

## Tests (dobles, sin red)
Clasificación con modelo simulado · consulta sin tarjeta · preguntas → tarjeta con respuestas · «Créala ya» · workflow no crea nada sin aprobar · NO_CARRIL al integrador · duplicado → «añadir a la existente».

## Riesgos
- Añade una llamada de modelo (~0,05-0,10 $) a cada texto libre: tope en yaml y apagable (`triaje.activo: false`).
- Toca el camino de entrada de todos los operadores: desplegar tras t_6f6f86b2/t_b28e05bd y con el flag apagado primero.
- Hermes/SOUL.md lo hace el integrador, fuera de este carril.
