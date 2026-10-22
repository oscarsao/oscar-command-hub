"""W5 (t_e2b5abde): alertas agrupadas (1/día por clave + /silenciar), reinicio del monitor tras un merge, back-merge
automático y retro semanal. Todo con dobles: sin red, sin Telegram, sin hermes ni git reales."""
import importlib.util
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_lanes import backmerge, retro, silencios
from agent_lanes.commands import parse_command

pytest.importorskip("psutil")
pytest.importorskip("rich")

MON = Path(__file__).resolve().parents[2] / "monitor" / "monitor.py"


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("monitor_w5", MON)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE", tmp_path)
    monkeypatch.setattr(mod, "ALERTS_LOG", tmp_path / "alerts.log")
    monkeypatch.setattr(mod, "LANES", tmp_path / "lanes")  # sin ajustes_hub.json: rige el valor por defecto
    return mod


def make(monitor):
    pushed = []
    m = monitor.Monitor(write_log=True, push=pushed.append)
    m.now = lambda: datetime(2026, 10, 26, 12, 0)
    return m, pushed


# --- 1. como mucho un aviso al día por clave -----------------------------------------------------------------------

def test_same_key_alerts_once_a_day_even_if_every_is_short(monitor, tmp_path):
    m, pushed = make(monitor)
    for _ in range(5):
        m.alert("ALTA", "algo roto", "k1", every=60)
    assert pushed == ["algo roto"]
    assert (tmp_path / "alerts.log").read_text(encoding="utf-8").count("algo roto") == 1
    m._seen["k1"] -= 25 * 3600  # pasó más de un día
    m.alert("ALTA", "algo roto", "k1", every=60)
    assert len(pushed) == 2


def test_cap_survives_a_monitor_restart(monitor):
    m, pushed = make(monitor)
    m.alert("ALTA", "algo roto", "k1")
    m2, pushed2 = make(monitor)  # reinicio: lee alert_daily.json
    m2.alert("ALTA", "algo roto", "k1")
    assert pushed == ["algo roto"] and pushed2 == []


def test_cap_is_editable_from_ajustes_hub(monitor, tmp_path):
    (tmp_path / "lanes").mkdir()
    (tmp_path / "lanes" / "ajustes_hub.json").write_text(
        json.dumps({"valores": {"avisos.alerta_cada_horas": 1}}), encoding="utf-8")
    m, pushed = make(monitor)
    m.alert("ALTA", "x", "k1", every=60)
    m._seen["k1"] -= 2 * 3600
    m.alert("ALTA", "x", "k1", every=60)
    assert len(pushed) == 2


def test_resolved_and_every_zero_are_not_capped(monitor):
    m, pushed = make(monitor)
    m.alert("RESUELTO", "ok", "k-ok", every=0)
    m.alert("RESUELTO", "ok", "k-ok", every=0)
    assert pushed == ["ok", "ok"]


def test_silenciar_mutes_key_and_prefixed_keys_until_expiry(monitor, tmp_path):
    m, pushed = make(monitor)
    silencios.silenciar("drift", 2, tmp_path)
    m.alert("ALTA", "develop atrasado", "drift-migrateam")
    assert pushed == []
    silencios.silenciar("drift", 0, tmp_path)  # 0 h = quitar el silencio
    m.alert("ALTA", "develop atrasado", "drift-migrateam")
    assert pushed == ["develop atrasado"]


def test_silencios_module_validation_and_listing(tmp_path):
    with pytest.raises(ValueError):
        silencios.silenciar("dos palabras", 1, tmp_path)
    with pytest.raises(ValueError):
        silencios.silenciar("ram", 10_000, tmp_path)
    silencios.silenciar("RAM", 1, tmp_path, now=1000)
    assert silencios.activos(tmp_path, now=1000) == {"ram": 1000 + 3600}
    assert silencios.activos(tmp_path, now=1000 + 7200) == {}
    (tmp_path / "alert_daily.json").write_text(json.dumps({"a": 1, "b": 2}), encoding="utf-8")
    assert silencios.claves_recientes(tmp_path) == ["b", "a"]


def test_silenciar_is_a_known_command():
    assert parse_command("/silenciar ram 12") == ("silenciar", "ram 12")


# --- 3. reinicio ordenado del monitor ------------------------------------------------------------------------------

def _fake_code(tmp_path, monitor):
    base = tmp_path / "code"
    base.mkdir()
    for n in monitor.WATCHED_CODE:
        (base / n).write_text("x = 1\n", encoding="utf-8")
    return base


def test_monitor_restarts_when_its_code_changes(monitor, tmp_path):
    base = _fake_code(tmp_path, monitor)
    started = monitor.code_fingerprint(base)
    spawned = []
    ok = lambda args: SimpleNamespace(returncode=0, stderr="")
    assert monitor.restart_if_code_changed(started, base=base, state=tmp_path, run=ok, spawn=spawned.append) is False
    (base / "monitor.py").write_text("x = 22\n", encoding="utf-8")
    (tmp_path / "headless.pid").write_text("123")
    assert monitor.restart_if_code_changed(started, base=base, state=tmp_path, run=ok, spawn=spawned.append) is True
    assert spawned and spawned[0][-1] == "--headless" and spawned[0][-2].endswith("monitor.py")
    assert not (tmp_path / "headless.pid").exists()  # si no, el relevo creería que ya hay uno vivo


def test_monitor_does_not_restart_into_broken_code(monitor, tmp_path):
    base = _fake_code(tmp_path, monitor)
    started = monitor.code_fingerprint(base)
    (base / "dbwatch.py").write_text("def (:\n", encoding="utf-8")
    alerts, spawned = [], []
    bad = lambda args: SimpleNamespace(returncode=1, stderr="SyntaxError")
    assert monitor.restart_if_code_changed(started, base=base, state=tmp_path, run=bad, spawn=spawned.append,
                                           alert=lambda *a: alerts.append(a)) is False
    assert spawned == [] and alerts and "no compila" in alerts[0][1]


# --- 2. back-merge ------------------------------------------------------------------------------------------------

class FakeIntegrator:
    def __init__(self, tmp_path, behind=3):
        self.lanes = {"claude-migrateam": SimpleNamespace(name="claude-migrateam", board="migrateam", repo="R",
                                                          remote="origin", base="develop")}
        self.dry_run, self.t, self.behind, self.cards, self.states = False, 10_000.0, behind, [], {}
        self.git_calls = []

    def _now(self):
        return self.t

    def _state(self, tid):
        return dict(self.states.get(tid, {}))

    def _save(self, tid, **data):
        self.states[tid] = {**self.states.get(tid, {}), **data}

    def _git(self, repo, *args, timeout=300):
        self.git_calls.append(args)
        out = {"fetch": "", "rev-list": f"{self.behind}\n", "rev-parse": "abcdef1234567890\n"}[args[0]]
        return subprocess.CompletedProcess(args, 0, out, "")

    def hermes_for(self, board):
        outer = self
        return SimpleNamespace(create=lambda title, body, assignee, **kw: outer.cards.append((title, body, assignee, kw))
                               or "t_00000001")


@pytest.fixture
def bm_config(tmp_path, monkeypatch):
    cfg = tmp_path / "backmerge.yaml"
    cfg.write_text("cada_minutos: 30\ncarriles:\n  claude-migrateam: {desde: master, hacia: develop}\n", encoding="utf-8")
    monkeypatch.setattr(backmerge, "CONFIG_PATH", cfg)
    return cfg


def test_backmerge_creates_one_card_per_episode(tmp_path, bm_config):
    i = FakeIntegrator(tmp_path)
    assert backmerge.check(i) == ["t_00000001"]
    title, body, assignee, kw = i.cards[0]
    assert "master → develop" in title and assignee == "claude-migrateam"
    assert "origin/develop" in body and kw["key"].startswith("backmerge-claude-migrateam-abcdef")
    i.t += 3600
    assert backmerge.check(i) == []  # sigue por detrás: no se repite
    i.behind = 0
    i.t += 3600
    backmerge.check(i)  # se cierra el episodio
    i.behind, i.t = 2, i.t + 3600
    assert backmerge.check(i) == ["t_00000001"] and len(i.cards) == 2  # otro hotfix: otra tarjeta


def test_backmerge_throttles_checks_and_skips_dry_run(tmp_path, bm_config):
    i = FakeIntegrator(tmp_path)
    backmerge.check(i)
    n = len(i.git_calls)
    i.t += 60
    backmerge.check(i)
    assert len(i.git_calls) == n  # dentro de cada_minutos no vuelve a hacer fetch
    j = FakeIntegrator(tmp_path)
    j.dry_run = True
    assert backmerge.check(j) == [] and j.git_calls == []


def test_backmerge_missing_config_is_off(tmp_path, monkeypatch):
    monkeypatch.setattr(backmerge, "CONFIG_PATH", tmp_path / "nope.yaml")
    assert backmerge.check(FakeIntegrator(tmp_path)) == []


# --- 4. retro semanal ---------------------------------------------------------------------------------------------

MONDAY_10H = datetime(2026, 10, 26, 10, 0).timestamp()


def test_due_only_monday_after_hour_once_per_week():
    assert retro.due(MONDAY_10H, 9, None)
    assert not retro.due(MONDAY_10H, 11, None)
    assert not retro.due(datetime(2026, 10, 27, 10, 0).timestamp(), 9, None)
    assert not retro.due(MONDAY_10H, 9, "2026-W44")


def test_alertas_groups_by_text_and_ignores_old_and_resolved(tmp_path):
    log = tmp_path / "alerts.log"
    log.write_text("\n".join([
        "2026-10-25 10:00:00 MEDIA RAM al 95% (15/16 GB)",
        "2026-10-25 11:00:00 MEDIA RAM al 97% (15/16 GB)",
        "2026-10-24 11:00:00 RESUELTO ✅ Resuelto: algo",
        "2026-09-01 11:00:00 MEDIA RAM al 90% (15/16 GB)",
        "basura"]), encoding="utf-8")
    al = retro.alertas(log, MONDAY_10H)
    assert al == {"[MEDIA] RAM al N% (N/N GB)": 2}


def test_analizar_and_render():
    now = MONDAY_10H
    tasks = {"blocked": [{"title": "a", "block_reason": "guard bloqueó X"}, {"title": "b", "block_reason": "guard bloqueó X"}],
             "triage": [{"created_at": now - 7200}],
             "done": [{"created_at": now - 4 * 3600, "completed_at": now - 3600},
                      {"created_at": now - 90 * 86400, "completed_at": now - 80 * 86400}]}
    k = retro.analizar(tasks, now)
    assert (k["bloqueadas"], k["guard"], k["triage"], k["hechas"]) == (2, 2, 1, 1)
    assert k["repetidas"] == [("guard bloqueó X", 2)] and k["mediana_h"] == pytest.approx(3.0)
    text = retro.render(now, retro.Counter({"[MEDIA] x": 4}), k)
    assert "# Retro semanal · 2026-10-26" in text and "4× [MEDIA] x" in text and "3.0 h" in text


def test_check_writes_report_and_sends_dm_once(tmp_path):
    sent = []
    i = FakeIntegrator(tmp_path)
    i.t = MONDAY_10H
    i.notifier = SimpleNamespace(send_to=lambda *a, **kw: sent.append(a))
    i.desk = SimpleNamespace(owner_id="42")
    i.hermes_for = lambda board: SimpleNamespace(list_status_all=lambda s: [])
    log = tmp_path / "alerts.log"
    log.write_text("2026-10-25 10:00:00 MEDIA RAM al 95%\n", encoding="utf-8")
    text = retro.check(i, out_dir=tmp_path / "retro", alerts_log=log)
    assert text and "Retro semanal" in text and sent[0][0] == "42"
    assert (tmp_path / "retro" / "2026-10-26.md").read_text(encoding="utf-8").startswith("# Retro semanal")
    assert retro.check(i, out_dir=tmp_path / "retro", alerts_log=log) is None  # ya enviada esta semana
    assert len(sent) == 1
