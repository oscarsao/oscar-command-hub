# Spec-Lite · Limpieza propuesta de tarjetas resueltas (t_62f89e84)

**Problema.** El kanban acumula tarjetas abiertas que ya están resueltas de hecho. Se quiere que el sistema las
**proponga** (nunca que las cierre solo). Complementa a `reconcile.py` (spec `reconciliacion.md`), sin tocarlo: aquello
vincula PR→tarjeta y propone obsoletas con reglas fijas; esto añade reglas configurables y el botón de cierre con motivo.

**Módulo** `agent_lanes/limpieza.py` + `limpieza.yaml` (se relee en cada pasada: editarlo no pide reiniciar).
1. **Detector** (función pura `detectar`), una vez al día pasada `hora` (Madrid), sobre los tableros de los carriles
   (o los de `tableros`). Cada candidata lleva *motivo* y *evidencia*. Reglas (`reglas:` activa/inactiva cada una):
   - `pr_mergeado`: PR mergeado que cita la tarjeta (rama `lane/<id>`, id en título o `Cierra: t_x` en el cuerpo).
     **Falso positivo evitado:** si la tarjeta tiene eventos o comentarios humanos DESPUÉS del merge, se reabrió: no se propone.
   - `hermana_hecha`: otra tarjeta `done` con el mismo título normalizado.
   - `decidir_respondida`: tarjeta bloqueada cuyo último comentario es de Oscar (respuesta ya dada).
   - `duplicada`: dos abiertas del mismo carril con título parecido (`similitud`, def. 0,85); se propone la más nueva → archivar.
   - `oscar_parada`: asignada a `oscar` sin movimiento `dias_sin_movimiento` (def. 7). El propio comentario del job no cuenta como movimiento.
   Nunca propone tarjetas `running`, ni las silenciadas (Mantener), ni las ya propuestas hace menos de `dias_silencio`.
2. **Propuesta en Telegram**: un mensaje con una línea por candidata «t_x · título · motivo · evidencia» y por
   cada una una fila [✅ Cerrar][⏳ Mantener][👁 Ver] (máx. `max_propuestas`). Cerrar = `complete --result <motivo>`
   (`archive` en duplicadas); Mantener = silencio `dias_silencio` días (def. 14); Ver = resumen del `show`. Sin respuesta, nada.
3. **Config** (`limpieza.yaml`): `activo`, `hora`, `dias_sin_movimiento`, `dias_silencio`, `similitud`, `max_propuestas`,
   `tableros`, `reglas`. Valor inválido → por defecto, nunca lanza.
4. **Registro**: al proponer, comentario «propuesta de cierre: motivo»; al decidir, «Oscar: cerrada…» / «Oscar: mantener hasta <fecha>».
5. **`--dry-run`**: `python -m agent_lanes.limpieza --dry-run` imprime las candidatas sin enviar, comentar ni guardar estado.

**Fuera de alcance:** cerrar sin botón; tocar el runner de carriles o `lanes.yaml`; cambiar `reconcile.py`.
**Tests:** `tests/test_limpieza.py`: una por regla, el falso positivo (PR mergeado + reabierta), silencio, config y botones.
