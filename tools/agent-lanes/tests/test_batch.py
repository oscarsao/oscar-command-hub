"""Lote diario del integrador (batch.py): git REAL sobre repos temporales (origen bare local), gh/Hermes/Telegram
simulados. Nada de red, de GitHub ni de Telegram reales."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from agent_lanes import proc as _proc
from agent_lanes.batch import BATCH_BRANCH_RE, BATCH_LABEL, render_batch_ficha
from agent_lanes.commands import COMMANDS, CommandCenter, parse_command
from agent_lanes.config import Lane
from agent_lanes.decisions import CallbackStore, DecisionDesk
from agent_lanes.hermes import OSCAR_AUTHOR
from agent_lanes.integrator import Integrator, IntegratorSettings, Policy, load_integrator_settings
from agent_lanes.notices import MessageStore
from tests.test_integrator import GH, OSCAR, Clock, FakeHermes, FakeLinks, FakeTg, buttons, press

SLUG = "oscarsao/oscar-hq"
SECRET = "sk-ant-api03-" + "Z" * 40
TEST_CMD = 'py -3.12 -c "import os,sys;sys.exit(1 if os.path.exists(\'BROKEN\') else 0)"'


def sh(cwd, *args) -> str:
    cp = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", "-c",
                         "commit.gpgsign=false", *args], capture_output=True, text=True, encoding="utf-8")
    assert cp.returncode == 0, (args, cp.stderr)
    return cp.stdout.strip()


class Gh:
    """gh simulado sobre el repo real: PR de lane/* registrados con `add`; el resto de comandos (git) va de verdad."""

    def __init__(self, origin: Path):
        self.origin, self.prs, self.log, self.created = origin, {}, [], []
        self.merge_rc = 0

    def add(self, n, tid, sha):
        self.prs[n] = {"number": n, "url": f"https://github.com/{SLUG}/pull/{n}", "state": "OPEN", "isDraft": False,
                       "headRefName": f"lane/{tid}", "headRefOid": sha, "baseRefName": "main",
                       "isCrossRepository": False, "mergeCommit": None}
        sh(self.origin, "update-ref", f"refs/pull/{n}/head", sha)

    def __call__(self, args, **kw):
        if args[0] != GH:
            return _proc.run(args, **kw)
        self.log.append(args)
        if args[1:3] == ["pr", "view"]:
            n = int(args[3])
            return subprocess.CompletedProcess(args, 0, json.dumps(self.prs[n]) if n in self.prs else "null", "")
        if args[1:3] == ["pr", "create"]:
            branch = args[args.index("--head") + 1]
            n = 100 + len(self.created)
            sha = sh(self.origin, "rev-parse", f"refs/heads/{branch}")
            self.created.append({"n": n, "branch": branch, "body": args[args.index("--body") + 1],
                                 "title": args[args.index("--title") + 1]})
            self.prs[n] = {"number": n, "url": f"https://github.com/{SLUG}/pull/{n}", "state": "OPEN",
                           "isDraft": False, "headRefName": branch, "headRefOid": sha, "baseRefName": "main",
                           "isCrossRepository": False, "mergeCommit": None}
            return subprocess.CompletedProcess(args, 0, self.prs[n]["url"] + "\n", "")
        if args[1:3] == ["pr", "merge"]:
            n = int(args[3])
            if self.merge_rc == 0:
                self.prs[n] = {**self.prs[n], "state": "MERGED", "mergeCommit": {"oid": "f" * 40}}
            return subprocess.CompletedProcess(args, self.merge_rc, "", "" if self.merge_rc == 0 else "boom")
        if args[1:3] == ["pr", "close"]:
            self.prs[int(args[3])]["state"] = "CLOSED"
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    def named(self, what):
        return [a for a in self.log if a[1:3] == ["pr", what]]


class Env:
    def __init__(self, tmp: Path, policy: Policy | None = None, *, deploy="none"):
        self.tmp = tmp
        self.origin, self.repo = tmp / "origin.git", tmp / "repo"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.origin)], capture_output=True, check=True)
        subprocess.run(["git", "init", "-b", "main", str(self.repo)], capture_output=True, check=True)
        sh(self.repo, "remote", "add", "origin", str(self.origin))
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-m", "base")
        sh(self.repo, "push", "origin", "main")
        sh(self.repo, "fetch", "origin")
        self.gh = Gh(self.origin)
        self.tasks, self.comments = [], {}
        self.n = 0
        self.lane = Lane(name="claude-oscarhq", board="oscarhq", repo=str(self.repo), base="main", remote="origin",
                         test_cmd=TEST_CMD, telegram=("-100", "231"), forbidden_paths=("secret-zone/",))
        self.policy = policy or Policy(lane="claude-oscarhq", batch="daily", batch_brand="oscarhq", deploy=deploy,
                                       manual_migrations=("supabase/migrations/",),
                                       batch_frontend_paths=("frontend/",),
                                       batch_frontend_cmd='py -3.12 -c "import sys;sys.exit(0)"')
        self.h = FakeHermes(self.tasks, self.comments)
        self.h.reviewed = None
        self.tg = FakeTg()
        self.clock = Clock()
        settings = IntegratorSettings(enabled=True, interval_seconds=300, worktree_root=str(tmp / "wt"),
                                      deploy_timeout_seconds=900, poll_seconds=20,
                                      policies={"claude-oscarhq": self.policy}, integration_telegram=None)
        self.desk = DecisionDesk(self.tg, CallbackStore(tmp / "cb"), lanes={"claude-oscarhq": self.lane},
                                 hermes_for=lambda b: self.h, links=FakeLinks(SLUG), owner_id=str(OSCAR),
                                 runner=self.gh, spawn=lambda fn: fn())
        self.integ = Integrator(settings, {"claude-oscarhq": self.lane}, hermes_for=lambda b: self.h,
                                links=FakeLinks(SLUG), notifier=self.tg, messages=MessageStore(), desk=self.desk,
                                runner=self.gh, gh_exe=GH, clock=self.clock, sleep=self.clock.sleep,
                                state_dir=tmp / "state")
        self.desk.integrator = self.integ
        self.integ.batches.clock = lambda: datetime(2026, 10, 1, 7, 0)  # antes de las 08:30: nada se monta solo

    def write(self, path, text):
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def branch(self, tid: str, files: dict, *, approve=True) -> int:
        """Rama lane/<tid> desde main con esos archivos, subida al origen y registrada como PR aprobado."""
        self.n += 1
        n = 10 + self.n
        sh(self.repo, "checkout", "-q", "-b", f"lane/{tid}", "main")
        for path, text in files.items():
            self.write(path, text)
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-m", f"{tid}")
        sh(self.repo, "push", "-q", "origin", f"lane/{tid}")
        sha = sh(self.repo, "rev-parse", "HEAD")
        sh(self.repo, "checkout", "-q", "main")
        self.gh.add(n, tid, sha)
        self.tasks.append({"id": tid, "title": f"Tarea {tid}", "assignee": "claude-oscarhq", "body": ""})
        self.comments[tid] = [{"author": OSCAR_AUTHOR,
                               "body": f"APROBADO-OSCAR 2026-10-01 07:00 · PR https://github.com/{SLUG}/pull/{n}"}]
        if approve:
            d = self.tmp / "state" / "approvals"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{tid}.json").write_text(json.dumps({"task_id": tid, "pr": n, "head_sha": sha, "at": 1000 + self.n}),
                                           encoding="utf-8")
        return n

    def queue(self) -> dict:
        return self.integ.run_pass(force=True)

    def remote_branches(self) -> list[str]:
        return sh(self.origin, "for-each-ref", "--format=%(refname:short)", "refs/heads/release").split()


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


# --- configuración ------------------------------------------------------------------------------------------

def test_real_lanes_yaml_batch_only_in_migrateam_and_oscarhq():
    pol = load_integrator_settings(lane_filter=False).policies
    assert pol["claude-migrateam"].batch == "daily" and pol["claude-oscarhq"].batch == "daily"
    assert pol["claude-hub"].batch == ""  # el propio sistema sigue PR a PR
    assert pol["claude-oscarhq"].batch_time == "08:30"


def test_batch_config_validation(tmp_path):
    f = tmp_path / "l.yaml"
    f.write_text("integrator:\n  lanes:\n    x:\n      batch: weekly\n", encoding="utf-8")
    with pytest.raises(ValueError, match="batch"):
        load_integrator_settings(f)
    f.write_text("integrator:\n  lanes:\n    x:\n      batch: daily\n      batch_time: '25:99'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="HH:MM"):
        load_integrator_settings(f)


# --- sin regresiones ----------------------------------------------------------------------------------------

def test_without_batch_policy_pr_by_pr_as_before(tmp_path):
    e = Env(tmp_path, Policy(lane="claude-oscarhq", deploy="none"))
    e.branch("t_a", {"src/a.py": "a = 2\n"})
    assert e.queue() == {"t_a": "offered"}  # ficha 🚦 individual de siempre
    assert "Listo para integrar PR #11" in e.tg.all_text
    assert buttons(e.tg.sent[0]["markup"]) == ["🔀 Fusionar"]
    assert e.integ.batches.all() == []
    assert e.gh.named("create") == []


# --- cola y montaje -----------------------------------------------------------------------------------------

def test_approved_branches_queue_without_individual_ficha(env):
    env.branch("t_a", {"src/a.py": "a = 2\n"})
    assert env.queue() == {"t_a": "batch_pending"}
    assert env.tg.sent == []  # ni ficha ni gates sueltos
    assert env.integ._state("t_a")["status"] == "batch_pending"


def test_assemble_merges_in_approval_order_one_pr_one_button(env):
    env.branch("t_a", {"src/a.py": "a = 2\n"})
    env.branch("t_b", {"src/b.py": "b = 2\n"})
    env.queue()
    out = env.integ.batches.assemble(env.lane, env.policy)
    assert "PR #100" in out and "2 rama" in out
    created = env.gh.created
    assert len(created) == 1 and created[0]["branch"] == "release/lote-oscarhq-2026-10-01"
    assert BATCH_BRANCH_RE.fullmatch(created[0]["branch"])
    assert created[0]["body"].index("t_a") < created[0]["body"].index("t_b")  # orden de aprobación
    assert env.remote_branches() == ["release/lote-oscarhq-2026-10-01"]
    # la rama del lote contiene lo de las dos ramas
    tree = sh(env.origin, "show", "release/lote-oscarhq-2026-10-01:src/a.py")
    assert "a = 2" in tree and "b = 2" in sh(env.origin, "show", "release/lote-oscarhq-2026-10-01:src/b.py")
    # UNA sola ficha con la tabla y un solo botón
    assert len(env.tg.sent) == 1
    card = env.tg.sent[0]
    assert buttons(card["markup"]) == [BATCH_LABEL]
    assert env.integ._state("t_a")["status"] == env.integ._state("t_b")["status"] == "batch_included"
    # en la cola, las incluidas ya no se vuelven a ofrecer
    assert env.queue() == {}
    # no quedan worktrees ni la rama local de lote en el repo
    assert sh(env.repo, "branch", "--list", "release/*") == ""
    assert not list((env.tmp / "wt").glob("int-*"))


def test_excluded_branches_do_not_block_the_rest(env):
    env.branch("t_a", {"src/a.py": "a = 2\n"})
    env.branch("t_conf", {"src/a.py": "a = 3\n"})              # choca con t_a
    env.branch("t_sec", {"src/c.py": f'KEY = "{SECRET}"\n'})    # secreto
    env.branch("t_zone", {"secret-zone/x.txt": "x\n"})          # ruta vetada del carril
    env.branch("t_ok", {"src/b.py": "b = 2\n"})
    env.queue()
    env.integ.batches.assemble(env.lane, env.policy)
    b = env.integ.batches.all()[0]
    assert [m["tid"] for m in b["members"]] == ["t_a", "t_ok"]
    reasons = {x["tid"]: x["reason"] for x in b["excluded"]}
    assert "choca" in reasons["t_conf"] and "secreto" in reasons["t_sec"] and "vetadas" in reasons["t_zone"]
    assert SECRET not in json.dumps(b) and SECRET not in env.tg.all_text
    text = env.tg.sent[0]["text"]
    assert "Fuera del lote (3)" in text and "Incluye (2)" in text
    # cada excluida lo sabe en su tarjeta, y vuelve a la cola del siguiente lote
    assert any("FUERA del lote" in c[1] for c in env.h.comments if c[0] == "t_conf")
    assert env.integ._state("t_conf")["status"] == "batch_pending"


def test_combined_test_failure_isolates_the_culprit(env):
    env.branch("t_a", {"src/a.py": "a = 2\n"})
    env.branch("t_bad", {"BROKEN": "x\n"})                      # su test_cmd falla
    env.branch("t_b", {"src/b.py": "b = 2\n"})
    env.queue()
    env.integ.batches.assemble(env.lane, env.policy)
    b = env.integ.batches.all()[0]
    assert [m["tid"] for m in b["members"]] == ["t_a", "t_b"]
    assert [x["tid"] for x in b["excluded"]] == ["t_bad"] and "probada sola" in b["excluded"][0]["reason"]
    assert "BROKEN" not in sh(env.origin, "ls-tree", "-r", "--name-only", f"refs/heads/{b['branch']}")


def test_no_batch_when_nothing_passes(env):
    env.branch("t_bad", {"BROKEN": "x\n"})
    env.queue()
    out = env.integ.batches.assemble(env.lane, env.policy)
    assert "no se pudo montar" in out or "vacío" in out
    assert env.gh.created == [] and env.remote_branches() == []


def test_alembic_second_head_is_left_out(tmp_path):
    pol = Policy(lane="claude-oscarhq", batch="daily", batch_brand="oscarhq", alembic_versions="alembic/versions/")
    e = Env(tmp_path, pol)
    rev = lambda rid, down: f'revision = "{rid}"\ndown_revision = {down!r}\n'  # noqa: E731
    e.write("alembic/versions/0001.py", rev("0001", None))
    sh(e.repo, "add", "-A")
    sh(e.repo, "commit", "-m", "alembic")
    sh(e.repo, "push", "-q", "origin", "main")
    e.branch("t_m1", {"alembic/versions/0002_a.py": rev("0002a", "0001")})
    e.branch("t_m2", {"alembic/versions/0002_b.py": rev("0002b", "0001")})  # otro head
    e.queue()
    e.integ.batches.assemble(e.lane, pol)
    b = e.integ.batches.all()[0]
    assert [m["tid"] for m in b["members"]] == ["t_m1"] and "heads" in b["excluded"][0]["reason"]
    assert b["migration"] == "alembic"


# --- fusión -------------------------------------------------------------------------------------------------

def test_merge_button_integrates_members_closes_prs_and_aggregates_migration(env):
    na = env.branch("t_a", {"src/a.py": "a = 2\n"})
    env.branch("t_b", {"supabase/migrations/001.sql": "select 1;\n"})
    env.queue()
    env.integ.batches.assemble(env.lane, env.policy)
    assert env.integ._state("t_a")["status"] == "batch_included"
    press(env.desk, env.tg)  # [🔀 Fusionar lote]
    assert env.gh.named("merge") and "--squash" in env.gh.named("merge")[0] and "--match-head-commit" in env.gh.named("merge")[0]
    for tid in ("t_a", "t_b"):
        st = env.integ._state(tid)
        assert st["status"] == "merged" and st["merge_sha"] == "f" * 40 and st["migration"] == "supabase"
        assert any(c[0] == tid and c[1].startswith("INTEGRADO " + "f" * 40) and c[2] == "lane-integrator"
                   for c in env.h.comments)
    assert env.gh.prs[na]["state"] == "CLOSED"  # el PR suelto queda cerrado como integrado
    assert env.integ.batches.all()[0]["status"] == "merged"
    # nuevas pasadas no los vuelven a tocar
    assert env.queue() == {}


def test_migration_applied_at_batch_level(env):
    env.branch("t_a", {"src/a.py": "a = 2\n"})
    env.branch("t_b", {"supabase/migrations/001.sql": "select 1;\n"})
    env.queue()
    env.integ.batches.assemble(env.lane, env.policy)
    press(env.desk, env.tg)
    ok, _ = env.integ.mark_migration_applied("t_a", OSCAR_AUTHOR, desk=env.desk)
    assert ok
    for tid in ("t_a", "t_b"):
        assert env.integ._state(tid)["migration_applied"]


def test_merge_refused_when_base_moved_then_lote_command_rebuilds(env):
    env.branch("t_a", {"src/a.py": "a = 2\n"})
    env.queue()
    env.integ.batches.assemble(env.lane, env.policy)
    # la base avanza fuera del lote
    env.write("src/b.py", "b = 9\n")
    sh(env.repo, "add", "-A")
    sh(env.repo, "commit", "-m", "otro")
    sh(env.repo, "push", "-q", "origin", "main")
    press(env.desk, env.tg)
    assert env.gh.named("merge") == []  # no se fusiona lo que ya no es lo probado
    assert env.integ.batches.all()[0]["status"] == "stale"
    # /lote lo monta de nuevo: cierra el viejo y abre uno con sufijo
    out = env.integ.batches.assemble(env.lane, env.policy, manual=True)
    assert "PR #101" in out
    assert env.gh.created[1]["branch"] == "release/lote-oscarhq-2026-10-01-2"
    assert env.gh.prs[100]["state"] == "CLOSED"


def test_closed_batch_pr_returns_branches_to_queue(env):
    env.branch("t_a", {"src/a.py": "a = 2\n"})
    env.queue()
    env.integ.batches.assemble(env.lane, env.policy)
    env.gh.prs[100]["state"] = "CLOSED"
    env.integ.batches.reconcile()
    assert env.integ._state("t_a")["status"] == "batch_pending"
    assert env.integ.batches.all()[0]["status"] == "closed"


def test_open_batch_is_not_duplicated(env):
    env.branch("t_a", {"src/a.py": "a = 2\n"})
    env.queue()
    env.integ.batches.assemble(env.lane, env.policy)
    assert "Ya hay un lote abierto" in env.integ.batches.assemble(env.lane, env.policy)
    assert len(env.gh.created) == 1


# --- hora y comando -----------------------------------------------------------------------------------------

def test_scheduled_time_assembles_once_per_day(env):
    env.branch("t_a", {"src/a.py": "a = 2\n"})
    env.integ.batches.clock = lambda: datetime(2026, 10, 1, 8, 29)
    env.integ.run_pass(force=True)
    assert env.gh.created == []                                  # aún no son las 08:30
    env.integ.batches.clock = lambda: datetime(2026, 10, 1, 8, 31)
    env.integ.run_pass(force=True)
    assert len(env.gh.created) == 1
    env.integ.batches.clock = lambda: datetime(2026, 10, 1, 18, 0)
    env.branch("t_late", {"src/b.py": "b = 2\n"})
    env.integ.run_pass(force=True)
    assert len(env.gh.created) == 1                              # un solo lote al día


def test_lote_command(env):
    assert parse_command("/lote migrateam") == ("lote", "migrateam")
    assert "lote" in dict(COMMANDS)
    env.branch("t_a", {"src/a.py": "a = 2\n"})
    env.queue()
    sent = []

    class N:
        def send_to(self, chat, thread, text, html=True, **kw):
            sent.append(text)

    cc = CommandCenter(N(), env.desk, lanes={"claude-oscarhq": env.lane}, hermes_for=lambda b: env.h,
                       messages=MessageStore(), owner_id=str(OSCAR))
    cc._spawn = lambda fn: fn()
    msg = lambda text, uid=OSCAR: {"text": text, "from": {"id": uid}, "chat": {"id": -100}}  # noqa: E731
    assert cc.handle(msg("/lote oscarhq"))
    assert any("Montando" in t for t in sent) and any("PR #100" in t for t in sent)
    sent.clear()
    cc.handle(msg("/lote nadie"))
    assert "Dime de qué proyecto" in sent[0]
    sent.clear()
    cc.handle(msg("/lote oscarhq", uid=1))
    assert sent == ["Solo Oscar"] and len(env.gh.created) == 1


# --- seguridad y prueba en seco -----------------------------------------------------------------------------

def test_push_is_limited_to_release_lote_branches(env):
    with pytest.raises(PermissionError):
        env.integ.batches._push(env.lane, "main")
    with pytest.raises(PermissionError):
        env.integ.batches._push(env.lane, "lane/t_a")
    with pytest.raises(PermissionError):
        env.integ._exec(["git", "push", "origin", "main"])  # el guard general no se ha aflojado


def test_dry_run_builds_without_push_pr_or_state(tmp_path):
    e = Env(tmp_path)
    n1 = e.branch("t_a", {"src/a.py": "a = 2\n"})
    n2 = e.branch("t_conf", {"src/a.py": "a = 3\n"})
    n3 = e.branch("t_b", {"src/b.py": "b = 2\n"})
    lines = []
    e.integ.dry_run, e.integ._out = True, lines.append
    assert e.integ.batches.dry_run(e.lane, e.policy, [n1, n2, n3]) == 0
    text = "\n".join(lines)
    assert "OK" in text and "incluye #11" in text and f"fuera #{n2}" in text and BATCH_LABEL in text
    assert e.gh.created == [] and e.remote_branches() == [] and e.integ.batches.all() == []
    assert sh(e.repo, "branch", "--list", "release/*") == ""


def test_ficha_fits_telegram_and_escapes():
    from agent_lanes.integration import risk_of
    rec = {"pr_number": 9, "batch_brand": "x", "batch_date": "2026-10-01", "batch_branch": "release/lote-x-2026-10-01",
           "batch_members": [[i, "<b>" + "t" * 80] for i in range(60)],
           "batch_excluded": [[70 + i, "y" * 50, "r" * 300] for i in range(30)], "gates": ["✔ tests OK"],
           "risks": [], "deploy_note": None}
    text = render_batch_ficha(risk=risk_of(None), phase="LISTO PARA INTEGRAR", rec=rec, status="s", repo="r", base="main")
    from agent_lanes.notices import TEXT_MAX
    assert len(text) <= TEXT_MAX and "<b><" not in text.replace("<b>LISTO", "") and "&lt;b&gt;" in text
