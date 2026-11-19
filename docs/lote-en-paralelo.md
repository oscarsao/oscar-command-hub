# Lote: preparar en paralelo, fusionar en serie

Decisión de Oscar (19-11-2026): la sesión de integración detecta los choques ANTES de fusionar y deja los PR preparados
en paralelo; **la fusión sigue siendo en serie, una a una, y con el botón de Oscar**.

## 1. Comprobación previa (solo lectura)

`agent_lanes/preflight_lote.py` no escribe nada (ni refs). Dadas las ramas del lote en orden de aprobación informa de:

- **Numeración de esquema SQL** (`supabase/migrations/`): versión repetida entre ramas o ya existente en la base, o menor
  que la última de la base (fuera de secuencia). **Bloquea** esa rama. Si dos ramas traen la misma versión, se conserva la
  primera de la lista.
- **Solapes**: ficheros tocados por más de una rama del lote o por otros PR abiertos. **Solo avisa.**
- **Fallos previos**: separa los fallos nuevos de los ya conocidos de `docs/fallos_previos.json` (lista curada a mano).

En el lote diario (`batch.py`) corre solo, como paso informativo: la ficha del lote y el cuerpo del PR llevan el informe,
la rama con choque de numeración sale del lote con su motivo y los solapes salen como «Avisos». Se apaga con el ajuste
`lotes.preflight` (`/ajustes`).

Uso suelto:

```
py -3.12 -m agent_lanes.preflight_lote --repo C:\ruta\clon --base origin/main \
    --ramas "#12=origin/lane/t_a,#15=origin/lane/t_b" --otros origin/feat/x --prefijos supabase/migrations/
```

Salida 1 si hay choque de numeración (o error), 0 si no. Un número (`12`) se lee de `refs/integrator/pr-12` o
`refs/pull/12/head`: hay que traerlos antes con `git fetch`.

## 2. Preparar en paralelo, fusionar en serie

1. La sesión de integración lanza el preflight con todo el lote y anota el informe en la tarjeta.
2. Por cada PR **sin choque de numeración**, un agente en **su propio worktree** (`git worktree add <carpeta> -b prep/<pr>
   <rama>`; nunca el checkout raíz) deja el PR listo: actualiza con la base, pasa su `test_cmd`, corrige lo mecánico
   (p. ej. renumerar su esquema si la rama es suya) y empuja **solo su rama**. Estos agentes trabajan a la vez.
3. Los PR con **solape de ficheros** se preparan igual, pero se fusionan en un orden fijo y el siguiente se actualiza con
   la base DESPUÉS de fusionar el anterior (el solape se resuelve en ese momento, no antes).
4. La fusión es **una a una, bajo el candado del integrador y con el botón de Oscar**: ningún agente de preparación
   fusiona, hace `--admin` ni empuja a la base.

### Ejemplo (19-11-2026, oscar-hq)

Lote: `#41 t_a` y `#42 t_b` traen `20261409_*.sql`; `#43 t_c` trae `20261118_*.sql`; `#44` y `#45` montan rutas en
`src/api/main.py`; `#46` y `#47` tocan `dashboard/package.json`. La base llega hasta `20261408`.

```
py -3.12 -m agent_lanes.preflight_lote --repo oscar-hq --base origin/master --ramas "#41,#42,#43,#44,#45,#46,#47"
```

- ⛔ `#42`: versión 20261409 repetida (la conserva `#41`) → agente de preparación renumera su esquema a `20261410`.
- ⛔ `#43`: 20261118 fuera de secuencia (última 20261408) → se renumera por encima de la última.
- ⚠ `src/api/main.py`: `#44`, `#45` y ⚠ `dashboard/package.json`: `#46`, `#47` → se preparan en paralelo y se fusionan en
  este orden, actualizando cada segundo PR con la base tras fusionar el primero.

Los cuatro grupos se preparan a la vez (un agente y un worktree por PR); Oscar pulsa el botón de cada fusión en serie.
