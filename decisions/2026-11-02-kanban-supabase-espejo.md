# Kanban a Supabase (F2): medición, espejo nocturno y plan de corte

Fecha: 2-nov-2026 · Tarjeta t_12fc09a5 · Ola 2 del hub · Estado: **propuesta, sin cortar nada**
Base: `decisions/2026-10-25-arquitectura-hub-v2.md` (kanban propio en Supabase, esquema `hub`, 2 semanas de espejo).

## Qué hay hoy (verificado en el código)

- `tools/kanban_migrar.py`: `recuentos`, `copiar`, `importar [--seco]`, `exportar`/`volver [--espejo --retener 14]`,
  `comparar` y, nuevo, `analizar`. Los SQLite de origen se abren siempre `mode=ro`.
- `agent_lanes/kanban_api.py`: dos backends, `hermes` (por defecto) y `supabase`. Selector: `KANBAN_BACKEND` >
  `.env` del hub > `runner.kanban_backend` de lanes.yaml. Se resuelve una vez por proceso: el cambio rige al reiniciar.
- El esquema `hub.*` está vacío. Conexión por `HUB_DB_DSN` (nunca en la línea de órdenes ni en el repo).

## Lo que cambia en esta tarjeta

`importar --seco` y `comparar` necesitan Postgres (`HUB_DB_DSN`), y `importar --seco` ejecuta los INSERT dentro de una
transacción que luego deshace: es una escritura transitoria en Supabase. Esta tarjeta tiene prohibido escribir en
Supabase, así que se añade **`analizar --origen <dir>`**: la misma comprobación del importador **sin Postgres**.

- Calcula el resultado esperado de `importar` (por board: tarjetas por estado, comentarios, eventos y ejecuciones) solo
  leyendo los SQLite.
- Lo contrasta con el origen usando la propia función `comparar`: lo que el importador saltaría (filas huérfanas) sale
  como diferencia.
- Lista las filas que el esquema rechazaría (id que no es `t_xxxxxxxx`, sin título, estado o `block_kind` fuera del
  CHECK, id repetido entre boards, ejecución sin `started_at`, evento sin `kind`, comentario sin cuerpo o autor).
- Solo da recuentos, nunca títulos ni cuerpos. Código de salida 0 si no hay filas rechazadas, 1 si las hay.

## Pasos de medición que debe ejecutar el integrador (yo no pude: el guard me impide leer el SQLite vivo de Hermes)

Todo en solo lectura, sobre una **copia** fuera de `~/AppData/Local/hermes`:

1. `py -3.12 tools/kanban_migrar.py copiar --destino C:\tmp\kanban_copia`
2. `py -3.12 tools/kanban_migrar.py recuentos --origen C:\tmp\kanban_copia`
3. `py -3.12 tools/kanban_migrar.py analizar --origen C:\tmp\kanban_copia` (equivale a `importar --seco` + `comparar`
   sin tocar Supabase; si da diferencias o filas rechazadas, se corrigen antes de cualquier importación real)
4. Pegar los tres informes en la tarjeta. Boards a mirar: default, oscarhq y migrateam (los demás vacíos se omiten).

**Resultado de estas medidas: pendiente** (no se ejecutaron en esta tarjeta; ver «Límites»).

## Espejo nocturno de 14 días

- Qué: `exportar --espejo --retener 14` genera `raiz/AAAA-MM-DD/<board>.db` desde `hub.*` con el esquema exacto de
  Hermes. Borra solo carpetas con nombre de fecha de más de 14 días dentro de `raiz`. No escribe nunca en la carpeta de
  Hermes.
- Cuándo: una vez de noche, tras el corte (antes del corte no hay nada que espejar: la fuente sigue siendo el SQLite).
  Programarla es un paso del integrador (tarea programada), no de esta tarjeta.
- Qué mide: tamaño y nº de tarjetas por board cada noche (`recuentos` sobre el espejo frente a `recuentos_pg`). Una
  noche con menos tarjetas que la anterior sin archivado explicativo = alerta.
- Para qué sirve: es la vuelta atrás con datos. El día 14 sin incidentes se retira el espejo y se cierra F2.

## Plan de corte con vuelta atrás por `KANBAN_BACKEND`

| Paso | Acción | Qué mide / criterio para seguir | Vuelta atrás |
|---|---|---|---|
| 0 | `copiar` + `analizar` (arriba) | 0 filas rechazadas, 0 diferencias | nada tocado |
| 1 | Aplicar la migración F2-01 en oscar-hq (integrador, skill migracion-supabase) | `check_contract` del backend supabase pasa | no se usa el esquema |
| 2 | Runner **parado**; `copiar` fresca; `importar` real; `comparar` | `recuentos iguales` en todos los boards | `DELETE` de lo importado lo hace el integrador con OK; `hermes` sigue intacto |
| 3 | `KANBAN_BACKEND=supabase` en el entorno del runner y arrancar | Un ciclo completo de tarjeta (claim→run→review) sin errores; 0 «database is read-only» | `KANBAN_BACKEND=hermes` y reiniciar |
| 4 | Vigilar 24 h: latencia de claims, errores de conexión, tarjetas duplicadas | Sin duplicados ni atascos | igual que paso 3, pero hay que `volver` (ver abajo) |
| 5 | Activar espejo nocturno (14 días) | Recuentos del espejo = `hub.*` cada noche | n/a |
| 6 | Día 14 sin incidentes: dejar `supabase` fijo en lanes.yaml, retirar espejo | — | — |

**Vuelta atrás**: antes de crear tarjetas nuevas en Supabase basta cambiar la variable y reiniciar el runner. Después,
las tarjetas nacidas en `hub.*` no existen en el SQLite de Hermes: hay que `volver` (`exportar` a una carpeta nueva) y
que el integrador, con el runner parado, sustituya los `kanban.db` de Hermes. Por eso `volver` se ensaya antes del
corte con `test_ida_y_vuelta_*` y, una vez, sobre la copia real.

## Qué pasa con Hermes

Hermes **sigue leyendo su SQLite**: no se toca ni se modifica en ningún paso. Tras el corte ese SQLite queda congelado
(foto del momento del corte); Hermes verá un kanban que ya no avanza. Mientras no exista el MCP de lectura de F2,
Hermes no ve el kanban vivo: es un efecto conocido y aceptado, y se avisa a Oscar antes del paso 3. No hay doble
escritura en ningún momento (un solo backend por proceso), para no partir el hub en dos kanbans.

## Riesgos

1. **Dos kanbans vivos**: otro proceso (monitor, bot) con `KANBAN_BACKEND` distinto. Mitigación: el valor se fija en
   el `.env`/lanes.yaml compartido y se verifica en el log `kanban: backend=…` de cada proceso al reiniciar.
2. **Importar con tarjetas en vuelo**: por eso el runner va parado en el paso 2 y `importar` aborta si el board ya
   tiene tarjetas en `hub.*`.
3. **Filas que el esquema rechaza**: `analizar` las saca antes; el importador aborta entero (una transacción).
4. **Latencia/caídas de Supabase** frente a un SQLite local: se mide en el paso 4; la vuelta atrás es la variable.
5. **Pérdida de lo creado tras el corte** si se vuelve a Hermes: cubierta por `volver` y por el espejo.
6. **Secretos**: `HUB_DB_DSN` solo en entorno/.env; los informes llevan recuentos e ids, nunca contenido.

## Límites de esta tarjeta

No se escribió en Supabase, no se cambió `KANBAN_BACKEND` y no se leyó el SQLite de Hermes (el guard del worker bloquea
ejecutar el CLI contra esa carpeta). Los informes reales de `recuentos`/`analizar` quedan para el integrador (pasos de
medición). Código y tests: `tools/kanban_migrar.py` (`analizar`) y `tests/test_kanban_migrar.py` (3 tests nuevos).
