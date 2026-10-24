"""Aplicar actualiza el código: `restart --drain` pone al día el checkout vivo (fast-forward a origin/main), verifica
HEAD == origin/main y «integrador activo», y el integrador deja «APLICADO <sha>» solo con ese veredicto. Todo con dobles:
ni git, ni schtasks, ni Telegram reales."""
from __future__ import annotations

import json
import subprocess
import time

from agent_lanes import drain
from agent_lanes.people import People
from agent_lanes.integrator import APPLY_LABEL, APPLY_WAIT, LAG_AFTER, LAG_REPEAT

from .test_hub_lane import hub
from .test_integrator import MERGE, buttons, press
from .test_plan_d_runtime import Sched

OLD, NEW = "a" * 40, "b" * 40


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class FakeGit:
    """git del checkout vivo. `dirty`: salida de `status --porcelain`; `ahead`: commits locales propios."""

    def __init__(self, sched=None, *, dirty="", ahead="", branch="main", fetch_rc=0, ff_rc=0):
        self.calls, self.sched = [], sched
        self.dirty, self.ahead, self.branch, self.fetch_rc, self.ff_rc = dirty, ahead, branch, fetch_rc, ff_rc
        self.head, self.origin = OLD, NEW

    def __call__(self, args, **kw):
        assert args[:3][0] == "git"
        verb = [a for a in args[3:]]
        self.calls.append(verb)
        if verb[0] == "fetch":
            return cp(self.fetch_rc, "", "sin red" if self.fetch_rc else "")
        if verb[:2] == ["rev-parse", "--abbrev-ref"]:
            return cp(0, self.branch + "\n")
        if verb[0] == "status":
            return cp(0, self.dirty)
        if verb[0] == "merge-base":
            return cp(1 if self.ahead else 0)
        if verb[0] == "log":
            return cp(0, self.ahead)
        if verb[0] == "merge":
            assert self.sched is None or "/End" in self.sched.calls  # con el runner ya parado
            assert "/Run" not in (self.sched.calls if self.sched else [])
            if not self.ff_rc:
                self.head = self.origin
            return cp(self.ff_rc, "", "Not possible to fast-forward" if self.ff_rc else "")
        if verb == ["rev-parse", "HEAD"]:
            return cp(0, self.head + "\n")
        if verb == ["rev-parse", "origin/main"]:
            return cp(0, self.origin + "\n")
        raise AssertionError(args)


def restarter(tmp_path, sched, git, *, alerts=None, active=True, sleep=None, clock=None):
    def run(args, **kw):
        res = sched.run(args, **kw)
        if args[1] == "/Run" and active:  # el runner nuevo escribe su marca al activar el integrador
            (tmp_path / "active.json").write_text(json.dumps({"pid": sched.new, "at": time.time() + 1}), encoding="utf-8")
        return res
    return drain.Restarter(pid_alive=lambda p: p in sched.alive, drain_file=tmp_path / "drain.json",
                           ack_file=tmp_path / "ack.json", lock_file=tmp_path / "runner.lock", run=run,
                           sleep=sleep or (lambda s: None), clock=clock or time.monotonic, now=time.time,
                           out=lambda s: None, repo=tmp_path / "live", git_run=git,
                           result_file=tmp_path / "result.json", active_file=tmp_path / "active.json",
                           alert=(alerts.append if alerts is not None else None))


def idle_ack(tmp_path):
    drain.write_ack(tmp_path / "ack.json", pid=100, busy=[], now=time.time() + 5)


def verdict(tmp_path):
    return json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))


# --- comprobaciones de git ------------------------------------------------------------------------------------

def test_sync_check_ok(tmp_path):
    assert drain.sync_check(tmp_path, FakeGit()) == (True, "")


def test_sync_check_lists_dirty_tracked_files_only(tmp_path):
    ok, why = drain.sync_check(tmp_path, FakeGit(dirty=" M tools/a.py\nM  tools/b.py\n"))
    assert not ok and "cambios locales" in why and "tools/a.py" in why and "tools/b.py" in why
    git = FakeGit()
    drain.sync_check(tmp_path, git)
    assert ["status", "--porcelain", "--untracked-files=no"] in git.calls  # no versionados y __pycache__ se ignoran


def test_sync_check_divergent_main_lists_commits_and_wrong_branch_and_fetch(tmp_path):
    ok, why = drain.sync_check(tmp_path, FakeGit(ahead="abc1234 commit local\n"))
    assert not ok and "diverge" in why and "abc1234 commit local" in why
    assert "no en main" in drain.sync_check(tmp_path, FakeGit(branch="feat/x"))[1]
    assert "fetch" in drain.sync_check(tmp_path, FakeGit(fetch_rc=1))[1]


# --- restart --drain ---------------------------------------------------------------------------------------------

def test_dirty_checkout_does_not_restart_and_alerts_high(tmp_path):
    s, git, alerts = Sched(tmp_path), FakeGit(dirty=" M tools/a.py"), []
    assert restarter(tmp_path, s, git, alerts=alerts).restart() == 1
    assert s.calls == [] and not (tmp_path / "drain.json").exists()  # ni se drena ni se toca el runner
    assert alerts and alerts[0].startswith("🚨 ALTA") and "tools/a.py" in alerts[0]
    assert verdict(tmp_path)["ok"] is False and "tools/a.py" in verdict(tmp_path)["reason"]


def test_divergent_main_does_not_restart(tmp_path):
    s, alerts = Sched(tmp_path), []
    assert restarter(tmp_path, s, FakeGit(ahead="abc1234 propio"), alerts=alerts).restart() == 1
    assert s.calls == [] and "abc1234 propio" in alerts[0]


def test_restart_fast_forwards_with_runner_stopped_then_verifies(tmp_path):
    s, git, alerts = Sched(tmp_path), FakeGit(), []
    s.alive = {100}
    idle_ack(tmp_path)
    assert restarter(tmp_path, s, git, alerts=alerts).restart() == 0
    assert s.calls == ["/End", "/Run"] and ["merge", "--ff-only", "origin/main"] in git.calls
    assert alerts == []
    v = verdict(tmp_path)
    assert v["ok"] is True and v["sha"] == NEW


def test_head_not_at_origin_after_restart_is_a_failure(tmp_path):
    s, git, alerts = Sched(tmp_path), FakeGit(ff_rc=1), []
    idle_ack(tmp_path)
    assert restarter(tmp_path, s, git, alerts=alerts).restart() == 1
    assert s.calls == ["/End", "/Run"]  # el runner no se queda parado: arranca (con el código viejo)
    assert "fast-forward" in verdict(tmp_path)["reason"] and alerts


def test_runner_without_integrator_marker_is_a_failure(tmp_path):
    s, alerts, t = Sched(tmp_path), [], {"now": 0.0}

    def sleep(sec):
        t["now"] += sec
    idle_ack(tmp_path)
    r = restarter(tmp_path, s, FakeGit(), alerts=alerts, active=False, sleep=sleep, clock=lambda: t["now"])
    assert r.restart() == 1
    assert "integrador activo" in verdict(tmp_path)["reason"] and alerts


# --- el integrador deja la ficha según el veredicto -------------------------------------------------------------

def applying(tmp_path):
    integ, w, h, tg, d, launched = hub(tmp_path)
    integ.run_pass()
    press(d, tg)  # fusionar
    press(d, tg)  # aplicar
    assert integ._state("t_1")["status"] == "applying"
    return integ, h, tg, d


def write_verdict(integ, **v):
    integ.apply_result_path.write_text(json.dumps({"at": time.time() + 10, **v}), encoding="utf-8")


def test_applied_comment_only_after_a_successful_verdict(tmp_path):
    integ, h, tg, d = applying(tmp_path)
    assert integ.finalize_apply() == []  # sin veredicto aún: nada
    assert not any(t.startswith("APLICADO") for _, t, _a in h.comments)
    write_verdict(integ, ok=True, sha=NEW, reason="")
    assert integ.finalize_apply() == ["t_1"]
    assert integ._state("t_1")["status"] == "applied"
    assert any(t.startswith(f"APLICADO {MERGE}") and NEW[:12] in t for _, t, _a in h.comments)
    assert "aplicado" in tg.edits[-1]["text"] and not buttons(tg.edits[-1]["markup"])


def test_failed_verdict_puts_stop_with_reason_and_retry_button(tmp_path):
    integ, h, tg, d = applying(tmp_path)
    write_verdict(integ, ok=False, reason="cambios locales en ficheros versionados: tools/a.py")
    assert integ.finalize_apply() == ["t_1"]
    assert integ._state("t_1")["status"] == "merged"
    assert not any(t.startswith("APLICADO") for _, t, _a in h.comments)
    assert "⛔" in tg.edits[-1]["text"] and "tools/a.py" in tg.edits[-1]["text"]
    assert buttons(tg.edits[-1]["markup"]) == [APPLY_LABEL]


def test_verdict_older_than_the_press_is_ignored_and_missing_one_fails_after_the_wait(tmp_path):
    integ, h, tg, d = applying(tmp_path)
    integ.apply_result_path.write_text(json.dumps({"at": 1.0, "ok": True, "sha": NEW}), encoding="utf-8")
    assert integ.finalize_apply() == []
    t0 = time.time()
    integ._now = lambda: t0 + APPLY_WAIT + 60
    assert integ.finalize_apply() == ["t_1"]
    assert "sin veredicto" in tg.edits[-1]["text"] or "no dejó veredicto" in tg.edits[-1]["text"]
    assert integ._state("t_1")["status"] == "merged"


def test_apply_pressed_while_applying_does_not_launch_again(tmp_path):
    integ, w, h, tg, d, launched = hub(tmp_path)
    integ.run_pass()
    press(d, tg)
    markup = tg.edits[-1]["markup"]
    press(d, tg, markup=markup)
    press(d, tg, markup=markup)
    assert len(launched) == 1 and integ._state("t_1")["status"] == "applying"


# --- aviso de checkout atrasado --------------------------------------------------------------------------------

def lagging(tmp_path):
    integ, w, h, tg, d, launched = hub(tmp_path)
    d.people = People(d.owner_id)  # sin operadores: el aviso solo va al DM de Oscar (no depende de people.yaml real)
    dm = []

    def send_to(chat_id, thread_id, text, **kw):
        dm.append({"chat": chat_id, "text": text, "markup": kw.get("reply_markup")})
        return {"chat_id": str(chat_id), "thread_id": "0", "message_id": 900 + len(dm)}
    tg.send_to = send_to
    clock = {"t": 1_000_000.0}
    integ._now = lambda: clock["t"]
    integ._launch = lambda argv, cwd: launched.append((argv, cwd))
    return integ, dm, clock, launched


def test_lag_over_30_minutes_alerts_with_apply_button(tmp_path):
    integ, dm, clock, launched = lagging(tmp_path)
    integ.check_live_lag()  # primera vez que se ve atrasado: arranca el cronómetro
    assert dm == []
    clock["t"] += LAG_AFTER - 60
    integ.check_live_lag()
    assert dm == []
    clock["t"] += 120
    integ.check_live_lag()
    assert len(dm) == 1 and dm[0]["text"].startswith("🚨 ALTA") and buttons(dm[0]["markup"]) == [APPLY_LABEL]
    clock["t"] += 600
    integ.check_live_lag()
    assert len(dm) == 1  # no repite hasta LAG_REPEAT
    clock["t"] += LAG_REPEAT
    integ.check_live_lag()
    assert len(dm) == 2


def test_lag_apply_button_launches_restart(tmp_path):
    integ, dm, clock, launched = lagging(tmp_path)
    integ.check_live_lag()
    clock["t"] += LAG_AFTER + 1
    integ.check_live_lag()
    d = integ.desk  # el teclado vive en el DM, no en tg.sent/edits
    data = dm[0]["markup"]["inline_keyboard"][0][0]["callback_data"]
    d.handle_update({"callback_query": {"id": "cq", "from": {"id": 6744452215}, "data": data,
                                        "message": {"chat": {"id": 6744452215}, "message_id": 901}}})
    assert len(launched) == 1 and integ._state("lag-claude-oscarhq")["status"] == "applying"


def test_lag_cleared_when_checkout_catches_up(tmp_path):
    integ, dm, clock, launched = lagging(tmp_path)
    integ.check_live_lag()
    assert integ._state("lag-claude-oscarhq")["behind_since"]
    integ._git = lambda repo, *a, **k: cp(0, "0\n")  # al día
    integ.check_live_lag()
    assert not integ._state("lag-claude-oscarhq")["behind_since"]
    clock["t"] += LAG_AFTER * 3
    integ.check_live_lag()
    assert dm == []
