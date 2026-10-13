# Pitch corto · /flujo — una petición suelta, una cadena de tarjetas

**Problema.** Una petición de Oscar suele ser varias piezas encadenadas (código → tests → doc → despliegue). Hoy las trocea
a mano en tarjetas sueltas, con las dependencias en la cabeza.
**Appetite.** Menos de un día. Módulo `agent_lanes/flujo.py` + `cmd_flujo`/`flujo_choice` en `commands.py` + acción `FLUJO`
en `decisions.py` + `parents=` en `HermesKanban.create`.

## Comportamiento
- `/flujo <texto>`: `claude -p --model sonnet` (patrón de `card_prepare.py`: sin herramientas ni MCP, ≤0,5 $, texto como DATO
  no confiable) devuelve un array JSON de hasta 8 tarjetas: título, carril, padres, cuerpo, límites, `confirmar`.
- El bot muestra la propuesta numerada con `[✅ Crear todo] [🚫 Cancelar]`. **No se crea nada hasta pulsar.**
- Al pulsar se crean en orden topológico con la CLI de Hermes: `--parent` (dependencias), `--idempotency-key flujo-<id>-<n>`
  y `--created-by oscar`. Reintentar tras un fallo parcial no duplica; si falla una tarjeta, sus descendientes no se crean.
- Tablero según el carril (lanes.yaml, solo lectura): claude-hub→default, claude-oscarhq→oscarhq, claude-migrateam→migrateam.
  Un padre de otro tablero no se puede enlazar: se anota «Depende de» en el cuerpo.

## Reglas de seguridad
- Carril fuera de `lanes.yaml` (solo carriles `implement`), padre que no sea una tarjeta anterior, >8 tarjetas o JSON roto
  ⇒ el flujo entero se rechaza (un hueco rompería las dependencias).
- Merge/deploy/producción/envío externo (lo marca el modelo **o** lo delata una lista de palabras): la tarjeta se crea
  **a nombre de Oscar** con `[CONFIRMAR]` en el título; ningún worker la recoge sin su visto bueno.
- Texto del modelo: solo entra por el parser (recortado); el botón es solo para Oscar como el resto.

## Riesgos
- El modelo puede trocear mal: por eso hay propuesta previa y Cancelar. La lista de palabras de riesgo puede dar falsos
  positivos (más tarjetas a nombre de Oscar) — se prefiere eso a un falso negativo.
- Depende de que `hermes kanban create` acepte `--parent` repetido (indicado en la tarea; no verificable sin hermes real).

## Fuera de alcance
Sin cambios en `lanes.yaml`, `contract/` ni `runner.py`. Sin ajustes configurables aún (tope de 8 y modelo son constantes).

## Pruebas
`tests/test_flujo.py`: parser (límites, carriles, ciclos), seguridad, ciclo completo con Hermes simulado, tableros, fallo parcial.
