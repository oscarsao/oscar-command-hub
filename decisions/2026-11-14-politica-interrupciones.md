---
estado: propuesta
dueño: Oscar
fecha: 2026-11-14
opción-por-defecto: aplicar solo el paso 1 (hook por destino) tras la verificación en vivo; el resto sigue pendiente
---
# Que el sistema dependa menos de Oscar: interrupciones, permisos y puertas automáticas

Origen: revisión del 14-11 de 5 sesiones interactivas (Integradora 27 días, 2.036 mensajes) y de la v3 de la fábrica
(`2026-10-30-fabrica-v3-borrador.md`). Cifras sacadas de las transcripciones con scripts; las categorías son por palabras clave (orientativas).

## Diagnóstico
1. **El cuello no es la latencia, es la presencia.** 868 preguntas con botones en 27 días (≈32/día) se contestan con mediana 1,2 min
   (87 % en <10 min, ninguna >24 h). El sistema está pensado para conversar en vivo; si Oscar se desconecta, para.
2. **Aceptas la recomendada el 74 %** de las 1.353 preguntas que la traían (14 % texto libre). Por categoría: producción 86 %,
   borrado 85 %, fusionar PR 83 %, envíos 78 %, dinero y secretos 74 %. Donde el criterio de Oscar cambió el resultado: 353 casos (26 %).
3. **Reglas escritas que nada aplica.** `settings.json` tenía en `allow` `gh pr merge *`, `railway up *` y `apply_migration` (Railway ya no existe desde el 14-11 y esas reglas se retiraron); el hook de push
   solo miraba el texto, así que `git push` a secas en master no preguntaba (verificado). Mismo patrón que el 28-ago.
4. **Mismo problema en el front.** oscar-hq tiene sistema de diseño (49 componentes, tokens) y regla escrita, pero ningún lint la aplica:
   529 `<button>` y 474 `<input>` crudos, 352 colores hex y 597 `style` inline fuera de `ds/`; conviven `DESIGN.md` (Liquid Glass) y el DS nuevo.
   La web no tiene CLAUDE.md ni DS. Resultado: rediseñar y corregir varias veces.

## Qué reducir y qué no (de la clasificación)
| Grupo | Aceptadas tal cual | Propuesta |
|---|---|---|
| Fusionar PR, «¿creo tarjetas?», producto/diseño | 147 (8 %) | Solo, con rama temporal, gates y reversión (C0/C1 de la v3) |
| Producción, borrado, dinero, secretos, envíos | 472 (26 %) | Se mantiene tu OK, pero por **lote con evidencia**, no 472 preguntas |
| Prioridad, alcance, producto sin categoría | 381 (21 %) | Pitch escrito con opción por defecto, no pregunta en vivo |
| Criterio que cambió la decisión | 353 + 78 sin recomendación | Se mantienen interactivas |
Estimación (no medida): de ≈32 a 18-22 interrupciones al día.

## Plan (orden)
1. **Hook por destino + permisos** — hecho como propuesta en `tools/agent-lanes/hooks-usuario/` (34 pruebas). Lo aplica Oscar.
   Es un **arreglo de seguridad, no de interrupciones**: cada llamada vigilada costará un aviso de permisos hasta que exista la
   aprobación por lote. Para no duplicarlo con los botones, se empareja con una regla en CLAUDE.md (ver README, paso 3).
   Más adelante, el lote necesita que un solo «sí» preautorice PRs concretos para que el hook devuelva `allow` por número de PR.
2. `policy.yaml` por repo (clases C0-C3 y tabla de qué se pregunta) como fuente única; el hook ya lee `politica-permisos.json`.
3. Recortar CLAUDE.md (MigraTeam 714 + 600 líneas de AGENTS.md, oscar-hq 360, raíz con «Mayo 2026» y Railway) y corregir que el asesor ya es Fable. **Hecho el 14-11 en los dos CLAUDE.md globales (Railway→Dokploy, regla de Alembic, asesor, fecha); falta recortar y los repos de producto.**
4. Roles de sesión interactiva (Coordinador / Integrador / Operaciones), formato fijo de pregunta (recomendación, reversible, defecto, plazo) y vista «Hoy».
5. **Puerta de UI** (lint «ningún fallo nuevo» con lista de excepciones, manifiesto del DS para agentes, un único lenguaje visual) — tarjeta a `claude-oscarhq`.
6. Código de C0 automático a `develop` y escalera D6 — tarjetas a carriles.
7. Medir cada semana: interrupciones/día, % de recomendadas aceptadas, veces que Oscar repite una regla.

## Experimento Fable → Sonnet (tras el paso 5)
3 brazos sobre 4-6 tareas de UI parecidas: A Sonnet directo; B Sonnet + puerta del DS + manifiesto; C pitch de Fable + Sonnet + puerta + manifiesto.
Métricas: rondas de corrección de Oscar, infracciones del DS por tarea, aprobadas a la primera, tiempo y consumo. Resultado orientativo, no estadístico.
Hipótesis a contrastar: la puerta y el manifiesto aportan más que el cambio de modelo.

## No hecho / pendiente de decidir
- No se ha aplicado nada en `~/.claude` (salvo `advisorModel: fable`, pedido expresamente el 14-11).
- ¿`develop` de MigraTeam es rama protegida (`protegidas_por_repo`)?
- No se analizaron las ~110 sesiones de carriles ni se repitió la medición de esperas en las otras sesiones.
