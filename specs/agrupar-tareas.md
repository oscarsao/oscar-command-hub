# Spec · Agrupar tareas: /agrupar, propuesta al crear y aviso en la verificación (t_a0110f3d)

Modo OASP: **supera Spec-Lite** (cuatro piezas, toca runner, creación de tarjetas y Telegram; varios días). Esta spec propone el corte en 4 entregas y NO implementa nada. No toca hooks, contrato, `.env` ni `.state/`.

## Idea de Oscar (23-oct)
«Agrupar debería existir como comando y también como propuesta al desarrollar y como parte de la verificación de tareas.»

## Qué existe ya (se reutiliza, no se duplica)
- `solapes.py`: `extraer_rutas`, `compartido`, `detectar` → `Grupo`, `teclado`, descartes (`par`), `solapes.yaml`. Detecta tarjetas activas con rutas en común y su aviso lleva botones [Son duplicadas][No son duplicadas][Luego].
- `choques.py` (E1): zona real por git (`git diff`, `merge-tree`), clases aditivo/compatible/conflicto/revisar, `propuesta`.
- `deps.py`: padre/hija = «encadenar». `solapes._enlazadas` ya excluye pares encadenados.
- `bandeja.py` / `lo_tuyo.yaml` / `personal.yaml`: qué tarjetas son «de persona» (no de carril).
- t_ee6ad56c (botones de solape) y t_05c99181 (carriles por proyecto): la lógica de repo-por-carril y de botones sale de ahí. **Antes de implementar hay que rebasar sobre ambas** si siguen abiertas; esta spec solo añade capas por encima.

## Diseño
**Módulo nuevo** `agent_lanes/agrupar.py` (funciones puras, dobles inyectables: lector de tableros, `Git`) + `agrupar.yaml` (todo dato: umbrales, palabras clave, estados, `semanal.min_grupos` def. 3, `semanal.dia/hora`; se relee cada pasada; ausente = defaults).

### Núcleo: `proponer(tarjetas, ajustes) -> list[GrupoProp]`
Tipos de grupo, en este orden de precedencia (una tarjeta cae en un solo grupo):
1. **bandeja** (no es de carril): épicas, tarjetas «tuyo»/«decidir»/retenidas (palabras de `agrupar.yaml`, `lo_tuyo.yaml`), assignee persona/`oscar`. Se listan aparte: **nunca** se fusionan ni encadenan.
2. **duplicadas**: título normalizado (minúsculas, sin ids/prefijos `[Hub]`) con similitud ≥ `duplicadas.umbral` (def. 0,8, `difflib`) y mismo repo. Acción: fusionar.
3. **mismo fichero/área**: `solapes.compartido` (o zona git de `choques` si hay rama). Acción: encadenar en orden (la de rama/progreso primero; si no, la más antigua).
4. **misma decisión pendiente**: tarjetas bloqueadas/`needs_input` cuyas preguntas coinciden (misma clave de `decisions.py`). Acción: una sola pregunta.
5. **lote de integración**: mismo repo, sin solape de ficheros, en review/PR listo. Acción: mismo lote.

### 1. Comando `/agrupar [proyecto|todo]`
Registrado como el resto de comandos de Telegram. Lee tarjetas abiertas (todos los tableros o uno), llama a `proponer` y envía UN mensaje numerado; por grupo botones **[🔗 Fusionar] [⛓ Encadenar] [📦 Mismo lote] [✋ Dejar]** (solo los que aplican al tipo). La bandeja se muestra al final, sin botones. **Nada se aplica sin botón.**
- Fusionar: comenta en ambas y deja la secundaria `blocked`/archivada con enlace a la principal (misma semántica que «Son duplicadas» de `solapes`, ampliada: solo tras pulsar).
- Encadenar: `deps` (hija depende de padre).
- Mismo lote: etiqueta/comentario para que `cola_integrador` las junte.
- Dejar: guarda el par en descartes (reutiliza `solapes.par` / fichero de descartes) y no se vuelve a proponer.
- Callbacks con prefijo propio `agr:` y estado en un JSON de `.state` gestionado por el propio módulo (patrón `solapes`), idempotentes.

### 2. Propuesta al crear
Gancho único en la capa que crea tarjetas desde `/hazlo`, `/flujo`, propuestas de Avisos y Hermes (`HermesCLI.create` no sirve: hay creadores que no pasan por él). Antes de crear: `similares(titulo, cuerpo, abiertas)` → si hay candidatas, mensaje con **[➕ Añadir a t_xxxx] [⛓ Crear nueva encadenada] [🆕 Crear nueva igualmente]**. «Añadir» = comentario en la existente, sin tarjeta nueva. Sin candidatas o con fallo de lectura: se crea como hoy (nunca bloquea).

### 3. Verificación
En el runner, justo antes de pasar a review, y en el lote del integrador: `otras_que_tocan(rama, tarjeta)` con la zona real (`git diff --name-only`) frente a las demás tarjetas abiertas (`choques.unidades_repo`). Si hay → aviso a Oscar con la propuesta de agrupación (un grupo, mismos botones). **Solo avisa**: no frena el paso a review. Idempotente por clave de conjunto. Fallo = silencio.

### 4. Resumen semanal
Lunes 9:00 (Gestión), ticker como `SolapeTicker`: `proponer` sobre todo; si hay ≥ `semanal.min_grupos` (def. 3) grupos accionables (sin contar bandeja) → un mensaje resumen con enlace a `/agrupar`. Una vez por semana (clave = ISO week).

## Entregas
| | Contenido | Riesgo |
|---|---|---|
| **A1** | `agrupar.py` + `agrupar.yaml` + `/agrupar` con botones y aplicación | Bajo-medio (cambia tarjetas solo tras botón) |
| **A2** | Propuesta al crear (gancho en creación) | Medio: toca el camino de `/hazlo`/`/flujo` |
| **A3** | Aviso en verificación (runner + lote integrador) | Medio-alto: toca el camino del runner hacia review |
| **A4** | Resumen semanal | Bajo |

## Tests (`tests/test_agrupar.py`, dobles, sin red/Telegram/hermes reales)
Duplicadas por título similar · encadenado por ficheros comunes (orden por rama/antigüedad) · misma decisión pendiente · exclusión de tarjetas de persona/épicas/retenidas (van a bandeja) · botones por tipo de grupo y callbacks `agr:` · «Dejar» no reaparece · nada se aplica sin botón · similares al crear (añadir/encadenar/igualmente) y fallo = crea igual · aviso de verificación idempotente y sin frenar · semanal: <3 no avisa, ≥3 avisa una vez por semana · yaml sobrescribe umbrales.

## Decisiones tomadas (recomendada aplicada, reversibles en yaml)
- Corte A1→A4; A1 sola da valor sin tocar runner.
- Umbral de duplicadas 0,8 y semanal ≥3 grupos (los de la tarjeta), editables en `agrupar.yaml`.
- Sin IA en el camino por defecto: similitud de texto + rutas + git.
- Reutilizar `solapes`/`choques`/`deps`; ninguna lógica de solape nueva.

## Pendiente de Oscar
- OK a este corte y a empezar por A1.
- Confirmar que t_ee6ad56c y t_05c99181 estén fusionadas antes de implementar (si no, A1 se rebasa sobre ellas).
- Dónde enganchar la creación (A2): ¿solo `/hazlo`, `/flujo` y Avisos, o también `hermes kanban create` directo (barrido posterior en vez de gancho)?
