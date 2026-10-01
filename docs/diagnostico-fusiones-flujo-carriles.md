# Diagnóstico: por qué fallan las fusiones del hub y cómo debería fluir el trabajo

Tarea t_bd182c83 · 2026-10-01 · solo diagnóstico y propuesta (no se ha cambiado nada del sistema).

## 0. Resumen para Oscar (1 minuto)

- Los PRs del hub no chocan porque "varios carriles trabajen a la vez": solo corre **un worker a la vez** (`max_workers: 1`).
  Chocan porque el **trabajo se hace en serie pero la integración va muy por detrás**: mientras un PR espera revisión y
  tu aprobación (horas), el siguiente carril arranca desde el mismo `main` y toca los mismos archivos. El segundo en
  fusionar siempre pierde.
- Es **un solo hub y siempre los mismos 3-4 archivos** (`commands.py`, `decisions.py`, `integrator.py`, sus tests).
  Cada función nueva del bot se añade en esos ficheros gigantes (≈900-1700 líneas), así que casi cualquier tarea los toca.
- Cuando el conflicto aparece **justo al fusionar**, el integrador **no lo devuelve solo al carril**: marca "fallido" y
  se queda parado hasta que alguien lo mueva a mano. Y aunque se arregle, el PR vuelve a pasar por revisión y por tu
  aprobación, **reabriendo el mismo hueco** que causó el problema. Es un bucle.
- Tu hipótesis era medio correcta: sí faltan "reservas de archivo", pero **reservar archivos solo no lo arregla** (no hay
  trabajo simultáneo que reservar); lo que falta es una **cola de integración** y que la aprobación sobreviva a una
  resincronización mecánica.

## 1. Evidencia (qué he podido comprobar y cómo)

Fuente: historial de git de `oscar-command-hub` (ramas remotas `origin/lane/*` y `origin/main`) y el código de
`tools/agent-lanes`. **No he podido** consultar la API de GitHub (`gh`, sin permiso en este carril), el kanban de
Hermes ni `.state/` (vetado): los números de PRs/horas salen del git, no de los avisos de Telegram.

### El caso t_2d586164 (PR #5), reconstruido con git
- Su rama y la de **t_bc181313 (promover MigraTeam, PR #4)** nacieron del **mismo commit** `caae7d5` (10:13 del 01-10).
- Las dos modifican `commands.py`, `decisions.py` y `tests/test_commands.py` (los 3 archivos del aviso).
- `t_bc181313` se fusionó a las **11:35** (`7b5ed4e`). Cuando aprobaste #5 (11:38) y se intentó fusionar (11:39), `main`
  ya era distinto → conflicto. Coincide con tu cronología.
- Segunda capa: la rama de t_2d586164 **contenía el commit `dcb0a6a` de otro trabajo (`/hazlo`, "ramas apiladas")**. Ese
  trabajo se fusionó después como PR #7 (`bd161f2`, 13:32) con *squash* (un commit nuevo con otro SHA), así que git ya no
  reconoce `dcb0a6a` como "ya incluido" y vuelve a chocar. Hizo falta una tarjeta entera de rescate
  (`t_c420d80e` "fusión de lane/t_2d586164 con main", 13:07) para arreglarlo.

### Frecuencia (de git, tarde del 01-10)
- 8 ramas `lane/*` abiertas a la vez en el remoto; el 01-10 `main` recibió 5 PRs squash (#1, #2, #3, #4 y #7) en 13 horas.
- Archivos más tocados en `main` desde el 28-09 (nº de commits): `integrator.py` 13 · `decisions.py` 8 · `commands.py` 8 ·
  `test_integrator.py` 6 · `runner.py` 6 · `lanes.yaml` 6 · `renotify.py` 6 · `hermes.py` 6 · `test_commands.py` 5.
- Dos pares de ramas más con la misma base y archivos comunes: `t_32d56b4d` y `t_7a0201c8` (ambas tocan
  `tools/monitor/monitor.py`, base `7b5ed4e`); la que fusione segunda chocará.
- Patrón: **siempre el hub** (los demás repos usan lote diario o cambian archivos dispersos) y siempre
  `commands.py`/`decisions.py`/`integrator.py`/`monitor.py`.

## 2. Causas raíz, por impacto

### Causa 1 · Cola de integración lenta + todas las tareas parten del mismo `main` (impacto: alto, frecuencia: casi cada PR)
- `git_ops.prepare_worktree` crea la rama desde `origin/main` en el momento de reclamar. `sync_base` solo se ejecuta **al
  empezar** una repetición, nunca al terminar ni antes de pedir revisión.
- `claude-hub` no usa `batch` (lote): fusiona PR por PR (`lanes.yaml`, bloque `integrator.lanes.claude-hub`), mientras
  que oscarhq y migrateam ya van por lote diario ordenado.
- No hay límite de PRs abiertos por carril ni reserva de archivos (MigraTeam sí: `check_claims.py`, porque ahí trabajan
  personas y sesiones en paralelo; en el hub nadie lo construyó, y con un worker solo no evitaría nada).
- Archivos "dios" (`commands.py` 940 líneas, `decisions.py` 915, `integrator.py` 1718): máxima probabilidad de solapar.

Opciones:
| | Opción | Riesgo |
|---|---|---|
| A (**recomendada**) | **Lote de integración también para el hub** (reutiliza `batch.py`: fusiona en orden de aprobación, detecta el conflicto en una rama temporal y devuelve solo la rama que choca) **+ resincronizar con `main` al cerrar** (merge de `origin/main` antes de pasar a revisión) | Medio-bajo: el lote lleva código ya probado, pero para el carril "sistema" (`apply: restart_drain`) no está probado; hay que comprobarlo en seco. Cambios de varios PRs entran juntos y se aplican con un solo reinicio |
| B | Límite de **1 PR abierto sin fusionar** en `claude-hub`: no se reclama otra tarea del hub hasta que se fusione la anterior | Bajo de implementar; **lento** (el hub avanzaría al ritmo de tus aprobaciones) |
| C | Partir `commands.py`/`decisions.py` en un módulo por comando/decisión (menos solape) | Alto esfuerzo y riesgo de regresión; a medio plazo, fuera de este arreglo |

### Causa 2 · El hueco "aprobado → fusionado" no se cierra solo (impacto: alto, cada vez que se da)
Lectura del código (`integrator._merge`, líneas ~1094-1165):
- Sí revalida: si `main` cambió desde los gates, repite los gates en una copia temporal. Bien.
- Pero si los gates repetidos fallan por conflicto, solo hace `status="failed"` + comentario. La **devolución automática al
  carril** (`_send_back_conflict`) existe únicamente en la **primera** pasada de gates (`_consider`, ~línea 613), no aquí.
  Además `status=failed` con la misma cabeza hace que el integrador deje de mirar ese PR (línea ~606) hasta que
  alguien cambie algo: **requiere intervención manual**.
- Los reintentos con espera (10/30/60 s) solo cubren la carrera "Base branch was modified", no un conflicto real.
- Aunque el carril lo resuelva (máx. 2 devoluciones), el PR vuelve a revisión + a tu aprobación: otro hueco de horas
  en el que `main` se mueve otra vez → bucle (el caso de t_2d586164 pasó 2 veces).
- No hay "resincronización mecánica" sin LLM: GitHub fusiona solo cuando no hay conflicto textual, pero cuando lo hay
  todo pasa por un worker caro (Sonnet, 3 $, serie).

Opciones:
| | Opción | Riesgo |
|---|---|---|
| A (**recomendada**) | En `_merge`, un conflicto al fusionar se **devuelve al carril con prioridad** (misma lógica que la primera pasada) y avisa a Oscar en Telegram ("aprobado pero `main` cambió: se está resolviendo"). Paso 2: **aprobación heredada** si el *patch-id* del cambio propio (diff excluyendo lo traído de `main`) no cambia y los tests pasan; si cambia → vuelve a pedir OK | Bajo el paso 1. El paso 2 es el delicado: una resolución puede cambiar el significado; mitigado por patch-id + tests + límite de tamaño. Toca el integrador (carril "sistema"; requiere tu OK de spec) |
| B | Solo avisar claro a Oscar ("aprobado pero con conflicto; el carril lo resuelve") sin automatizar la devolución | Mínimo, pero el bucle sigue |
| C | Con la Causa 1-A, el lote en una rama temporal ya valida el estado combinado: reduce el hueco a minutos | Depende de A de la causa 1 |

### Causa 3 · Ramas apiladas + fusión *squash* (impacto: medio, ya visto en t_2d586164)
Una tarea que nace sobre la rama de otra aún no integrada (`Rama-origen`) arrastra commits que luego `main` recibe
con otro SHA (squash): conflicto garantizado y trabajo duplicado. El integrador ya sabe de dependencias
(`pending_parents`, estado `waiting_deps`), pero el **runner no las respeta al reclamar**: deja empezar al hijo.

Opciones:
| | Opción | Riesgo |
|---|---|---|
| A (**recomendada**) | El runner **no reclama** una tarea con padre aún sin fusionar (la deja en espera con motivo visible); cuando el padre se fusiona, el hijo nace de `main` limpio | Bajo; retrasa los hijos, pero evita el retrabajo |
| B | Permitir apilar, pero el integrador **fusiona padre e hijo en el mismo lote** (en orden) | Medio: depende de la Causa 1-A |
| C | Pasar de squash a merge normal para el hub | Alto: ensucia el historial y cambia el contrato de fusión |

### Causa 4 · El revisor no ve los tests (impacto: bajo en datos, medio en confianza)
`review.py` solo permite a la revisora `git diff/log/show/status` y `Read/Grep/Glob`; el `role revisor.md` le pide
comprobar que "los tests existen y prueban lo que dicen" pero **no le pasa el resultado** del `test_cmd` que el runner
ya ejecutó antes (`verify.py`). Resultado: "no he podido ejecutar los tests" en cada revisión (visto en t_2d586164).
Opciones: **A (recomendada)** inyectar en el prompt de revisión el resultado verificado del runner (comando, exit code,
nº de tests) y decir al revisor que no intente ejecutarlos — riesgo nulo, no toca el guard de seguridad; B darle
`pytest` permitido — **afloja el contrato** (ejecuta código del worker con permisos de Oscar), no recomendado; C dejarlo.

### Pendiente de verificar (no he tenido acceso al kanban/logs)
- **Tareas "blocked" que piden la misma decisión repetida**: la regla de autonomía del 01-10 y `fix/preguntas-en-secuencia`
  ya atacan esto; falta medirlo con `block_loop_detected` del kanban (el auditor diario ya lo cuenta).
- **Doble claim de la misma tarea por sesiones distintas**: el claim de Hermes es un *lease* con TTL (`hermes.claim`) y
  el runner lo exige antes de empezar, pero la skill `/tomar-tarea` (consola interactiva) reclama por su cuenta. La
  hipótesis es solapamiento runner ↔ consola; hay que mirar los eventos `claimed` duplicados del kanban.

## 3. Cómo debería funcionar (con los puntos de control que faltan)

```
 1 Solicitud   Telegram (DM / tema) o /hazlo → tarjeta en Hermes (triage)
               [FALTA] declarar "Archivos probables" / dependencias en la tarjeta (alimenta el punto 2)
 2 Admisión    El runner reclama (lease) SOLO si: sin padre pendiente (Causa 3) y sin PR del mismo carril
               pendiente sobre los mismos archivos o límite de PRs abiertos del hub (Causa 1)
 3 Carril      Rama lane/<id> desde origin/main FRESCO + worker implementa + tests
 4 Cierre      [FALTA] merge de origin/main ANTES de pasar a revisión + tests otra vez (resync al cerrar)
 5 Revisión    Revisor (solo lectura) recibe el resultado de tests ya verificado (Causa 4)
 6 Aprobación  Tú pulsas ✅ Aprobar → entra en la COLA DE INTEGRACIÓN (no se fusiona suelto)
 7 Integración [FALTA] lote/cola ordenada: rama temporal = main + PRs aprobados en orden; gates de conflicto,
               secretos, rutas sensibles, tests sobre el estado COMBINADO
               · conflicto → vuelve al carril con prioridad y aviso; aprobación heredada si el patch-id propio no cambió
 8 Fusión      Tu botón [🔀 Fusionar] (un clic por lote); squash con --match-head-commit; reintento solo en carrera
 9 Despliegue  Por riesgo: 🟢 staging solo / 🟠 botón 🚀 / 🔴 producción con doble confirmación / ⚙️ sistema = [🔁 Aplicar]
10 Verificación  Sonda del despliegue o reinicio ordenado + aviso final en 🚦 Integración
```

Controles que faltan hoy, por orden de coste/beneficio: (4) resync al cerrar · (7) lote para el hub ·
(7b) conflicto al fusionar devuelto solo · (2) respeto de dependencias al reclamar · (5) tests visibles al revisor.

## 4. Orden recomendado de implementación (para que decidas)

1. **Causa 4-A** (media hora, sin riesgo): tests visibles para el revisor.
2. **Causa 2-A paso 1** + **resync al cerrar** (Causa 1-A, mitad): cerrar los dos huecos con cambios pequeños.
3. **Causa 3-A**: respetar dependencias en el runner.
4. **Causa 1-A, mitad lote** para `claude-hub`: probar primero en seco (`--dry-run`), porque es el carril "sistema".
5. **Causa 2-A paso 2** (aprobación heredada) solo después de tener métricas de los pasos anteriores.
6. **Causa 1-C** (partir `commands.py`/`decisions.py`) como trabajo gradual, sin urgencia.

Decisiones que necesito de ti: (a) ¿activar lote para el hub? · (b) ¿te vale la "aprobación heredada" cuando el
cambio propio es idéntico y los tests pasan, o prefieres aprobar de nuevo siempre?
