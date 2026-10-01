"""Monitor (01-10): sonda de la base de Supabase de Oscar HQ. Todo con fakes: sin red, sin base real."""
import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("psutil")
pytest.importorskip("rich")

MONDIR = Path(__file__).resolve().parents[2] / "monitor"
SECRET = "SECRETO-NO-DEBE-SALIR"


@pytest.fixture
def mods(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("monitor_db_under_test", MONDIR / "monitor.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE", tmp_path)
    monkeypatch.setattr(mod, "ALERTS_LOG", tmp_path / "alerts.log")
    return mod, mod.dbwatch


def prom(load=0.5, mem_total=1000, mem_avail=600, backends=20, cpu_idle=100.0, cpu_busy=10.0, io=1.0):
    return "\n".join([
        "# HELP node_load1 x",
        f"node_load1 {load}", f"node_load5 {load}",
        f"node_memory_MemTotal_bytes {mem_total}", f"node_memory_MemAvailable_bytes {mem_avail}",
        f'node_cpu_seconds_total{{cpu="0",mode="idle"}} {cpu_idle}',
        f'node_cpu_seconds_total{{cpu="1",mode="idle"}} {cpu_idle}',
        f'node_cpu_seconds_total{{cpu="0",mode="user"}} {cpu_busy}',
        f'node_disk_io_time_seconds_total{{device="nvme0n1"}} {io}',
        f'pg_stat_database_numbackends{{datname="postgres"}} {backends}',
    ])


def make_watch(dbw, tmp_path, *, text=None, dsn="", sql=None, clock=None):
    box = {"text": text if text is not None else prom()}

    def http(url, key):
        assert "customer/v1/privileged/metrics" in url
        if isinstance(box["text"], Exception):
            raise box["text"]
        return box["text"]

    w = dbw.DbWatch(ref="abc", key=SECRET, dsn=dsn, snap_dir=tmp_path / "snaps", http=http, sql=sql,
                    clock=clock or (lambda: datetime(2026, 10, 1, 10, 0).timestamp()))
    return w, box


def test_probe_extracts_metrics_without_sql(mods, tmp_path):
    _, dbw = mods
    w, _ = make_watch(dbw, tmp_path, text=prom(load=1.5, mem_avail=120, backends=33))
    s = w.probe()
    assert s["ok"] and s["load1"] == 1.5 and s["cores"] == 2 and s["mem_avail_pct"] == 12.0
    assert s["backends"] == 33 and s["sql"] == {"enabled": False}


def test_cpu_and_io_use_delta_between_probes(mods, tmp_path):
    _, dbw = mods
    t = [1000.0]
    w, box = make_watch(dbw, tmp_path, text=prom(cpu_idle=100, cpu_busy=0, io=0), clock=lambda: t[0])
    assert "cpu_pct" not in w.probe()
    t[0] += 300
    box["text"] = prom(cpu_idle=300, cpu_busy=100, io=150)  # +400 idle (2 cpus), +100 busy
    s = w.probe()
    assert s["cpu_pct"] == 20.0 and s["io_busy_pct"] == 50.0


def test_probe_failure_and_garbage(mods, tmp_path):
    _, dbw = mods
    w, box = make_watch(dbw, tmp_path, text=TimeoutError("timed out"))
    s = w.probe()
    assert not s["ok"] and "TimeoutError" in s["error"]
    box["text"] = "<html>no es prometheus</html>"
    assert not w.probe()["ok"]


def test_sql_part_top_delta_activity_and_no_parameter_values(mods, tmp_path):
    _, dbw = mods
    stats = {"rows": [("1", "select * from users where email = 'a@b.com' and id = 42", 10, 5000.0),
                      ("2", "select count(*) from big", 3, 9000.0)]}

    def sql(dsn, q):
        if q == "select 1":
            return [(1,)]
        if "pg_stat_statements" in q:
            return stats["rows"]
        if "group by" in q:
            return [("active", "api", 3), ("idle", "api", 10), ("idle", "", 2)]
        return [(7, "api", 12.4, "update t set x = 'secreto' where id = 99")]

    w, _ = make_watch(dbw, tmp_path, dsn="postgres://x", sql=sql)
    first = w.probe()["sql"]
    assert first["top"][0]["query"] == "select count(*) from big" and first["top"][0]["delta_is_total"]
    assert first["by_state"] == {"active": 3, "idle": 12} and first["by_app"]["(sin nombre)"] == 2
    assert first["by_app"]["api"] == 13
    assert first["long"] == [{"pid": 7, "app": "api", "secs": 12.4, "query": "update t set x = ? where id = ?"}]
    blob = json.dumps(first)
    assert "a@b.com" not in blob and "secreto" not in blob and "42" not in blob
    # segunda foto: solo cuenta lo nuevo (la consulta 1 sube 1000 ms, la 2 no se mueve)
    stats["rows"] = [("1", stats["rows"][0][1], 11, 6000.0), ("2", "select count(*) from big", 3, 9000.0)]
    second = w.probe()["sql"]["top"]
    assert [t["delta_ms"] for t in second] == [1000.0] and not second[0]["delta_is_total"]
    # reinicio de pg_stat_statements: contador menor -> se toma el valor actual
    stats["rows"] = [("1", stats["rows"][0][1], 1, 200.0)]
    assert w.probe()["sql"]["top"][0]["delta_ms"] == 200.0


def test_sql_failure_counts_as_slow_and_does_not_break_metrics(mods, tmp_path):
    _, dbw = mods

    def sql(dsn, q):
        raise TimeoutError("canceling statement due to statement timeout")

    w, _ = make_watch(dbw, tmp_path, dsn="postgres://x", sql=sql)
    s = w.probe()
    assert s["ok"] and "trivial_s" in s["sql"] and "TimeoutError" in s["sql"]["error"]


def test_save_retention_7_days_and_last_good_top(mods, tmp_path):
    _, dbw = mods
    w, _ = make_watch(dbw, tmp_path)
    d = tmp_path / "snaps"
    d.mkdir()
    old = (datetime(2026, 10, 1) - timedelta(days=9)).strftime("%Y-%m-%d")
    recent = (datetime(2026, 10, 1) - timedelta(days=3)).strftime("%Y-%m-%d")
    (d / f"{old}.jsonl").write_text("{}\n")
    (d / f"{recent}.jsonl").write_text("{}\n")
    top = [{"query": "q", "calls": 1, "delta_ms": 1.0}]
    w.save({"ts": "2026-10-01T10:00:00", "ok": True, "sql": {"top": top}})
    assert not (d / f"{old}.jsonl").exists() and (d / f"{recent}.jsonl").exists()
    assert json.loads((d / "2026-10-01.jsonl").read_text().splitlines()[0])["sql"]["top"] == top
    assert w.last_good_top == top
    w.save({"ts": "2026-10-01T10:05:00", "ok": False, "sql": {"enabled": True}})  # foto mala no pisa la buena
    assert w.last_good_top == top
    # un watcher nuevo (reinicio del monitor) recupera la última foto buena del disco
    w2, _ = make_watch(dbw, tmp_path)
    assert w2.last_good_top == top


def test_read_env_by_name_only(mods, tmp_path, monkeypatch):
    _, dbw = mods
    env = tmp_path / ".env"
    env.write_text(f"OTRA=1\nSUPABASE_SERVICE_ROLE_KEY={SECRET}\n", encoding="utf-8")
    monkeypatch.delenv("SUPABASE_MONITOR_RO_DSN", raising=False)
    got = dbw.read_env((dbw.ENV_KEY, dbw.ENV_DSN), [env])
    assert got == {dbw.ENV_KEY: SECRET}
    monkeypatch.setattr(dbw, "read_env", lambda *a, **k: {})
    assert dbw.from_env([env]) is None  # sin clave: vigilancia desactivada


def monitor_with(mods, tmp_path, **kw):
    mon, dbw = mods
    pushed = []
    w, box = make_watch(dbw, tmp_path, **kw)
    m = mon.Monitor(write_log=True, push=pushed.append, db=w)
    m.now = lambda: datetime(2026, 10, 1, 12, 0)
    return m, w, box, pushed


def test_probe_dead_twice_opens_alta_with_what_to_do_and_resolves(mods, tmp_path):
    m, w, box, pushed = monitor_with(mods, tmp_path)
    w.last_good_top = [{"query": "select pesada ?", "calls": 5, "delta_ms": 8000.0}]
    box["text"] = TimeoutError("timed out")
    m.refresh_db(force=True)
    assert not pushed  # una sola lectura: aún no
    m.refresh_db(force=True)
    assert len(pushed) == 1
    msg = pushed[0]
    assert "no responde a la sonda" in msg and "supabase.com/dashboard/project/abc/settings/general" in msg
    assert "👉 Qué hacer" in msg and "select pesada ?" in msg and SECRET not in msg
    box["text"] = prom()
    m.refresh_db(force=True)
    assert "Resuelto" in pushed[-1]


def test_slow_trivial_query_twice_is_alta_but_once_is_not(mods, tmp_path):
    mon, dbw = mods
    m, w, box, pushed = monitor_with(mods, tmp_path, dsn="postgres://x")
    slow = {"v": 16.0}
    w._probe_sql = lambda now: {"enabled": True, "trivial_s": slow["v"]}
    m.refresh_db(force=True)
    assert not pushed
    slow["v"] = 0.1  # se recupera: la racha se corta
    m.refresh_db(force=True)
    slow["v"] = 14.0
    m.refresh_db(force=True)
    assert not pushed
    m.refresh_db(force=True)
    assert len(pushed) == 1 and "una consulta trivial tarda 14 s" in pushed[0]


def test_aviso_memory_connections_and_load_10_min(mods, tmp_path):
    m, w, box, pushed = monitor_with(mods, tmp_path, dsn="")
    box["text"] = prom(mem_avail=100, backends=50, load=3.0)  # 10 % de memoria, 50 conexiones, load 3 > 2 cores
    m.refresh_db(force=True)
    assert not pushed
    m.refresh_db(force=True)  # memoria y conexiones: 2 lecturas; load aún no (10 min = 3 sondas)
    assert len(pushed) == 2 and any("10.0% de memoria" in p for p in pushed) and any("50 conexiones" in p for p in pushed)
    m.refresh_db(force=True)
    assert len(pushed) == 3 and "carga 3.0 con 2 núcleos" in pushed[-1]
    log = (mods[0].ALERTS_LOG).read_text(encoding="utf-8")
    assert "AVISO" in log and "ALTA" not in log.replace("ALTA/", "")


def test_healthy_probe_is_silent_and_disabled_without_db(mods, tmp_path):
    m, w, box, pushed = monitor_with(mods, tmp_path)
    for _ in range(4):
        m.refresh_db(force=True)
    assert not pushed
    assert len(list((tmp_path / "snaps").glob("*.jsonl"))) == 1
    mon, _ = mods
    off = mon.Monitor(write_log=True, push=pushed.append)  # sin db
    assert off.refresh_db(force=True) is None and not pushed


def test_db_outage_alerts_pass_the_night_silence(mods, tmp_path):
    m, w, box, pushed = monitor_with(mods, tmp_path)
    m.now = lambda: datetime(2026, 10, 1, 3, 0)
    box["text"] = TimeoutError("x")
    m.refresh_db(force=True)
    m.refresh_db(force=True)
    assert len(pushed) == 1  # la caída de la base no espera a las 08:00
