"""Sonda de la base de Supabase de Oscar HQ (01-10: se saturó 30-09 y 01-10 y el reinicio borró pg_stat_statements).

Cada sonda (la lanza el monitor headless cada 5 min) SOLO LEE:
  1. el endpoint de métricas  https://<ref>.supabase.co/customer/v1/privileged/metrics  (Basic, service_role):
     load, memoria disponible, CPU, IO y nº de backends;
  2. si hay DSN de solo lectura (rol `monitor_ro` con `pg_read_all_stats`): top 10 de pg_stat_statements por
     total_exec_time (delta con la foto anterior), pg_stat_activity por estado y por application_name, y las consultas
     activas de más de 5 s. Sin DSN (o sin psycopg) esa parte queda desactivada y el resto sigue funcionando.

Cada foto se guarda en db_snapshots/AAAA-MM-DD.jsonl (7 días de retención). Nunca se guardan valores de parámetros:
el texto de las consultas se normaliza (literales y números -> ?) y se recorta.
Las credenciales se leen del entorno / .env por nombre y nunca se imprimen ni se guardan.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_REF = "kkkxptlwknyafbavajhj"
SNAP_DIR = Path(__file__).resolve().parent / "db_snapshots"
RETENTION_DAYS = 7
SLOW_S = 5.0          # una consulta trivial que tarda más de esto cuenta como lenta
LONG_QUERY_S = 5.0    # consultas activas más largas que esto se registran
HTTP_TIMEOUT = 10
ENV_KEY, ENV_DSN, ENV_REF = "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_MONITOR_RO_DSN", "SUPABASE_PROJECT_REF"

_LABEL_RE = re.compile(r'(\w+)="([^"]*)"')
_STR_RE = re.compile(r"'(?:[^']|'')*'")
_NUM_RE = re.compile(r"\b\d+(?:\.\d+)?\b")

STATEMENTS_SQL = ("select queryid::text, query, calls, total_exec_time from pg_stat_statements "
                  "order by total_exec_time desc limit 200")
STATE_SQL = ("select coalesce(state, 'sin estado'), coalesce(nullif(application_name, ''), '(sin nombre)'), count(*) "
             "from pg_stat_activity where datname = current_database() group by 1, 2")
LONG_SQL = ("select pid, coalesce(nullif(application_name, ''), '(sin nombre)'), "
            "extract(epoch from now() - query_start)::float, left(query, 400) from pg_stat_activity "
            f"where state = 'active' and pid <> pg_backend_pid() and now() - query_start > interval '{int(LONG_QUERY_S)} seconds' "
            "order by query_start limit 10")


def normalize_query(text: str | None, limit: int = 200) -> str:
    """Texto sin valores: strings y números -> ?, espacios colapsados, recortado."""
    t = _NUM_RE.sub("?", _STR_RE.sub("?", text or ""))
    return " ".join(t.split())[:limit]


def read_env(names: tuple[str, ...], files: list[Path]) -> dict[str, str]:
    """Solo las variables pedidas, de os.environ y de los .env dados (el entorno manda). Nunca imprime valores."""
    import os
    found: dict[str, str] = {}
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            k, sep, v = ln.partition("=")
            if sep and k.strip() in names and v.strip():
                found[k.strip()] = v.strip().strip('"').strip("'")
    for n in names:
        if os.environ.get(n):
            found[n] = os.environ[n]
    return found


def parse_prom(text: str) -> list[tuple[str, dict[str, str], float]]:
    out = []
    for ln in text.splitlines():
        if not ln or ln.startswith("#"):
            continue
        head, _, val = ln.rpartition(" ")
        name, _, rest = head.partition("{")
        try:
            out.append((name.strip(), dict(_LABEL_RE.findall(rest)), float(val)))
        except ValueError:
            continue
    return out


class DbWatch:
    def __init__(self, *, ref: str = DEFAULT_REF, key: str = "", dsn: str = "", snap_dir: Path | None = None,
                 http=None, sql=None, clock=time.time):
        """`http(url, key) -> texto` y `sql(dsn, consulta) -> filas` son inyectables en tests."""
        self.ref, self.key, self.dsn = ref, key, dsn
        self.snap_dir = snap_dir or SNAP_DIR
        self.http, self.sql, self.clock = http or self._http, sql or self._sql, clock
        self._prev_cpu: tuple[float, float, float, float] | None = None  # (t, idle, total, io_time)
        self._prev_stats: dict[str, float] | None = None
        self.last_good_top: list[dict] = self._load_last_top()

    @property
    def metrics_url(self) -> str:
        return f"https://{self.ref}.supabase.co/customer/v1/privileged/metrics"

    @property
    def dashboard_url(self) -> str:
        return f"https://supabase.com/dashboard/project/{self.ref}/settings/general"

    # --- E/S reales (sustituidas por fakes en tests) ---------------------------------------------------
    def _http(self, url: str, key: str) -> str:
        auth = base64.b64encode(f"service_role:{key}".encode()).decode()
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}", "User-Agent": "oscar-monitor"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")

    def _sql(self, dsn: str, query: str) -> list[tuple]:
        try:
            import psycopg
            conn = psycopg.connect(dsn, connect_timeout=8, autocommit=True,
                                   options="-c default_transaction_read_only=on -c statement_timeout=8000")
        except ImportError:
            import psycopg2
            conn = psycopg2.connect(dsn, connect_timeout=8, options="-c default_transaction_read_only=on "
                                                                    "-c statement_timeout=8000")
            conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute(query)
            return cur.fetchall() if cur.description else []
        finally:
            conn.close()

    # --- sonda ------------------------------------------------------------------------------------------
    def probe(self) -> dict:
        """Una foto. Nunca lanza: los fallos quedan en `ok`/`error`."""
        now = self.clock()
        snap: dict = {"ts": datetime.fromtimestamp(now).isoformat(timespec="seconds"), "ok": False}
        t0 = time.monotonic()
        try:
            rows = parse_prom(self.http(self.metrics_url, self.key))
            snap["latency_s"] = round(time.monotonic() - t0, 2)
            self._fill_metrics(snap, rows, now)
            snap["ok"] = "load1" in snap or "mem_avail_pct" in snap
            if not snap["ok"]:
                snap["error"] = "respuesta sin métricas reconocibles"
        except Exception as exc:  # el texto de la excepción puede traer la URL pero no la clave (va en cabecera)
            snap["latency_s"] = round(time.monotonic() - t0, 2)
            snap["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        snap["sql"] = self._probe_sql(now) if self.dsn else {"enabled": False}
        return snap

    def _fill_metrics(self, snap: dict, rows: list, now: float) -> None:
        def total(name, **lab):
            vals = [v for n, ls, v in rows if n == name and all(ls.get(k) == x for k, x in lab.items())]
            return sum(vals) if vals else None

        for key, name in (("load1", "node_load1"), ("load5", "node_load5")):
            v = total(name)
            if v is not None:
                snap[key] = round(v, 2)
        mt, ma = total("node_memory_MemTotal_bytes"), total("node_memory_MemAvailable_bytes")
        if mt and ma is not None:
            snap["mem_avail_pct"] = round(100 * ma / mt, 1)
        cpus = {ls.get("cpu") for n, ls, _ in rows if n == "node_cpu_seconds_total" and ls.get("cpu") is not None}
        if cpus:
            snap["cores"] = len(cpus)
        idle = (total("node_cpu_seconds_total", mode="idle") or 0) + (total("node_cpu_seconds_total", mode="iowait") or 0)
        all_cpu = total("node_cpu_seconds_total")
        io = total("node_disk_io_time_seconds_total")
        if all_cpu is not None and self._prev_cpu:
            pt, p_idle, p_all, p_io = self._prev_cpu
            if all_cpu > p_all:
                snap["cpu_pct"] = round(100 * (1 - (idle - p_idle) / (all_cpu - p_all)), 1)
            if io is not None and now > pt:
                snap["io_busy_pct"] = round(min(100.0, 100 * (io - p_io) / (now - pt)), 1)
        if all_cpu is not None:
            self._prev_cpu = (now, idle, all_cpu, io or 0.0)
        backends = total("pg_stat_database_numbackends")
        if backends is None:
            backends = total("pg_stat_activity_count")
        if backends is not None:
            snap["backends"] = int(backends)

    def _probe_sql(self, now: float) -> dict:
        out: dict = {"enabled": True}
        t0 = time.monotonic()
        try:
            self.sql(self.dsn, "select 1")
            out["trivial_s"] = round(time.monotonic() - t0, 2)
        except Exception as exc:
            out["trivial_s"] = round(time.monotonic() - t0, 2)  # conectar/responder lento o con timeout = lento
            out["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            return out
        try:
            out["top"] = self._top_statements(self.sql(self.dsn, STATEMENTS_SQL))
            out["by_state"], out["by_app"] = {}, {}
            for state, app, n in self.sql(self.dsn, STATE_SQL):
                app = app or "(sin nombre)"
                out["by_state"][state] = out["by_state"].get(state, 0) + int(n)
                out["by_app"][app] = out["by_app"].get(app, 0) + int(n)
            out["long"] = [{"pid": int(pid), "app": app, "secs": round(float(secs), 1), "query": normalize_query(q)}
                           for pid, app, secs, q in self.sql(self.dsn, LONG_SQL)]
        except Exception as exc:
            out["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        return out

    def _top_statements(self, rows: list[tuple]) -> list[dict]:
        """Top 10 por tiempo total EN EL INTERVALO (delta con la foto anterior; sin foto previa, el acumulado)."""
        cur = {str(qid): (q, int(calls), float(ms)) for qid, q, calls, ms in rows}
        prev = self._prev_stats
        self._prev_stats = {qid: ms for qid, (_, _, ms) in cur.items()}
        items = []
        for qid, (q, calls, ms) in cur.items():
            before = prev.get(qid, 0.0) if prev is not None else 0.0
            delta = ms - before if ms >= before else ms  # reinicio/reset de pg_stat_statements
            if delta > 0:
                items.append({"query": normalize_query(q), "calls": calls, "total_ms": round(ms, 1),
                              "delta_ms": round(delta, 1), "delta_is_total": prev is None or qid not in prev})
        items.sort(key=lambda i: i["delta_ms"], reverse=True)
        return items[:10]

    # --- histórico --------------------------------------------------------------------------------------
    def save(self, snap: dict) -> None:
        try:
            self.snap_dir.mkdir(parents=True, exist_ok=True)
            day = snap["ts"][:10]
            with (self.snap_dir / f"{day}.jsonl").open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(snap, ensure_ascii=False) + "\n")
            cutoff = (datetime.fromtimestamp(self.clock()) - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
            for old in self.snap_dir.glob("*.jsonl"):
                if old.stem < cutoff:
                    old.unlink(missing_ok=True)
        except OSError:
            pass  # sin disco no se pierde el aviso
        top = (snap.get("sql") or {}).get("top")
        if snap.get("ok") and top:
            self.last_good_top = top

    def _load_last_top(self) -> list[dict]:
        try:
            for f in sorted(self.snap_dir.glob("*.jsonl"), reverse=True):
                for ln in reversed(f.read_text(encoding="utf-8").splitlines()):
                    top = (json.loads(ln).get("sql") or {}).get("top")
                    if top:
                        return top
        except (OSError, ValueError):
            pass
        return []

    def fix_text(self) -> str:
        """El «👉 Qué hacer»: panel para reiniciar y los 3 mayores consumidores de la última foto buena."""
        txt = f"abre el panel de Supabase y reinicia la base (Restart project): {self.dashboard_url}"
        if self.last_good_top:
            txt += ". Mayores consumidores (última foto buena): " + " | ".join(
                f"{i}) {t['query'][:90]} ({t['delta_ms'] / 1000:.1f} s, {t['calls']} llamadas)"
                for i, t in enumerate(self.last_good_top[:3], 1))
        else:
            txt += ". Sin datos de consultas (falta el rol monitor_ro: ver docs/monitor-db-supabase.md)."
        return txt


def from_env(files: list[Path]) -> DbWatch | None:
    """None si no hay clave de servicio (la vigilancia queda desactivada sin bloquear el resto del monitor)."""
    env = read_env((ENV_KEY, ENV_DSN, ENV_REF), files)
    if not env.get(ENV_KEY):
        return None
    return DbWatch(ref=env.get(ENV_REF, DEFAULT_REF), key=env[ENV_KEY], dsn=env.get(ENV_DSN, ""))
