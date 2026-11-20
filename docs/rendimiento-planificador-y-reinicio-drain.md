# Rendimiento del planificador y reinicio con drain (t_beb8fbf5, 20-11-2026)

## Alcance y límite de este informe

### Medición sobre las copias de la Integradora (20-11-2026, solo lectura, `mode=ro`)

La BD **no es el cuello de botella**. Tamaños y consultas equivalentes a `list` y `show` (SQLite directo, sin el CLI):

| Tablero | Tamaño | Tarjetas | Eventos | Comentarios | `list` por estados | `show` de una tarjeta (4 consultas) |
|---|---|---|---|---|---|---|
| default | 4,2 MB | 382 | 4.127 | 1.363 | 3,3 ms | ≈0,7 ms |
| migrateam | 4,5 MB | 269 | 3.824 | 1.797 | — | — |
| oscarhq | 22 MB | 1.375 | 17.464 | 6.555 | 4,5 ms | ≈0,9 ms |

Ya existen índices en `status`, `assignee+status`, `task_id` de eventos/comentarios/runs, enlaces padre/hijo, etc.;
máximo 177 eventos y 169 comentarios por tarjeta. Por tanto **no hace falta índice, poda ni vaciado de heartbeats**: no
se propone tocar la BD. Los 120 s de `show` y los 1.204 s de «solapes» no vienen de los datos sino del coste de cada
llamada al CLI `hermes` (arranque del proceso, contención con el resto de procesos de la vuelta) multiplicado por el
número de llamadas; de ahí que los arreglos de este PR reduzcan y acoten las llamadas (presupuesto, caché, hilo aparte)
en lugar de tocar la BD. Esa medición con el CLI real (`hermes kanban show`) no se hizo: queda para la Integradora
cronometrarla en el equipo vivo. No se ha copiado contenido de las copias al repo.

Resto de límites:

- El diagnóstico sale de leer el código de agent-lanes (que es donde están los 1.204 s y los 120 s).
- Las mediciones «antes/después» de este documento son **simuladas** (reloj falso, Hermes falso que «cuesta» lo que
  cuesta un timeout real) y están en `tests/test_rendimiento_planificador.py`. Prueban el mecanismo, no el tamaño real
  de la BD.
- Lo que depende de la BD de Hermes (índices, poda, vaciado de heartbeats) queda **solo descrito** (apartado 4): vive
  fuera de este repo y habría que probarlo contra una copia que haga la Integradora.

## 1. Dónde está el cuello de botella (por lectura del código; las cifras son hipótesis coherentes, no medidas)

1. **`solapes` bloquea la vuelta principal.** `runner.py` llama a `SolapeTicker.tick()` en línea. Cada pasada hace
   `hermes kanban list --status X` (4 estados × tablero) y, por cada par de tarjetas que comparten ruta, un
   `hermes kanban show <id>` (`solapes._enlazadas`) para ver si son padre/hija. Con 10 tarjetas solapadas son ≥10
   `show`.
2. **Un `show` que agota el timeout cuesta ~385 s**, no 120: `lanes.yaml` fija `cli_timeout_seconds: 120` y
   `cli_timeout_backoff: [5, 20]`, y `show`/`list` son idempotentes (`TIMEOUT_IDEMPOTENT`), así que se reintentan:
   120 + 5 + 120 + 20 + 120 = 385 s. **Tres llamadas colgadas ≈ 1.155 s**, que es el orden de los 1.204 s de «solapes»
   que vio la Integradora. Una `list` o `show` colgada es la causa; el planificador de workers (hilo propio) no.
3. **El drain solo acusa entre vueltas.** `runner.py` escribe `drain_ack.json` al empezar cada vuelta del bucle
   principal. Mientras `solapes` (o cualquier ticker) está dentro de una llamada colgada, el acuse no llega, y con él
   `Restarter` espera hasta `--timeout`. Es una de las causas de los 1.500 s y 2.700 s.
4. **La otra causa son los workers de larga duración.** El acuse `idle` exige que no quede ningún worker (`service.busy_tasks()`), 0 hilos
   `sweep-done`/`integrador-pasada` activos y 0 deploys/gates en marcha. t_ac4dbbe0, t_dfba8206 e `integrador-pasada`
   lo mantienen en falso hasta que terminan solos; el drain no mata a nadie (por diseño, incidente t_bdee05fe).
5. Sin datos reales, **no** se puede afirmar que `hermes kanban show` sea lento por la BD (tablas sin índice, miles de
   eventos/heartbeats). Es la hipótesis más probable de por qué una llamada llega a 120 s, pero está sin medir.

## 2. Cambios de este PR (bajo riesgo, solo código del hub)

| Cambio | Dónde | Efecto |
|---|---|---|
| Presupuesto por pasada (`presupuesto_s`, 120 s) y corte tras `max_fallos` (2) `show` seguidos fallidos | `solapes.Pase` | Una pasada ya no puede encadenar decenas de timeouts; lo no leído queda para la siguiente |
| Caché de relaciones padre/hija entre pasadas (`relaciones_min`, 30 min) | `SolapeTicker._recuerdo` | Pasadas posteriores no repiten el `show` de cada tarjeta |
| Un `show` fallido no se repite dentro de la pasada y **no se avisa el par** (antes se avisaba como «no enlazadas») | `solapes._enlazadas` | Sin avisos falsos con Hermes enfermo; se reintenta a la siguiente |
| `solapes` en su propio hilo (`HiloAparte`) | `runner.py`, `vuelta.HiloAparte.tick` | La vuelta principal ya no espera al barrido: el acuse del drain llega aunque `solapes` vaya lento. No cuenta en `busy` (lee Hermes y puede enviar un aviso; un `/End` justo entre el envío y el guardado de `seen` podría repetir ese aviso, caso raro) |
| Pasada cortada = no se avisa ni se marca (evita un aviso parcial y otro después con el grupo completo). Si la caché caduca antes de completar, no converge: subir `presupuesto_s` | `SolapeTicker.tick` | Un solo aviso por conjunto |
| `restart --drain` cuenta cada 5 min qué lo retiene | `drain.Restarter` | La Integradora ve «sigo drenando (900 s de 3600): t_ac4dbbe0, integrador-pasada» y decide con datos |

Los tres ajustes nuevos se leen de `solapes.yaml` (se relee en cada pasada; ausentes o inválidos = valores por defecto;
`0` desactiva el límite). No hay nada hardcodeado fuera de esos defaults.

### Mediciones simuladas (reloj falso; `pytest -s tests/test_rendimiento_planificador.py`)

Escenario: 10 tarjetas del mismo repo que comparten fichero; cada `show` agota el timeout (385 s reales).

| | Antes (`presupuesto_s=0, max_fallos=0, relaciones_min=0`) | Después (defaults) |
|---|---|---|
| Tiempo de la pasada | 3.858 s | 393 s |
| Llamadas `show` | 10 | 1 |
| Vuelta principal bloqueada | sí (todo el tiempo) | no (hilo aparte) |

Escenario sano (`show` de 3 s, 5 tarjetas): la 2.ª pasada y siguientes hacen 0 `show` (antes, 5 en cada una), hasta que
caduca la caché de 30 min.

Tiempos de `show`, `list` y de una vuelta **con la BD real: pendientes de medir** (ver apartado 5).

## 3. Reinicio con drain: procedimiento

Lo ejecuta la Integradora, con OK de Oscar. Este PR no reinicia nada.

1. **Antes de empezar**, ver qué retiene: `.state/drain_ack.json` solo existe en drenaje; antes, mirar
   `.state/runner-latido.json` (workers en vuelo) y el kanban (tarjetas `running`).
2. **Lanzar** `py -3.12 tools\agent-lanes\lanes.py restart --drain --timeout 3600` (el sync_check del checkout vivo
   corre primero y, si falla, no drena).
3. **Seguir la salida**: cada 5 min sale `sigo drenando (N s de T): <workers/hilos>`. Si pone «sin acuse del runner
   todavía», la vuelta principal está dentro de un paso largo (ver `vuelta: paso «…» tardó N s` en `runner.log`).
4. **Terminó bien** cuando imprime `runner reiniciado` y `aplicado: HEAD == origin/main e integrador activo`.
5. **Timeout**: el drenaje se cancela solo, NO se reinicia, se avisa ALTA y los carriles vuelven a reclamar. No es
   un fallo del runner: significa que sigue habiendo trabajo en curso. `drain.json` caduca solo a las 2 h.

### Ruta segura cuando no cierra (sin matar workers)

- Elegir otra ventana, no subir el `--timeout` a ciegas: un worker de 2 h necesita ≥2 h.
- Si lo que retiene es **`integrador-pasada`** y no un worker: esperar; su pasada es síncrona (gates + tests) y no se
  debe cortar a mitad de un merge.
- Si lo retiene un worker concreto, la decisión de parar ese worker es de Oscar (`needs_input`), no del reinicio.
- Con el runner **colgado** (sin latido y sin workers) `restart --drain --colgado` salta la espera; el supervisor ya lo
  lanza solo, máx. 1/hora.
- Mientras el drenaje está activo, nadie reclama tarjetas nuevas: no dejarlo abierto «por si acaso».

### Tiempos esperados y cuándo se considera colgado

| Qué | Tiempo normal | Señal de problema |
|---|---|---|
| Vuelta principal | segundos; el log marca pasos >30 s y vueltas >300 s | un paso >300 s (`vuelta principal de N s`) |
| `hermes kanban list/show` | segundos | 120 s = timeout; 385 s con los 2 reintentos |
| Pasada de `solapes` (tras este PR) | <120 s, y en hilo aparte | log `solapes: pasada cortada (…)` repetido = Hermes lento |
| Drain sin workers | < 1 min | sigue sin acuse tras 5 min: ver paso 3 |
| Worker | lo fija el presupuesto de la tarjeta/carril | **colgado** = sin latido del runner >180 s (`DEFAULT_PARADO_SECS`) o RAM sobre el límite (`GuardiaRam`) |

Un worker **no** se considera colgado por llevar mucho tiempo: solo si el runner deja de latir o excede la RAM. Un
latido parado con workers en vuelo solo avisa; nunca se corta un worker por eso (`supervisor.reinicio_si_colgado`).

## 4. Propuestas que dependen de la BD de Hermes (NO implementadas; migraciones solo descritas)

Hermes está fuera de este repo, así que aquí solo se proponen, para probarlas sobre una **copia** de la BD:

1. **Medir primero**: tamaño del fichero, nº de tarjetas, de eventos y de heartbeats; `EXPLAIN QUERY PLAN` de lo que
   ejecutan `kanban list --status`, `--assignee` y `show` (¿recorre toda la tabla de eventos por `task_id`?).
2. **Índices** (si no existen) sobre la columna de tarjeta de la tabla de eventos/comentarios/heartbeats, y sobre
   `status`/`assignee` de tarjetas. Es reversible (`DROP INDEX`).
3. **`show` con límite**: pedir solo los últimos N eventos (necesita opción en Hermes; hoy `show --json` devuelve todo).
4. **Poda/archivado**: heartbeats y eventos de tarjetas `done` de más de X días a una tabla de archivo; vaciar
   heartbeats antiguos. Con copia previa (ya hay `backup_kanban`) y siempre con el runner en drenaje o parado.
5. **Bloqueos de escritura**: `database is locked`/read-only ya se reintentan (`hermes._transient_readonly`); si la
   medición muestra contención, activar WAL y `busy_timeout` en Hermes.

## 5. Pendiente para la Integradora (con OK de Oscar)

1. Aplicar este PR con el próximo reinicio (junto con #172 y #173, ya en main).
2. Medir con datos reales, **solo lectura y sobre una copia**: `hermes kanban list --json` y `show <id> --json` con
   `Measure-Command` (tiempos de `list` y `show`), nº de tarjetas y eventos; y, tras el reinicio, el log
   `vuelta: paso «solapes»` (debería desaparecer: ya no está en la vuelta principal).
3. Si `show` sigue >10 s con la BD copiada, abrir tarjeta de Hermes con el apartado 4.
