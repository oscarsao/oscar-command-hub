# Monitor · vigilancia de la base de Supabase de Oscar HQ

Contexto: el 30-09 y el 01-10 la base (proyecto `kkkxptlwknyafbavajhj`, plan Micro 1 GB, máx. 60 conexiones) se saturó;
Oscar HQ estuvo caído de 09:40 a 11:30 del 01-10 y la causa no se pudo confirmar porque el reinicio borró
`pg_stat_statements`. Ahora el monitor headless sondea la base cada 5 minutos, guarda fotos y avisa antes de que se cuelgue.

Código: `tools/monitor/dbwatch.py` (sonda) + `Monitor.refresh_db/check_db` en `tools/monitor/monitor.py`.
Solo lee. La ventana en vivo (`monitor.py` sin `--headless`) no sondea; lo hace solo el servicio.

## Qué se guarda

`tools/monitor/db_snapshots/AAAA-MM-DD.jsonl` (una línea por sonda, 7 días de retención, fuera de git).
Nunca se guardan valores de parámetros: el texto de las consultas se normaliza (literales y números → `?`) y se recorta.

Ejemplo de foto (una línea; aquí en varias para leerla):

```json
{"ts": "2026-10-01T09:35:02", "ok": true, "latency_s": 0.84,
 "load1": 3.4, "load5": 2.9, "cores": 2, "mem_avail_pct": 9.7, "cpu_pct": 88.2, "io_busy_pct": 71.0, "backends": 52,
 "sql": {"enabled": true, "trivial_s": 14.6,
   "top": [{"query": "select * from conversations where tenant_id = ? order by updated_at desc", "calls": 1840,
            "total_ms": 912000.0, "delta_ms": 61200.0, "delta_is_total": false}],
   "by_state": {"active": 11, "idle": 38, "idle in transaction": 3},
   "by_app": {"oscar-hq-api": 41, "supabase_admin": 4, "(sin nombre)": 7},
   "long": [{"pid": 4182, "app": "oscar-hq-api", "secs": 12.4, "query": "select ... where tenant_id = ?"}]}}
```

Sin la parte SQL queda `"sql": {"enabled": false}` y solo hay métricas del endpoint.

## Avisos (mismo mecanismo `condition()` que el resto del monitor)

| Nivel | Condición | Persistencia |
|---|---|---|
| AVISO | memoria disponible < 15 % | 2 sondas (~5-10 min) |
| AVISO | load1 > nº de cores | 3 sondas (~10 min) |
| AVISO | más de 45 conexiones | 2 sondas |
| ALTA | la sonda no responde / no devuelve métricas | 2 sondas seguidas |
| ALTA | consulta trivial (`select 1`) > 5 s (o, sin SQL, la latencia del endpoint) | 2 sondas seguidas |

Las caídas (ALTA) saltan el silencio nocturno 23:00-08:00; los AVISO de noche se resumen a las 08:00.
Al normalizarse llega «✅ Resuelto … (duró ~N min)».

Ejemplo de alerta (DM del bot de Trabajos):

```
🚨 Monitor: La base de Supabase de Oscar HQ está lenta: una consulta trivial tarda 15 s.
👉 Qué hacer: abre el panel de Supabase y reinicia la base (Restart project):
https://supabase.com/dashboard/project/kkkxptlwknyafbavajhj/settings/general.
Mayores consumidores (última foto buena): 1) select * from conversations where tenant_id = ? order by updated_at desc
(61.2 s, 1840 llamadas) | 2) … | 3) …
```

Importante: **mira los mayores consumidores antes de reiniciar**: el reinicio borra `pg_stat_statements`, pero la
última foto buena ya está guardada en `db_snapshots/`.

## Credenciales (solo por nombre, en `agent-lanes/.env` o `tools/monitor/.env`; nunca se imprimen)

| Variable | Para qué | Obligatoria |
|---|---|---|
| `SUPABASE_SERVICE_ROLE_KEY` | Basic `service_role` contra el endpoint de métricas | sí (sin ella toda la vigilancia queda desactivada y el monitor lo dice al arrancar) |
| `SUPABASE_PROJECT_REF` | ref del proyecto (por defecto `kkkxptlwknyafbavajhj`) | no |
| `SUPABASE_MONITOR_RO_DSN` | URI Postgres del rol `monitor_ro` (parte SQL) | no: sin ella solo hay métricas |

La parte SQL además necesita `psycopg` (v3) o `psycopg2` instalado; si falta, queda desactivada sin bloquear el resto.

### Crear el rol `monitor_ro` (una vez, en el SQL Editor de Supabase)

```sql
create role monitor_ro login password '<contraseña-larga-generada>';
grant pg_read_all_stats to monitor_ro;                    -- pg_stat_statements y pg_stat_activity de todos
alter role monitor_ro set default_transaction_read_only = on;
alter role monitor_ro set statement_timeout = '8s';
alter role monitor_ro set search_path = public, extensions; -- pg_stat_statements vive en `extensions`
alter role monitor_ro connection limit 2;
```

DSN: `postgresql://monitor_ro:<contraseña>@db.<ref>.supabase.co:5432/postgres` (o la del pooler en modo sesión), a
`SUPABASE_MONITOR_RO_DSN`. Ocupa 1 conexión unos segundos cada 5 min.
