# Spec · Choques entre tareas: aditivo avanza, compatible a cola, conflicto frena (t_b28e05bd)

Modo OASP: **supera Spec-Lite** (detección por git, tres políticas, avisos con mención, base fresca en el ciclo; varios días y toca runner). Esta spec propone el corte en 4 entregas y NO implementa nada. No toca hooks, contrato, `.env` ni `.state/`.

## Decisión de Oscar (22-oct)
Evitar choques y bases viejas SIN parar lo que pueda avanzar:
1. **Aditivo** (ficheros distintos, o mismo fichero con `git merge-tree` limpio) → en paralelo, sin parar.
2. **Compatible, misma zona** → proponer con botones fusionar en una tarjeta o encadenar; sin respuesta, la nueva va a la COLA como dependiente de la otra y arranca al fusionarse, desde la base nueva.
3. **Diferente/incoherente** (intención opuesta o conflicto real de merge) → FRENAR la nueva y avisar con MENCIÓN a los implicados (creador de cada tarjeta / dueño de la rama; Oscar siempre) con PROPUESTA concreta (cuál primero, qué cambiar). Vale también para la misma persona con una tarea hecha o en cola que diga otra cosa.

## Qué existe ya (no se duplica)
- `git_ops.prepare_worktree`: fetch fresco de la base al crear el worktree. `git_ops.sync_base` (merge de la base, devuelve texto de conflicto): solo en reintentos (`runner.py:365`).
- `solapes.py`: `extraer_rutas` (texto→rutas), `compartido`, `detectar`, `SolapeTicker`, aviso idempotente por conjunto, `solapes.yaml`. Solo AVISA y solo si la tarjeta cita rutas.
- `deps.py`: dependencia padre/hija (`pending_parents`, «integrado» por comentario); `solapes._enlazadas` ya excluye pares encadenados. **Es el mecanismo de cola a reutilizar** (alineado con t_8125ad1d).

## Qué falta
No se mira lo que hay en git (ramas `lane/*`, PR abiertos, ramas de personas), no se distingue aditivo de conflicto, no hay freno ni mención, y la rama puede llegar a review sobre base vieja.

## Diseño
**Módulo nuevo** `agent_lanes/choques.py`, funciones puras con dobles inyectables (git runner, lector de tableros, `gh`), + `choques.yaml` (todo dato, ningún umbral en código; se relee cada pasada; ausente/inválido = defaults).

1. **Zona de una unidad de trabajo** (`zona(unidad) -> set[ruta]`), por orden de fiabilidad:
   - rama con commits: `git diff --name-only <base>...<rama>` (real);
   - PR abierto: igual con su `headRefName` (`gh pr list --json`);
   - tarjeta sin código: `extraer_rutas` (lo actual) + opcional `prediccion.activa` (def. `false`): UNA llamada barata (`prediccion.max_usd`, def. 0,05) con título, cuerpo y árbol del repo (`git ls-files` recortado) que devuelve ficheros/módulos probables; cache por hash de tarjeta; fallo = se ignora.
2. **Fuentes de la comparación** (`fuentes:` en yaml): ramas `lane/*` abiertas; PR abiertos; ramas remotas NO `lane/*` con commits en los últimos `ramas_personas.dias` (def. 14) y no fusionadas en la base (`git branch -r --no-merged`), dueño = autor del último commit; tarjetas activas de todos los tableros (incl. «equipo» cuando exista), con `estados` como en `solapes.yaml`.
3. **Clasificación** (`clasificar(nueva, otra) -> aditivo | compatible | conflicto | revisar`):
   - sin ficheros ni módulo en común → `aditivo`;
   - ambas con rama: `git merge-tree --write-tree <ramaA> <ramaB>`; exit 0 → `aditivo` (mismo fichero, cambio limpio); exit 1 → `conflicto` (real);
   - misma zona sin rama de una de ellas (tarjeta) → `compatible` por defecto;
   - **contradicción de intención** (mismo módulo y objetivos opuestos): `choques.yaml` → `contradiccion.patrones` (pares de verbos/expresiones: añadir↔quitar, activar↔desactivar, renombrar↔borrar…) sobre título+cuerpo; si casan → `revisar`. Nunca se resuelve solo; sin IA en el camino por defecto.
4. **Acciones por clase** (`politica:` en yaml, editable):
   - `aditivo` → nada (avanza); solo se anota en el log.
   - `compatible` → aviso con botones [🔗 Fusionar en una][⛓ Encadenar][▶️ En paralelo]. Sin respuesta tras `compatible.espera_min` (def. 30) → la nueva se encadena como hija de la otra con el mecanismo de `deps.py` (cola; arranca tras integrarse el padre, con `prepare_worktree` desde base fresca). Comentario en la tarjeta con el motivo.
   - `conflicto`/`revisar` → la nueva se **frena** (pasa a `blocked` con motivo `choque`, nunca `running`) y sale aviso con propuesta: «A primero (ya tiene rama/PR), B espera y cambia X», o «fusionar en una», o «descartar B»; botones [Aplicar propuesta][Encadenar][Seguir igualmente (Oscar)][Aparcar].
5. **Menciones**: dueño de cada tarjeta (creador) o de la rama (autor); enlace `tg://user?id=<id>`. Mapeo persona→id y grupo de cada persona: el de **t_89163bdb** (no existe en el repo: hasta entonces `choques.yaml` → `personas:` {nombre, telegram_id, grupo, aliases_git}). Se avisa en el grupo de cada implicado y siempre en Atención para Oscar. Sin id conocido: nombre en texto, sin enlace. Idempotente: clave = conjunto ordenado de ids de tarjeta/rama + clase (como `solapes.clave`); persistido en el fichero de estado del ticker (patrón `SolapeTicker`).
6. **Base fresca en todo el ciclo**: antes de verificar/pasar a review, el runner llama `sync_base` y **re-ejecuta `test_cmd`**:
   - merge limpio → continúa;
   - conflicto → NO pasa a review: tarjeta hija «rebase» (assignee = el mismo carril, padre = la tarjeta) con el texto del conflicto, o aviso a Oscar si `base.al_conflicto: aviso`;
   - rama más de `base.max_atras` commits (def. 20) por detrás de la base → mismo camino (hija de rebase / aviso) sin intentar el merge automático;
   - nunca PR sobre base vieja.
7. **Cuándo corre**: (a) al crear/pasar a `ready` (gancho del clasificador de t_6f6f86b2, cuando exista; antes, el barrido), (b) barrido periódico `choques.minutos` (def. 10) junto al `SolapeTicker`, (c) previo a review (punto 6). Un fallo de git/`gh` degrada a «sin información»: nunca bloquea una tarjeta por error propio.

## Entregas
| | Contenido | Riesgo |
|---|---|---|
| **E1** | `choques.py` núcleo + `choques.yaml`: zona real (ramas lane/*, PR, ramas de personas), `merge-tree`, clasificación, contradicción por patrones. Solo avisa (sin frenar ni encolar), con propuesta. | Bajo: lectura de git |
| **E2** | Menciones `tg://` + grupos por persona (depende de t_89163bdb) e idempotencia. | Bajo-medio |
| **E3** | Acciones: compatible → botones + cola por `deps.py` (depende de t_8125ad1d); conflicto → freno a `blocked`. | Medio: toca estado de tarjetas |
| **E4** | Base fresca: `sync_base` + tests antes de review, hija de rebase, `max_atras`. Predicción barata por LLM (opcional, off). | Medio-alto: toca el camino de `runner` hacia review |

## Tests (`tests/test_choques.py`, sin red, sin `git`/`gh` reales: dobles y `tmp_path`)
Aditivo en paralelo (ficheros distintos; y mismo fichero con merge-tree limpio) · compatible → aviso y, tras la espera, cola/dependencia · conflicto → freno + aviso + propuesta · misma persona (tarea hecha/en cola que contradice) · rama de persona no-lane (dueño = autor, mención) · base desactualizada → sync + tests antes de review; conflicto o `max_atras` → hija de rebase · idempotencia · yaml sobrescribe umbrales; ausente = defaults · fallo de git/`gh` = sin información, no bloquea.

## Decisiones tomadas (recomendada aplicada, reversibles en yaml)
- Corte en E1→E4; E1 sola da valor (detección + aviso) sin riesgo.
- `merge-tree --write-tree` requiere git ≥ 2.38: comprobar versión; si es menor, cae a «comparar ficheros» (conservador: mismo fichero = `compatible`).
- Predicción por LLM apagada por defecto.

## Pendiente de Oscar
- OK a este corte y a empezar por E1.
- Que t_89163bdb (mapeo persona→grupo) y t_8125ad1d (dependencias) estén disponibles antes de E2/E3; si no, E2 usa `personas:` en yaml.
