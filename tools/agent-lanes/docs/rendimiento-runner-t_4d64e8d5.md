# Rendimiento del runner (t_4d64e8d5) — medición y cambios

## Qué se cambió
| Cambio | Dónde | Efecto |
|---|---|---|
| Caché de `hermes kanban list` por tablero+estado+assignee (`runner.list_cache_seconds`, 20 s); las escrituras la invalidan y un contador de generación impide guardar un listado empezado antes de una escritura | `agent_lanes/hermes.py` | un listado por tablero y estado por pasada, no uno por lector |
| Contador de procesos por ejecutable (`proc.stats_delta`) y línea de log cada `runner.stats_log_minutes` (5) | `agent_lanes/proc.py`, `runner.py` | permite medir llamadas/min reales en `.state/runner.log` |
| Intervalo de sondeo configurable | `runner.interval_seconds` (ya existía) en `lanes.yaml`, documentado | subirlo baja CPU e hijos |
| Siembra de decisiones: no se reintenta en cada pasada tras un fallo (`REINTENTO_SIEMBRA` = 5 min); ya sembradas no se recalculan hasta la revisión (30 min) o el día siguiente | `agent_lanes/canal_avisos.py` | menos `kanban list/show` desde el hilo `siembra-decisiones` |
| `git worktree prune` del integrador como mucho cada `runner.worktree_cleanup_minutes` (10) por repo | `agent_lanes/integrator.py`, `lanes.yaml` | menos `git` |
| Llamadas sin ventana | `lanes.py` (`proc.run`), `tools/backup_kanban.py` (`CREATE_NO_WINDOW`) | sin consolas |

## Llamadas sin ventana — auditoría
- `proc.run`, `checks.py`, `monitor.py`: ya llevan `CREATE_NO_WINDOW`.
- `integrator.launch_detached` y `ciego.launch_restart`: usan `DETACHED_PROCESS`, que ya no asigna consola (equivale; los dos flags no se combinan). Anotado en el código.
- Dejados fuera a propósito (se lanzan a mano desde una consola): `aplicar_reorganizacion.py`, `hermes-sync/hermes_sync.py`, y el guard (`contract/*`, no se toca).

## Medición
Sintética y determinista (test `test_medicion_antes_despues_llamadas_por_pasada`, 8 lectores × 6 carriles × 3 estados por pasada):

| | Llamadas hermes por pasada | Por minuto (intervalo 60 s) |
|---|---|---|
| Antes (sin caché) | 144 listados + 1 escritura | ~145 |
| Después (caché) | 18 listados + 1 escritura | ~19 |

Reducción de listados: −87 %. **No medido aquí:** CPU media del proceso del runner en 5 min y nº de hijos simultáneos con psutil, ni el smoke en primer plano: las reglas del worker prohíben lanzar `runner.py` contra el kanban real. Tras el merge y [🔁 Aplicar], el log del runner anota cada 5 min `procesos lanzados en N min: git=…, hermes=… (X/min)`, que da el antes/después real; para CPU/hijos, `psutil.Process(pid).cpu_percent()` y `children()` sobre el pid del runner.

`ruff --select F821` sobre `agent_lanes`, `runner.py`, `lanes.py`: limpio.
