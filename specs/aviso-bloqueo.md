# Spec · Aviso de bloqueo con motivo real y diagnóstico automático (t_1d4ad791, origen t_3959fea9)

Modo OASP: **supera Spec-Lite** (toca runner, avisos, botones, decisiones y config; varios días). Esta spec propone el corte en 3 entregas y NO implementa nada. No toca hooks, contrato, `.env` ni `.state/`.

## Límite de esta spec
No he podido leer la entrega A (t_211c9902, formato único de avisos) ni t_61a4baa6: no están en el repo y el kanban real me está vedado. Lo que sigue sale del código de `main` (d377295). **Antes de implementar E1, quien la tome debe leerlas y borrar de aquí lo que ya cubran.**

## Qué existe ya (no se duplica)
- `block_kind` en `renotify.py`/`runner.py`: `transient` → [Reintentar][Aparcar]; `config` y `console` → [Aparcar][Que lo resuelva el integrador] (`decisions.keyboard_spec`); `stuck` (triage) con historial (`stuck_history`).
- `guard_route.py`: bloqueos de guard a la cola del integrador, escalada a Oscar a las 2 h, contador «repetido».
- `runner._block`: 2.º bloqueo transitorio igual → pregunta a Oscar (no relanza).
- `CONFIG_REASONS` (runner.py:83): único caso de «verificación mecánica» tratado como estructural.

## Qué falta (el hueco real)
1. Todo `transient` ofrece Reintentar, aunque sea estructural: «push que nunca llegó», «conflicto de merge», «migración vetada» caen en «verificación mecánica fallida» o «error del runner» con frase fija (`PUBLIC_REASONS`), sin el motivo real.
2. No hay clasificador transitorio/estructural: solo prefijos de texto hardcodeados.
3. No hay tabla «qué carril puede tocar qué» ni reasignación automática.
4. No hay botón «Más detalle» (existe 💬 Explícame más, solo en needs_input).

## Diseño
**Clasificador** (`agent_lanes/bloqueos.py`, función pura `clasificar(reason, lane_cfg) -> Diagnostico`): clase `transitorio | estructural-permiso | estructural-otro | decision`, `resumen` (1-2 líneas con el motivo real extraído), `que_falta`, `accion` (`reintentar | reasignar:<carril> | integrador | oscar`). Primer patrón que casa gana; sin patrón → `desconocido` = comportamiento actual (Reintentar + Aparcar).

**Configuración** `tools/agent-lanes/bloqueos.yaml` (como `tipo_peticion.yaml`; sin fichero valen los defaults del módulo):
- `patrones:` lista `{clase, regex, resumen, que_falta}`. Defaults: transitorio = `cannot lock ref`, `timed out`, `rate limit`, `max_runtime`; estructural = `toca rutas vetadas`, `Destino-Ops no permitido`, `non-fast-forward|no llegó al remoto|rama no empujada`, `CONFLICT|conflicto de merge`, `alembic|migración`.
- `capacidades:` por carril, derivada de `lanes.yaml` (`forbidden_paths`, `dest_roots`, `kind`) con sobrescritura manual; se usa para decir «este carril no puede tocar X; pueden: …».
- `textos:` plantillas del aviso por clase; `botones:` qué botones salen por clase; `umbrales:` reintentos automáticos máx. (def. 2), espera entre ellos, veces antes de escalar.

**Aviso** (formato A1 si ya existe): `⛔ t_x · título` / `Motivo: <resumen real>` / `Intentado: …` / `Falta: …` / `➡️ Próximo paso: … · Lo da: …`.
Botones por clase: transitorio = [🔄 Reintentar][🗄 Aparcar][🔎 Más detalle]; estructural = [🧑‍💻 Integrador][🗄 Aparcar][🔎 Más detalle] (**sin Reintentar**); permiso con carril alternativo = aviso informativo «reasignada a X» con [↩ Deshacer]. «Más detalle» (acción nueva `DETAIL`) responde en el chat con el motivo técnico recortado y los últimos comentarios; no consume el teclado.

## Entregas
| | Contenido | Riesgo |
|---|---|---|
| **E1** (Spec-Lite) | `bloqueos.py` + `bloqueos.yaml` + motivo real en el aviso + botones por clase + `DETAIL`. Sin reasignar ni reintentar solo. Tests con dobles. | Bajo: texto y botones |
| **E2** | Tabla de capacidades + reasignación automática cuando SOLO un carril puede (reversible, 1 vez por tarjeta, comentario en la tarjeta, aviso con Deshacer). | Medio: toca `hermes.assign` desde la ruta de bloqueo |
| **E3** | Reintento automático de transitorios con tope y espera; delegar al integrador SOLO si estructural sin reasignación; unificar con `GuardRouter` (misma cola). Cobertura de todos los tableros. | Medio-alto: toca `runner._block` |

## Tests (`tests/test_bloqueos.py`, sin red)
Clasificación de cada patrón por defecto; yaml sobrescribe patrones y textos; sin yaml = defaults; estructural nunca ofrece Reintentar; `DETAIL` no consume teclado; un patrón mal formado se ignora con log (no rompe el aviso).

## Decisiones de Oscar (21-oct) y estado
- Alcance: **E1 sola** (aviso, clasificador, botones). Implementada en esta rama: `agent_lanes/bloqueos.py`, `bloqueos.yaml`, botón `DETAIL`, `tests/test_bloqueos.py`.
- Editable: yaml en el repo + `/aplicar` (sin comando nuevo).
- E2 (reasignación automática): sin pedir permiso, con aviso y [Deshacer]. Pendiente de otra tarjeta.
- Fuera de E1: el carril review (`review.py`) sigue con las frases fijas; E3 lo unifica.
