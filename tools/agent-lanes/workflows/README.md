# Workflows de carril · plantilla `implementar-con-revision`

F0 del pitch `pitches/workflows-desde-carriles.md` (tarjeta `t_3cd20f03`). Un **workflow** entrega una feature grande en
una sola pasada: **Spec → Implementar → Revisar (3 lentes en paralelo) → Corregir + push + PR**, siempre en la rama
`lane/<id>`, sin tocar la base ni mergear. Nació del piloto de Funnel Studio (copia de referencia en
`specs/piloto-funnel-workflow.js.txt`).

> El guion se guarda como `implementar-con-revision.js.txt` porque el integrador bloquea ficheros `.js` en los PR. Para
> ejecutarlo se pasa su contenido al motor de workflows de Claude Code junto con `args`. **Estado F0: solo plantilla.** El
> runner todavía no lo usa (F1, tras un flag apagado por defecto).

## Parámetros (`args`)

| Clave | Obligatorio | Qué es |
|---|---|---|
| `repo` | sí | Ruta del repo (checkout raíz; solo para crear el worktree si no existe). |
| `remoto` | sí | Remoto `origin` (p. ej. `github.com/<org>/<repo>`). |
| `base` | sí | Rama base del PR. La rama de trabajo nunca puede ser la base ni `main`/`master`. |
| `worktree` | sí | Ruta del worktree dedicado. |
| `rama` | sí | `lane/<id>`. |
| `encargo` | sí | Qué hay que construir (texto de la tarjeta). |
| `autorizacion` | sí | **Fuente de la autorización**: tarjeta `t_xxxx` y/o decisión de Oscar con fecha y texto literal. |
| `test_cmd` | sí | Comando de tests (el único que ejecutan los agentes). |
| `atribucion_commit` | sí | Líneas finales de cada commit (`Co-Authored-By: …`). |
| `ficheros_permitidos` | sí (lista, puede ir vacía) | Ficheros/carpetas exclusivos. |
| `ficheros_vetados` | sí (lista, puede ir vacía) | Ficheros/carpetas que no se tocan. |
| `decisiones_oscar` | no | Respuestas literales de Oscar. |
| `spec_ref` | no | Documento/spec de referencia. |
| `reglas` | no | Reglas del proyecto (pilares adicionales). |
| `modelo` | no | Modelo de los agentes (si falta, el del motor). |
| `lentes` | no | Lista `{clave, agentType?, enfoque}`; por defecto corrección / seguridad / configurable-agnóstico. |
| `dry_run` | no | `true` = no lanza agentes ni toca git: devuelve el plan, los `args` y los prompts (secretos → `***`). |

### Lección del piloto: la autorización va en cada agente
Sin la fuente de la autorización los agentes se niegan a ejecutar. `autorizacion` es obligatoria (el guion y
`agent_lanes/workflow_plantilla.py` rechazan el arranque si falta o no cita tarjeta/fecha) y el guion la pone al principio
del encargo de **cada** agente (`ENCARGO Y AUTORIZACIÓN: …`). El runner la rellenará siempre desde la tarjeta:
`workflow_plantilla.autorizacion_desde_tarjeta(id, cuerpo)`.

## Salida
`{spec, impl, hallazgos, final}`; `hallazgos` = `{severidad alta|media|baja, fichero, linea, resumen, escenario, arreglo, lente}`.
Con `dry_run`: `{dry_run, args, fases, lentes, prompts}`.

## Probar sin gastar (dry-run)
`py -3.12 -m pytest tools/agent-lanes/tests/test_workflow_plantilla.py -q` ejecuta el guion con dobles (sin agentes reales,
sin red) en modo normal y en `dry_run`; los tests de ejecución requieren `node` y se omiten si no está.

## Criterio de uso
Workflow para **entregas grandes o varias tareas del mismo proyecto que comparten código**; lo pequeño (Fast-Track, un
bugfix) sigue por carriles normales: un workflow gasta varias veces un worker simple. El umbral será configurable en F1/F2
(estimación OASP mínima y/o nº de ficheros).

## Seguridad
Los hooks de guard del worker no se tocan y siguen aplicando a cada agente. Nada de secretos en args ni en logs.
