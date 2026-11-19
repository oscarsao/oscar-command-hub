# Diagnóstico: el hilo principal del runner tarda más de 60 min por vuelta (t_b9861cd4)

Fase 1, solo lectura. Fecha de los datos: logs del 18 y 19-11-2026 (`.state/runner.log` y `runner.log.2026-11-18`,
solo leídos). Relacionadas: t_e3018aaf (`git worktree add` > 300 s), t_a15b2137.

## Resumen

- El planificador (`run_scheduler`, hilo `lane-scheduler`, `runner.py:79-98`) ya está separado del hilo principal
  desde el 23-10. Por eso "el planificador sigue lanzando" y, a la vez, "el principal tarda más de 60 min": son dos
  hilos distintos. Lo que sufre el hilo principal no frena los lanzamientos; frena lo que solo hace él.
- El hilo principal (`runner.py:358-445`) ejecuta **en serie y sin tiempo máximo por paso** limpieza de worktrees,
  integrador (con gates y `test_cmd`), ocho tickers, refresco del canal, reintento de decisiones y el recuento de
  resultados (`service.pop_results()`, `runner.py:435`). Una vuelta dura la suma de todos los pasos + `sleep(interval)`
  (60 s, `lanes.yaml:19`).
- **No hay medición por paso.** El log solo escribe cuando un paso encuentra algo; un paso lento y sano no deja
  rastro. Por eso los "huecos de 20 a 70 min sin ninguna línea" no se pueden atribuir con certeza a un paso concreto
  con lo que hay hoy. Lo que sí se puede es acotar cada paso por código y fechar vueltas largas con las pasadas.
- **Candidato principal (confianza media):** `integrator.run_pass()` (`runner.py:384-389`), porque es el único paso
  del hilo principal con esperas de hasta 1800 s *por PR* (`integrator.py:1202`) y por rama de lote (`batch.py:509`,
  `batch.py:541`) y varios PR en serie. Segundo candidato: la limpieza de worktrees (`runner.py:378-383`), con
  borrados de 5 a 120 s por tarjeta medidos. Tercero: el bloque de tickers del final (20 min medidos el 19-11).
- **Lo que NO es:** Hermes. En el 18 y el 19-11 hay 0 líneas "sin respuesta en … s" (el timeout de la CLI de Hermes,
  `hermes.py:431`). Los avisos de "base de datos read-only" duran 1 s de reintento cada uno (`hermes.py:446-450`).
- **Los 16 `git worktree remove` que superan 300 s no son del hilo principal:** son del carril review, en hilos
  `lane-flight_*` (`review.py:446` → `git_ops.py:306`). Cuestan plazas de ese carril, no vuelta del principal.
  Pertenecen a t_e3018aaf. Las 2 verificaciones con `test_cmd` > 30 min del 18-11 también corren en `lane-flight_*`
  (`runner.py` del paquete, `verifier`, `verify.py:84`), no en el principal.

## Cómo está construida una vuelta (orden real, `tools/agent-lanes/runner.py`)

| # | Paso | Línea | Acotado por | Medido en los logs |
|---|------|-------|-------------|--------------------|
| 1 | latido + `shows.clear()` | 359-361 | nada que espere | ~0 |
| 2 | `sweep_done` por carril de código: `hermes list` + `git cleanup` por tarjeta done con carpeta | 378-383 → `review.py:492-509`, `git_ops.py:293-331` | por llamada: `list` ≤ 120 s × 3 intentos + 25 s; `worktree remove` 300 s, `worktree list` 60 s, `branch` 60 s; sin tope por paso | borrado de carpeta huérfana: 5-120 s cada una (04:53:27→04:57:03 del 18-11: ~100 s; 12:20:19→12:22:34: 28-60 s; 22:57:41→22:57:51: 5 s). Sweeps de 2-5 min por vuelta |
| 3 | `integrator.run_pass` | 384-389 → `integrator.py:574-623` | throttle 300 s (`lanes.yaml:445`); dentro, por tarjeta done: `hermes show` + `gh pr view` (180 s, reintentos); por PR nuevo: fetch + `worktree add` + merge + `test_cmd` **1800 s** en el propio hilo, bajo `self._lock` (`integrator.py:1096`, `1202`); `batches.run_due()` (`integrator.py:618`): 1800 s por rama | no se escribe nada al ir bien. `lote diario` 09:15:30 del 19-11 es la única huella |
| 4 | tickers `guard`, `solapes`, `choques`, `reconcile`, `limpieza`, `recolector`, `paradas`, `avisos_ticker` | 390-405 | cada uno con su propio ritmo (10 min) y llamadas a `git`/`gh`/`hermes` (120 s por llamada en choques, `choques.py:198`) | `choques` termina a las 09:21:54; el siguiente rastro es a las 09:41:52 (diario): **~20 min** en los pasos 4-8 del 19-11 |
| 5 | `canal.refrescar`, `wf_ticker`, `reasignar_abiertas`, `retry_pending`, `diario.tick`, `hub_panel.tick` | 406-429 | llamadas a Telegram/Hermes con timeouts propios | incluido en la fila anterior |
| 6 | `service.pop_results()` y `sleep(interval)` | 435, 445 | 60 s | — |

## Vueltas largas fechadas (cota inferior, a partir de las líneas `pasada:`)

`pop_results()` es lo último de la vuelta. Un resultado que ya había terminado y no sale hasta una pasada posterior
fija una cota mínima de duración de la vuelta que lo recogió.

| Día | Resultado terminado | Recogido en `pasada:` | Vuelta ≥ |
|-----|---------------------|-----------------------|----------|
| 18-11 | `t_beadcaa4` y `t_1632ed17` terminan (error del review) 19:08:12 / 19:32:43 | 19:48:23 | **≈ 40 min** |
| 19-11 | `t_bca8ab62` bloqueada (guard) 09:19:44 | 09:42:03 | **≈ 21 min** |

Son cotas, no la duración exacta. Entre el 18-11 18:32:49 (pasada) y el 19:48:23 (siguiente) solo hay una línea
`MainThread` de sweep a las 18:34:00, compatible con una vuelta de ≈ 75 min, pero un pop con resultados vacíos no deja
línea y no se puede descartar. En la vuelta del 18-11 19:48 el tramo tras `choques` fue de 22 s: el tiempo se fue
**antes** de `choques` (pasos 2-3 y primeros tickers). En la del 19-11 09:21, el tramo tras `choques` fue de ~20 min:
ahí el tiempo se fue en los pasos 4-5. O sea, **no hay un único paso culpable**: hay varios sin tope y cualquiera
puede llevarse la vuelta.

## Qué paso bloquea al resto y por qué

1. **El diseño: todo en serie y sin tope por paso** (`runner.py:378-429`). Cualquier llamada lenta de cualquier
   paso retrasa a los siguientes y también `pop_results` (los avisos "pasada", `busy.json` y la rotación de
   resultados salen tarde). Solo el planificador está aislado.
2. **`integrator.run_pass` con gates en el hilo principal** (`runner.py:386`, `integrator.py:791`, `1202`): hasta
   30 min por PR con `test_cmd` (en claude-hub incluye la suite completa de `tools/agent-lanes/tests`,
   `lanes.yaml:293`) y bajo un cerrojo que además hace esperar a los botones de Oscar (`integrator.py:1096`,
   `2010`). Con 2-3 PR nuevos en una pasada, una vuelta pasa de 60 min sin ningún fallo.
3. **Sweep sin tope y sin memoria de fallos por timeout** (`review.py:492-509`): el reintento de 6 h
   (`REINTENTO_LIMPIEZA`) solo se anota si `cleanup` devuelve `(False, msg)`. Si lanza `TimeoutExpired`
   (`git_ops.py:306`, 300 s) la excepción atraviesa `sweep_done`, el `except` de `runner.py:382` solo la registra y
   la misma tarjeta se reintenta en la vuelta siguiente: otros 300 s cada minuto. Con `worktree remove` colgado en
   ese worktree, es una fuga continua.
4. **El `timeout` de `proc.run` no es un tope real en Windows** (`proc.py:15-21`): sin `on_start` llama a
   `subprocess.run(..., timeout=)`. Al vencer, CPython mata solo el proceso directo y en Windows vuelve a hacer
   `communicate()` para vaciar las tuberías; si un proceso nieto (p. ej. los hijos de git o pnpm) conserva el
   extremo abierto, esa espera no termina. La ruta con `on_start` sí usa `kill_tree` (`proc.py:36-40`). **Hipótesis a
   confirmar midiendo**: encaja con timeouts de "300 s" que en el log aparecen más de 9 min después de empezar la
   operación (18/19-11), pero el log no registra el inicio de cada llamada.
5. Los 4 pasos finales dependen de Telegram y `gh`; el 18-11 `getUpdates` dio 502 durante 30 min (01:17-01:48) y
   hay `gh`/Hermes sin tope por paso. No hay datos de que hayan bloqueado el principal; se citan por completitud.

## Arreglo mínimo propuesto (segunda tarjeta; NO implementado aquí)

Orden por relación coste/beneficio, cada uno independiente:

1. **Medir antes de cambiar nada (≈ 1 h, solo logging).** Envolver cada paso de la vuelta en un cronómetro y escribir
   una línea `vuelta principal: <paso> <s>` solo si supera 30 s, más el total de la vuelta si supera 5 min. Más
   anotar inicio y fin de cada `git worktree add/remove` y de cada `test_cmd` del integrador. Test: la vuelta devuelve
   lo mismo y llama a los mismos pasos en el mismo orden con y sin cronómetro. Con 1-2 días de datos se confirma o
   descarta el candidato principal.
2. **Sacar `integrator.run_pass()` del hilo principal** (un hilo propio, como el planificador y `lane-reminders`),
   con el mismo cerrojo `self._lock`. `busy()` ya lo cuenta para el reinicio ordenado. Es el cambio de más efecto: el
   único paso con esperas de 30 min por elemento.
3. **Sacar `sweep_done` a un hilo propio y arreglar su memoria de fallos:** anotar `_limpieza_fallida` también ante
   excepción (timeout) y un tope de N tarjetas o de X segundos por pasada.
4. **Un tope real de tiempo para `proc.run`:** usar siempre la ruta con `kill_tree` (o `Popen` + `taskkill /T` al
   vencer) también sin `on_start`. Tocar `proc.py` afecta a todas las llamadas externas: va con tests y entra solo
   después del paso 1, cuando se haya confirmado la hipótesis 4. Es el arreglo de fondo de t_e3018aaf.
5. **Tope por ticker** (el ticker se salta si el anterior sigue en marcha, no espera): solo si la medición del paso 1
   los señala.

No tocar `lanes.yaml` (p. ej. subir el timeout) ni bajar `interval_seconds`: no atacan la causa y el 23-10 ya se vio
que el planificador separado absorbe esa parte.

### Texto listo para la tarjeta 2 (claude-hub)

> **[Hub · Runner] Medir cada paso del hilo principal y sacar el integrador y la limpieza a hilos aparte · fix**
> Repo oscar-command-hub (main), `tools/agent-lanes`. Origen: diagnóstico t_b9861cd4
> (`tools/agent-lanes/docs/diagnostico-hilo-principal-lento.md`). Hacer, en este orden y en PR separados si pasa de
> 1 h: (1) cronómetro por paso en `runner.py` (log solo si > 30 s) + inicio/fin de worktree y `test_cmd` del
> integrador; (2) `integrator.run_pass` en hilo propio; (3) `sweep_done` en hilo propio y registrar fallo también ante
> `TimeoutExpired`; (4) valorar tope real en `proc.run` (con `kill_tree`) solo si el paso (1) confirma la hipótesis.
> Criterio de hecho: ninguna vuelta del hilo principal supera 10 min con el integrador o la limpieza lentos
> (test con dobles que se cuelgan) y el orden y resultado de los pasos no cambia. Límites: no reiniciar el runner;
> el cambio rige cuando Oscar fusione y pulse [🔁 Aplicar]. Relacionadas: t_e3018aaf, t_a15b2137.

## Lo que falta por medir (y de dónde sale)

- Duración real de cada paso: no existe hoy; la da el paso 1 de arriba. Hasta entonces, `.state/runner-latido.json`
  (`supervisor.Latido`, `runner.py:360`) guarda el último latido "principal" y sirve para ver en vivo cuánto lleva
  parada una vuelta.
- Si los timeouts de 300 s superan de verdad los 300 s de reloj (hipótesis 4).
- Cuántas tarjetas `done` y PR nuevos entran en cada pasada del integrador (el log no lo cuenta).
