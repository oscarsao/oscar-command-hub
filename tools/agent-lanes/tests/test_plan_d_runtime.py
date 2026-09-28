"""Plan D (28-09): /salud, 🧊 atascadas en triage, línea de tarjetas sin etiqueta, candado de repos y reinicio
ordenado (drain). Todo con fakes: ni Telegram, ni Hermes, ni schtasks, ni git reales."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_lanes import drain, health
from agent_lanes.commands import panel_url
from agent_lanes.config import Lane, load_lanes
from agent_lanes.git_ops import github_slug_problem
from agent_lanes.hermes import OSCAR_AUTHOR, HermesCLI
from agent_lanes.reminders import Reminders, reminder_text
from agent_lanes.runner import LaneRunner
from agent_lanes.status import lane_rows, runner_line
from tests.test_bandeja_unica import card
from tests.test_commands import (GESTION, OSCAR, Q_REC, KanbanHermes, center, command, needs, press, texts)
from tests.test_runner_state import (LANE, FakeGit, FakeHermes, FakeNotifier, FakeVerifier, FakeWorker, ok_outcome)


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


# --- /salud ---------------------------------------------------------------------------------------------

NOW = 1_790_000_000.0


def _iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def gw(tmp_path, **over):
    data = {"pid": 23868, "gateway_state": "running", "active_agents": 1, "updated_at": _iso(NOW - 120),
            "platforms": {"telegram": {"state": "connected"}}, **over}
    p = tmp_path / "gateway_state.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_gateway_line_ok_needs_live_pid_and_fresh_update(tmp_path):
    line = health.gateway_line(gw(tmp_path), lambda pid: pid == 23868, NOW)
    assert line.startswith("✅ running · pid 23868 vivo · 1 agentes activos · actualizado hace 2 min")


@pytest.mark.parametrize("over,alive,expect", [
    ({}, False, "pid 23868 MUERTO"),                          # el fichero dice running pero el proceso murió
    ({"updated_at": _iso(NOW - 7200)}, True, "hace 2 h"),     # sin actualizar
    ({"platforms": {"telegram": {"state": "retrying"}}}, True, "sin conexión: telegram"),
])
def test_gateway_line_warns(tmp_path, over, alive, expect):
    line = health.gateway_line(gw(tmp_path, **over), lambda pid: alive, NOW)
    assert line.startswith("⚠️") and expect in line


def test_gateway_line_without_file(tmp_path):
    assert "no disponible" in health.gateway_line(tmp_path / "nada.json", lambda p: True, NOW)


def test_monitor_lines_services_age_and_last_five_alerts(tmp_path):
    (tmp_path / "health.json").write_text(json.dumps({"Oscar HQ": "✅ 200", "MigraTeam": "❌ 502"}), encoding="utf-8")
    os.utime(tmp_path / "health.json", (NOW - 60, NOW - 60))
    lines = [f"2026-09-28 12:{i:02d}:00 {'ALTA' if i % 2 else 'MEDIA'} alerta {i}" for i in range(8)]
    lines.insert(3, "2026-09-28 12:59:00 BAJA ruido")
    (tmp_path / "alerts.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    svc, alerts = health.monitor_lines(tmp_path, NOW)
    assert svc == "(hace 60 s) Oscar HQ ✅ 200 · MigraTeam ❌ 502"
    assert alerts == [f"28-09 12:{i:02d} {'ALTA' if i % 2 else 'MEDIA'} alerta {i}" for i in range(3, 8)]


def test_monitor_stale_health_is_flagged(tmp_path):
    (tmp_path / "health.json").write_text(json.dumps({"Oscar HQ": "✅ 200"}), encoding="utf-8")
    os.utime(tmp_path / "health.json", (NOW - 7200, NOW - 7200))
    svc, alerts = health.monitor_lines(tmp_path, NOW)
    assert svc.startswith("⚠️ monitor sin actualizar") and alerts == []


def test_migrateam_drift_uses_local_refs_without_fetch():
    calls = []

    def run(args, **kw):
        calls.append(args)
        return cp(0, "3\t1\n")
    text = health.migrateam_drift("C:/mig", run=run)
    assert calls == [["git", "-C", "C:/mig", "rev-list", "--left-right", "--count", "origin/develop...origin/master"]]
    assert text.startswith("⚠️ master tiene 1 commit que develop no · develop lleva 3 sin promocionar")
    assert health.migrateam_drift("C:/mig", run=lambda a, **k: cp(128, "", "fatal")) == "no disponible"


def test_machine_line_with_fake_psutil_and_without_it(monkeypatch):
    fake = SimpleNamespace(virtual_memory=lambda: SimpleNamespace(percent=62.0, used=10 * 1024 ** 3,
                                                                  total=16 * 1024 ** 3),
                           cpu_percent=lambda interval: 14.0)
    assert health.machine_line(fake) == "RAM 62 % (10,0/16,0 GB) · CPU 14 %"

    class Broken:
        def virtual_memory(self):
            raise RuntimeError("x")
    assert health.machine_line(Broken()) == "no disponible"


def test_build_isolates_broken_sections():
    def boom():
        raise RuntimeError("roto")
    text = health.build(runner=lambda: "runner: VIVO (pid 1)", rows=boom, gateway=lambda: "✅ running <x>",
                        monitor=boom, drift=lambda: "master tiene 0", machine=lambda: "RAM 1 %",
                        pending=lambda: (2, 1, 3), now=NOW)
    assert "<b>Carriles</b> · runner: VIVO (pid 1)" in text and "• no disponible" in text
    assert "✅ running &lt;x&gt;" in text  # escapado
    assert "<b>Servicios</b> (monitor) · no disponible" in text
    assert "2 preguntas · 1 atascadas · 3 tarjetas · /decisiones" in text


def test_salud_command_only_reads_local_state(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "HERMES_GATEWAY_STATE", gw(tmp_path))
    monkeypatch.setattr(health, "MONITOR_STATE_DIR", tmp_path / "monitor")
    monkeypatch.setattr(health, "machine_line", lambda: "RAM 50 % · CPU 5 %")
    monkeypatch.setattr(health, "migrateam_drift", lambda repo: f"drift de {repo}")
    import urllib.request

    def no_http(*a, **k):
        raise AssertionError("/salud no hace peticiones HTTP")
    monkeypatch.setattr(urllib.request, "urlopen", no_http)
    cc, desk, bot, h, _ = center(tmp_path, {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_REC)})
    command(desk, "/salud")
    text = bot.sent[-1]["text"]
    assert text.startswith("🩺 <b>Salud</b>")
    assert "claude-migrateam (MigraTeam): libre · 0 en cola · 0 en review" in text
    assert "<b>Hermes</b> · " in text and "drift de C:/mig" in text and "RAM 50 %" in text
    assert "1 preguntas · 0 atascadas · 0 tarjetas · /decisiones" in text


# --- 🧊 atascadas en triage -------------------------------------------------------------------------------

def stuck(tid, reason="verificación mecánica fallida: test_cmd exit 1", kind="transient", at=2000.0,
          assignee="claude-migrateam"):
    return {"task": {"id": tid, "title": f"Tarea {tid}", "body": "## Objetivo\nX\n", "assignee": assignee,
                     "status": "triage", "created_at": 10},
            "runs": [],
            "events": [{"kind": "blocked", "payload": {"kind": kind, "reason": reason}, "created_at": 1000.0},
                       {"kind": "block_loop_detected",
                        "payload": {"kind": kind, "reason": reason, "recurrences": 2, "limit": 2},
                        "created_at": at}],
            "comments": []}


class TriageHermes(KanbanHermes):
    """unblock falla en triage (como Hermes 0.21.4); requeue_triage la devuelve a ready."""

    def unblock(self, tid):
        self.calls.append(("unblock", tid))
        if self.shows[tid]["task"]["status"] != "blocked":
            return False
        self.shows[tid]["task"]["status"] = "ready"
        return True

    def requeue_triage(self, tid, author=OSCAR_AUTHOR):
        self.calls.append(("requeue_triage", tid))
        if self.shows[tid]["task"]["status"] != "triage":
            return None
        self.shows[tid]["task"]["status"] = "ready"
        return "ready"


def triage_center(tmp_path, shows):
    cc, desk, bot, h, messages = center(tmp_path, {})
    th = TriageHermes(shows)
    for obj in (cc, desk, cc.renotifier()):
        obj.hermes_for = lambda b: th
    return cc, desk, bot, th, messages


def test_decisiones_lists_stuck_tasks_with_reason_and_retry(tmp_path):
    shows = {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_REC),
             "t_eeeeeee5": stuck("t_eeeeeee5"),
             "t_fffffff6": stuck("t_fffffff6", reason="El worker necesita decisión:\n- ¿Borro la tabla vieja?",
                                 kind="needs_input", assignee="claude-oscarhq")}
    cc, desk, bot, h, messages = triage_center(tmp_path, shows)
    command(desk, "/decisiones")
    heads = [m["text"] for m in bot.sent]
    i = next(n for n, t in enumerate(heads) if t.startswith("🧊 Atascadas (2)"))
    first, second = bot.sent[i + 1], bot.sent[i + 2]
    assert first["text"].startswith("🧊 t_eeeeeee5")
    assert "atascada en triage · bloqueada 2 veces seguidas · verificación mecánica fallida" in first["text"]
    assert "test_cmd" not in first["text"]  # nada técnico a Telegram
    assert "pregunta repetida: ¿Borro la tabla vieja?" in second["text"]
    assert texts(first["markup"]) == ["🔄 Reintentar", "🗄 Aparcar"]
    assert messages.all_messages("t_eeeeeee5")  # copia registrada: decidir aquí edita las demás


def test_retry_on_a_stuck_task_requeues_it_without_rewriting(tmp_path):
    cc, desk, bot, h, _ = triage_center(tmp_path, {"t_eeeeeee5": stuck("t_eeeeeee5")})
    command(desk, "/decisiones")
    card_msg = next(m for m in bot.sent if m["text"].startswith("🧊 t_eeeeeee5"))
    press(desk, card_msg, 0)
    assert ("unblock", "t_eeeeeee5") in h.calls and ("requeue_triage", "t_eeeeeee5") in h.calls
    assert h.shows["t_eeeeeee5"]["task"]["status"] == "ready"
    assert "🔄 reencolada" in bot.edits[-1]["text"]


def test_old_blocked_notice_retry_also_works_once_the_task_went_to_triage(tmp_path):
    cc, desk, bot, h, _ = triage_center(tmp_path, {"t_eeeeeee5": stuck("t_eeeeeee5")})
    markup = desk.markup("blocked", task={"id": "t_eeeeeee5", "title": "T"}, lane=cc.lanes["claude-migrateam"],
                         block_kind="transient")
    old = {"chat_id": GESTION, "thread_id": "230", "message_id": 77, "markup": markup}
    press(desk, old, 0)
    assert h.shows["t_eeeeeee5"]["task"]["status"] == "ready"


def test_retry_reports_when_the_task_is_neither_blocked_nor_in_triage(tmp_path):
    shows = {"t_eeeeeee5": stuck("t_eeeeeee5")}
    shows["t_eeeeeee5"]["task"]["status"] = "done"
    cc, desk, bot, h, _ = triage_center(tmp_path, shows)
    markup = desk.markup("stuck", task={"id": "t_eeeeeee5", "title": "T"}, lane=cc.lanes["claude-migrateam"])
    press(desk, {"chat_id": GESTION, "thread_id": "230", "message_id": 78, "markup": markup}, 0)
    assert any("ni en triage" in e["text"] for e in bot.edits)


def test_hermes_requeue_triage_passes_ids_by_argv_and_reads_status():
    calls = []

    def run(args, **kw):
        calls.append((args, kw))
        if args[1:3] == ["kanban", "--board"] and "show" in args:
            return cp(0, json.dumps({"task": {"id": "t_1234abcd", "status": "ready"}}))
        if "comment" in args:
            return cp(0)
        return cp(0, "ok\n")
    h = HermesCLI("migrateam", exe=Path("hermes.exe"), runner=run)
    assert h.requeue_triage("t_1234abcd", python=Path("py.exe")) == "ready"
    args, kw = calls[0]
    assert args[:2] == ["py.exe", "-c"] and args[3:] == ["migrateam", "t_1234abcd", OSCAR_AUTHOR]
    assert "t_1234abcd" not in args[2] and "specify_triage_task" in args[2]  # código fijo, ids por argv
    assert "kanban specify" not in " ".join(args)  # nunca la CLI (reescribe con LLM)
    assert any("comment" in a for a, _ in calls)


@pytest.mark.parametrize("tid,out", [("--board x", "ok\n"), ("t_1234abcd", "no\n")])
def test_hermes_requeue_triage_rejects_bad_ids_and_non_triage(tid, out):
    h = HermesCLI("migrateam", exe=Path("hermes.exe"), runner=lambda a, **k: cp(0, out))
    assert h.requeue_triage(tid, python=Path("py.exe")) is None


def test_reminders_count_stuck_tasks(tmp_path):
    assert reminder_text(2, 5, 1, 3) == ("Tienes 2 preguntas de agentes (la más antigua hace 5 h), 3 tareas "
                                         "atascadas en triage y 1 tarjeta de decisión · /decisiones")
    assert reminder_text(0, 0, 0, 1) == "Tienes 1 tarea atascada en triage · /decisiones"
    from datetime import datetime
    from agent_lanes.reminders import MADRID
    sent = []
    bot = SimpleNamespace(send_to=lambda chat, thread, text, html=False: sent.append(text))
    r = Reminders(bot, "1", lambda: [], tmp_path / "rem.json", now=lambda: datetime(2026, 9, 28, 13, 5, tzinfo=MADRID),
                  stuck=lambda: [object()])
    assert r.tick() == "Tienes 1 tarea atascada en triage · /decisiones" and sent


# --- 📋 tarjetas sin etiqueta ------------------------------------------------------------------------------

def test_untagged_line_after_decision_cards_with_panel_link(tmp_path):
    shows = {"t_d0000001": card("t_d0000001", "[DECISIÓN] Precio"),
             "t_d0000003": card("t_d0000003", "Llamar a la gestoría"),
             "t_d0000006": card("t_d0000006", "Renovar dominio", status="blocked"),
             "t_d0000007": card("t_d0000007", "Otra de un carril", assignee="claude-oscarhq")}
    cc, desk, bot, h, _ = center(tmp_path, shows)
    assert cc.oscar_cards() == (cc.decision_cards(), 2)
    command(desk, "/decisiones")
    text = bot.sent[-1]["text"]
    assert text.startswith("📌 <b>Tus tarjetas de decisión (1)</b>")
    assert text.endswith('📋 2 tarjetas más a tu nombre sin etiqueta · <a href="https://k/">panel</a>')


@pytest.mark.parametrize("base,url", [("https://k", "https://k/"), ("https://k/", "https://k/"),
                                      ("https://panel.x/tasks/{board}/{id}", "https://panel.x/"), ("", None)])
def test_panel_url(base, url):
    assert panel_url(base) == url


# --- candado de repos ---------------------------------------------------------------------------------------

GH_LANE = Lane(name="claude-oscarhq", board="oscarhq", repo="C:/ohq", base="master", github="oscarsao/oscar-hq")


@pytest.mark.parametrize("remote", ["https://github.com/oscarsao/oscar-hq.git", "git@github.com:OscarSao/Oscar-HQ.git",
                                    "https://x:token@github.com/oscarsao/oscar-hq"])
def test_github_slug_matches_https_and_ssh(remote):
    calls = []

    def run(args, **kw):
        calls.append(args)
        return cp(0, remote + "\n")
    assert github_slug_problem(GH_LANE, run=run) is None
    assert calls == [["git", "-C", "C:/ohq", "remote", "get-url", "origin"]]


@pytest.mark.parametrize("lane,remote,expect", [
    (GH_LANE, "https://github.com/oscarsao/otro.git", "apunta a oscarsao/otro"),
    (GH_LANE, "https://gitlab.com/oscarsao/oscar-hq.git", "no es un repo de GitHub"),
    (Lane(name="claude-x", board="b", repo="C:/x", base="main"), "https://github.com/a/b", "falta `github"),
])
def test_github_slug_problems(lane, remote, expect):
    assert expect in github_slug_problem(lane, run=lambda a, **k: cp(0, remote))


def test_code_lane_with_mismatched_repo_does_not_claim_and_logs_once(caplog):
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(),
                   notify=FakeNotifier(), repo_guard=lambda lane: "claude-oscarhq: apunta a otro/repo")
    with caplog.at_level(logging.ERROR, logger="agent_lanes"):
        assert r.jobs() == [] and r.run_once() == {}
    assert not [c for c in h.calls if c[0] == "claim"]
    assert len([m for m in caplog.messages if "candado de repos" in m]) == 1


def test_code_lane_without_github_does_not_claim():
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(),
                   notify=FakeNotifier(), repo_guard=lambda lane: github_slug_problem(lane, run=lambda a, **k: cp()))
    assert LANE.github == "" and r.jobs() == []


def test_matching_repo_claims_normally():
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(),
                   notify=FakeNotifier(), repo_guard=lambda lane: None)
    assert [tid for tid, _ in r.jobs()] == ["t_1"]


def test_real_lanes_yaml_locks_every_code_lane():
    lanes = load_lanes()
    expected = {"claude-oscarhq": "oscarsao/oscar-hq", "claude-migrateam": "PildoraDigital/OCR-PDF-and-images",
                "claude-scraper": "oscarsao/icp-prospect-engine", "claude-nextjobs": "oscarsao/nextjobs-autoapply"}
    assert {n: lanes[n].github for n in expected} == expected
    assert all(l.github for l in lanes.values() if l.kind == "implement")


# --- drain + status ----------------------------------------------------------------------------------------

def test_drain_file_older_than_two_hours_is_ignored(tmp_path):
    f = tmp_path / "drain.json"
    drain.request(f, now=1000.0)
    assert drain.active(f, now=1000.0 + 3600)
    assert drain.active(f, now=1000.0 + 2 * 3600 + 1) is None
    assert drain.discard_stale(f, now=1000.0 + 3 * 3600) and not f.exists()
    drain.request(f, now=1000.0)
    assert not drain.discard_stale(f, now=1500.0) and f.exists()


class Sched:
    """schtasks + PIDs simulados. /End mata el runner viejo; /Run arranca uno nuevo que escribe runner.lock."""

    def __init__(self, tmp_path, old=100, new=200):
        self.tmp, self.old, self.new = tmp_path, old, new
        self.alive = {old}
        self.calls = []
        (tmp_path / "runner.lock").write_text(json.dumps({"pid": old}), encoding="utf-8")

    def run(self, args, **kw):
        self.calls.append(args[1])
        if args[1] == "/End":
            self.alive.discard(self.old)
        if args[1] == "/Run":
            self.alive.add(self.new)
            (self.tmp / "runner.lock").write_text(json.dumps({"pid": self.new}), encoding="utf-8")
        return cp(0)


def restarter(tmp_path, sched, sleep=None, clock=None):
    return drain.Restarter(pid_alive=lambda p: p in sched.alive, drain_file=tmp_path / "drain.json",
                           ack_file=tmp_path / "ack.json", lock_file=tmp_path / "runner.lock", run=sched.run,
                           sleep=sleep or (lambda s: None), clock=clock or time.monotonic, now=lambda: 1000.0,
                           out=lambda s: None)


def test_restart_waits_for_the_idle_ack_then_end_and_run(tmp_path):
    s = Sched(tmp_path)
    steps = {"n": 0}

    def sleep(sec):  # el runner termina su pasada y acusa el drenaje en la 2ª espera
        steps["n"] += 1
        assert (tmp_path / "drain.json").exists() and s.calls == []  # nada se para con workers vivos
        busy = [] if steps["n"] >= 2 else ["deploy en curso"]
        drain.write_ack(tmp_path / "ack.json", pid=100, busy=busy, now=1001.0)
    assert restarter(tmp_path, s, sleep=sleep).restart(timeout=600) == 0
    assert s.calls == ["/End", "/Run"]
    assert not (tmp_path / "drain.json").exists() and not (tmp_path / "ack.json").exists()


def test_restart_ignores_an_ack_older_than_the_request(tmp_path):
    s = Sched(tmp_path)
    drain.write_ack(tmp_path / "ack.json", pid=100, busy=[], now=10.0)  # de un drenaje anterior
    t = {"now": 0.0}

    def sleep(sec):
        t["now"] += sec
    assert restarter(tmp_path, s, sleep=sleep, clock=lambda: t["now"]).restart(timeout=30) == 1
    assert s.calls == [] and not (tmp_path / "drain.json").exists()  # timeout: drenaje cancelado, sin reinicio


def test_restart_without_live_runner_just_runs_the_task(tmp_path):
    s = Sched(tmp_path)
    s.alive.clear()
    assert restarter(tmp_path, s).restart() == 0 and s.calls == ["/Run"]


def test_restart_requires_drain_flag():
    import lanes
    with pytest.raises(SystemExit):
        lanes.restart([])


def test_runner_line_shows_drain(tmp_path):
    (tmp_path / "runner.lock").write_text(json.dumps({"pid": 7}), encoding="utf-8")
    line = runner_line(tmp_path / "runner.lock", lambda p: True, {"since": time.time()})
    assert line.startswith("runner: VIVO (pid 7) · drenando desde")


class StatusHermes:
    def __init__(self, by_status):
        self.by_status = by_status

    def list_status(self, assignee, status, sort=None):
        return self.by_status.get(status, [])


def test_running_without_state_is_starting_while_the_runner_lives(tmp_path):
    h = StatusHermes({"running": [{"id": "t_new", "title": "N"}]})
    rows = lane_rows({LANE.name: LANE}, hermes_for=lambda b: h, state_dir=tmp_path, pid_alive=lambda p: True,
                     runner_is_alive=True)
    assert rows[0]["state"] == "arrancando"
    rows = lane_rows({LANE.name: LANE}, hermes_for=lambda b: h, state_dir=tmp_path, pid_alive=lambda p: False,
                     runner_is_alive=False)
    assert rows[0]["state"] == "huérfano"


def test_state_file_is_written_right_after_the_claim(tmp_path):
    seen = {}

    class H(FakeHermes):
        def drop_telegram_subs(self, task_id):
            seen["state"] = (tmp_path / f"{task_id}.json").exists()
            return super().drop_telegram_subs(task_id)
    LaneRunner(LANE, hermes=H(tasks=[{"id": "t_1", "title": "T", "body": "B"}]), git=FakeGit(),
               worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(), notify=FakeNotifier(),
               state_dir=tmp_path).run_once()
    assert seen["state"] is True and not (tmp_path / "t_1.json").exists()
