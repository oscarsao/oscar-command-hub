"""Carril Integrador (28-09): gates deterministas, botones solo de Oscar, merge con gh y deploy verificado.
Todo con mocks de git/gh/railway/hermes/Telegram: ningún test toca un repo, GitHub ni Railway reales."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from agent_lanes.config import Lane
from agent_lanes.decisions import NOT_OWNER, CallbackStore, DecisionDesk
from agent_lanes.hermes import OSCAR_AUTHOR
from agent_lanes.integrator import (INT_DEPLOY, INT_MERGE, INT_MERGE_DEPLOY, INTEGRATOR_AUTHOR, MIGRATEAM_WARNING,
                                    NEEDS_MIGRATION, Integrator, IntegratorSettings, Policy, alembic_heads,
                                    build_integrator, load_integrator_settings, parse_revision, scan_secrets)
from agent_lanes.notices import MessageStore

OSCAR = 6744452215
GH, RW = "gh.exe", "railway.exe"
HEAD, BASE, BASE2, MERGE = "a" * 40, "b" * 40, "c" * 40, "d" * 40
PROJECT, SERVICE, ENVIRONMENT = "p-1", "s-1", "e-1"
NEW_DEP = "11111111-2222-3333-4444-555555555555"
OLD_DEP = "99999999-2222-3333-4444-555555555555"
SECRET = "sk-ant-api03-" + "Z" * 40
OHQ_SLUG, MGT_SLUG = "oscarsao/oscar-hq", "PildoraDigital/OCR-PDF-and-images"


def diff(path, body, start=1):
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +{start} @@\n{body}\n"


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class World:
    """git + gh + railway + test_cmd simulados. `calls` guarda (argv, kwargs) de todo lo ejecutado."""

    def __init__(self, slug=OHQ_SLUG, tid="t_1"):
        self.calls = []
        self.slug = slug
        self.pr = {"number": 7, "url": f"https://github.com/{slug}/pull/7", "state": "OPEN", "isDraft": False,
                   "headRefName": f"lane/{tid}", "headRefOid": HEAD, "baseRefName": "master",
                   "isCrossRepository": False, "mergeCommit": None}
        self.base = BASE           # origin/master tras el fetch
        self.remote_base = BASE    # ls-remote (para detectar que la base se movió)
        self.conflict = False
        self.changed = ["src/app.py"]
        self.diff = diff("src/app.py", "+print('hola')", start=1)
        self.test_rc = 0
        self.alembic_files: dict[str, str] = {}
        self.merge_rc = 0
        self.linked = PROJECT
        self.deployments = [{"id": OLD_DEP, "status": "SUCCESS"}]
        self.after_up = [{"id": NEW_DEP, "status": "BUILDING"}, {"id": NEW_DEP, "status": "SUCCESS"}]
        self.up_out = f"Build Logs: https://railway.com/project/{PROJECT}/service/{SERVICE}?id={NEW_DEP}&"
        self.up_rc = 0
        self.upped = False
        self.not_ancestor: set[tuple[str, str]] = set()  # (viejo, nuevo) con merge-base --is-ancestor != 0
        self.between: list[str] = []  # archivos de `git diff --name-only <fusión> <punta>`

    def argv(self, name):
        return [a for a, _ in self.calls if a and (a[0] == name or (a[0] == "git" and name in a))]

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        if kw.get("shell"):
            return cp(self.test_rc, "", "tests rotos" if self.test_rc else "")
        if args[0] == "git":
            return self._git(args)
        if args[0] == GH:
            return self._gh(args)
        if args[0] == RW:
            return self._railway(args)
        raise AssertionError(args)

    def _git(self, args):
        if "fetch" in args:
            return cp()
        if "rev-parse" in args:
            ref = args[-1]
            return cp(0, (HEAD if ref.startswith("refs/integrator/") else self.base) + "\n")
        if "ls-remote" in args:
            return cp(0, f"{self.remote_base}\trefs/heads/master\n")
        if "merge-base" in args:
            return cp(1 if (args[-2], args[-1]) in self.not_ancestor else 0)
        if "rev-list" in args:
            return cp(0, "2\n")
        if "diff" in args and "--name-only" in args and len(args[-1]) == 40 and len(args[-2]) == 40:
            return cp(0, "\n".join(self.between) + "\n")
        if "worktree" in args and "add" in args:
            wt = Path(args[args.index("--detach") + 1])
            for name, src in self.alembic_files.items():
                (wt / "backend/alembic/versions").mkdir(parents=True, exist_ok=True)
                (wt / "backend/alembic/versions" / name).write_text(src, encoding="utf-8")
            wt.mkdir(parents=True, exist_ok=True)
            return cp()
        if "worktree" in args:
            return cp()
        if "merge" in args and "--abort" in args:
            return cp()
        if "merge" in args:
            return cp(1, "", "CONFLICT") if self.conflict else cp()
        if "--diff-filter=U" in args:
            return cp(0, "src/app.py\n")
        if "diff" in args and "--name-only" in args:
            return cp(0, "\n".join(self.changed) + "\n")
        if "diff" in args and "-U0" in args:
            return cp(0, self.diff)
        raise AssertionError(args)

    def _gh(self, args):
        if args[1:3] == ["pr", "view"]:
            return cp(0, json.dumps(self.pr))
        if args[1:3] == ["pr", "merge"]:
            if self.merge_rc == 0:
                self.pr = {**self.pr, "state": "MERGED", "mergeCommit": {"oid": MERGE}}
                self.base = self.remote_base = MERGE  # el squash queda en la punta de master
            return cp(self.merge_rc, "", "" if self.merge_rc == 0 else "boom")
        raise AssertionError(args)

    def _railway(self, args):
        if args[1:2] == ["status"]:
            return cp(0, json.dumps({"id": self.linked, "name": "Oscar HQ"}))
        if args[1:2] == ["up"]:
            self.upped = True
            return cp(self.up_rc, self.up_out)
        if args[1:3] == ["deployment", "list"]:
            if self.upped and self.after_up:
                state = self.after_up.pop(0) if len(self.after_up) > 1 else self.after_up[0]
                return cp(0, json.dumps([state, *self.deployments]))
            return cp(0, json.dumps(self.deployments))
        raise AssertionError(args)


class FakeHermes:
    def __init__(self, tasks, comments):
        self.tasks, self.comments_by = tasks, comments
        self.comments = []

    def list_status(self, assignee, status):
        return [t for t in self.tasks if t.get("assignee") == assignee and status == "done"]

    reviewed = HEAD

    def show(self, tid):
        runs = [{"metadata": {"branch": f"lane/{tid}", "head_sha": self.reviewed}}] if self.reviewed else []
        return {"task": {"id": tid}, "comments": list(self.comments_by.get(tid, [])), "runs": runs}

    def comment(self, tid, text, author=None):
        self.comments.append((tid, text, author))
        self.comments_by.setdefault(tid, []).append({"author": author, "body": text})
        return True


class FakeLinks:
    def __init__(self, slug):
        self.slug = slug

    def repo_slug(self, lane):
        return self.slug

    def __call__(self, lane, tid, *, branch=True, changed_files=None):
        return [("🗂 Tarjeta", f"https://k/{tid}")]


class FakeTg:
    bot_id = "42"

    def __init__(self):
        self.sent, self.edits, self.answers, self.markups = [], [], [], []
        self._n = 500

    def send(self, text, target=None, lane_target=None, *, silent=False, reply_markup=None):
        self._n += 1
        self.sent.append({"text": text, "markup": reply_markup, "silent": silent, "lane_target": lane_target})
        return {"chat_id": "-100", "thread_id": "231", "message_id": self._n}

    def edit(self, chat_id, message_id, text, reply_markup=None, **kw):
        self.edits.append({"text": text, "markup": reply_markup})
        return True

    def edit_markup(self, chat_id, message_id, markup):
        self.markups.append(markup)
        return True

    def delete(self, chat_id, message_id):
        return True

    def answer_callback(self, cid, text=None, *, alert=False):
        self.answers.append(text)
        return True

    @property
    def all_text(self):
        return "\n".join([m["text"] for m in self.sent] + [e["text"] for e in self.edits])


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def lane(name="claude-oscarhq", tmp=None):
    return Lane(name=name, board="oscarhq" if name == "claude-oscarhq" else "migrateam",
                repo=str(tmp / "repo") if tmp else "C:/repo", base="master",
                test_cmd='py -3.12 "{AGENT_LANES_DIR}/agent_lanes/checks.py" {BASE}', telegram=("-100", "231"))


OHQ_POLICY = Policy(lane="claude-oscarhq", deploy="railway_up", manual_migrations=("supabase/migrations/",),
                    railway_dir="C:/Users/oscar/dev/oscar-hq", railway_project=PROJECT, railway_service=SERVICE,
                    railway_environment=ENVIRONMENT, health_url="https://ohq/api/health")
MGT_POLICY = Policy(lane="claude-migrateam", deploy="on_merge", alembic_versions="backend/alembic/versions/",
                    health_url="https://mgt/health", health_commit_key="commit_sha")


def approved(slug=OHQ_SLUG, author=OSCAR_AUTHOR):
    return {"author": author, "body": f"APROBADO-OSCAR 2026-09-28 10:00 · PR https://github.com/{slug}/pull/7"}


def record(tmp_path, pr=7, head=HEAD, tid="t_1"):
    d = tmp_path / "state" / "approvals"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tid}.json").write_text(json.dumps({"task_id": tid, "pr": pr, "head_sha": head}), encoding="utf-8")


def make(tmp_path, *, name="claude-oscarhq", comments=None, desk=True, enabled=True, dry_run=False, world=None,
         health=None, out=None, approve=True):
    if approve:
        record(tmp_path)
    slug = OHQ_SLUG if name == "claude-oscarhq" else MGT_SLUG
    w = world or World(slug)
    ln = lane(name, tmp_path)
    policy = OHQ_POLICY if name == "claude-oscarhq" else MGT_POLICY
    settings = IntegratorSettings(enabled=enabled, interval_seconds=300, worktree_root=str(tmp_path / "wt"),
                                  deploy_timeout_seconds=900, poll_seconds=20, policies={name: policy})
    h = FakeHermes([{"id": "t_1", "title": "Arreglar login", "assignee": name, "body": "## Objetivo\nLogin"}],
                   {"t_1": comments if comments is not None else [approved(slug)]})
    tg = FakeTg()
    clock = Clock()
    d = None
    if desk:
        d = DecisionDesk(tg, CallbackStore(tmp_path / "cb"), lanes={name: ln}, hermes_for=lambda b: h,
                         links=FakeLinks(slug), owner_id=str(OSCAR), runner=w, spawn=lambda fn: fn())
    integ = Integrator(settings, {name: ln}, hermes_for=lambda b: h, links=FakeLinks(slug), notifier=tg,
                       messages=MessageStore(), desk=d, runner=w, gh_exe=GH, railway_exe=RW, clock=clock,
                       sleep=clock.sleep, http_get=health or (lambda url: (200, "{}")),
                       state_dir=tmp_path / "state", dry_run=dry_run, out=out or (lambda s: None))
    if d:
        d.integrator = integ
    return integ, w, h, tg, d, clock


def press(desk, tg, *, n=0, user=OSCAR, markup=None):
    markup = markup or next(m["markup"] for m in reversed(tg.sent + tg.edits) if m.get("markup"))
    data = markup["inline_keyboard"][0][n]["callback_data"]
    desk.handle_update({"callback_query": {"id": "cq", "from": {"id": user}, "data": data,
                                           "message": {"chat": {"id": -100}, "message_id": 555,
                                                       "message_thread_id": 231}}})


def buttons(markup):
    return [b["text"] for row in (markup or {}).get("inline_keyboard", []) for b in row]


# --- activación ------------------------------------------------------------------------------------------

def test_disabled_by_default_and_build_returns_none(monkeypatch):
    monkeypatch.delenv("INTEGRATOR_ENABLED", raising=False)
    assert load_integrator_settings(env={}).enabled is False
    assert build_integrator({}, {}) is None
    assert build_integrator({"INTEGRATOR_ENABLED": "false"}, {}) is None
    assert isinstance(build_integrator({"INTEGRATOR_ENABLED": "true"}, {}, hermes_for=lambda b: None), Integrator)


def test_real_lanes_yaml_policies():
    s = load_integrator_settings(env={})
    assert s.policies["claude-oscarhq"].deploy == "railway_up"
    assert s.policies["claude-migrateam"].deploy == "on_merge"
    assert s.policies["claude-migrateam"].alembic_versions == "backend/alembic/versions/"
    assert "supabase/migrations/" in s.policies["claude-oscarhq"].manual_migrations
    assert s.deploy_timeout_seconds == 900
    assert "claude-scraper" not in s.policies  # solo carriles con política explícita


def test_without_enabled_it_does_nothing(tmp_path):
    integ, w, h, tg, _, _ = make(tmp_path, enabled=False)
    assert integ.run_pass(force=True) == {}
    assert w.calls == [] and h.comments == [] and tg.sent == []


def test_disabled_integrator_ignores_buttons(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.run_pass()
    integ.settings = IntegratorSettings(enabled=False, policies=integ.settings.policies,
                                        worktree_root=integ.settings.worktree_root)
    before = len(w.argv(GH))
    press(d, tg)
    assert not [a for a in w.argv(GH)[before:] if "merge" in a]
    assert tg.markups  # vuelven los botones


# --- detección de tarjetas ----------------------------------------------------------------------------

@pytest.mark.parametrize("comments", [[], [approved(author="lane-review")], [approved(slug="otro/repo")],
                                      [approved(), {"author": INTEGRATOR_AUTHOR, "body": "INTEGRADO abc · PR x"}]])
def test_cards_that_are_not_integrated(tmp_path, comments):
    integ, w, h, tg, _, _ = make(tmp_path, comments=comments)
    assert integ.run_pass() == {}
    assert not w.argv("merge") and tg.sent == []


def test_pr_not_open_or_wrong_branch_is_skipped(tmp_path):
    w = World()
    w.pr["headRefName"] = "feature/x"
    integ, *_ = make(tmp_path, world=w)
    assert integ.run_pass() == {}
    assert not w.argv("merge")


# --- gates --------------------------------------------------------------------------------------------

def test_oscarhq_all_gates_pass_offers_merge_button_only(tmp_path):
    integ, w, h, tg, _, _ = make(tmp_path)
    assert integ.run_pass() == {"t_1": "offered"}
    msg = tg.sent[-1]
    assert "🚦 Listo para integrar PR #7" in msg["text"]
    assert buttons(msg["markup"]) == ["🔀 Fusionar"]
    assert "✔ sin conflictos con master" in msg["text"] and "✔ tests OK" in msg["text"]
    assert "✔ sin secretos" in msg["text"] and "fusionar NO despliega" in msg["text"]
    assert "PR #7" in msg["text"] and msg["lane_target"] == ("-100", "231")
    assert any(a == INTEGRATOR_AUTHOR and "gates OK" in t for _, t, a in h.comments)
    # el merge de prueba neutraliza los hooks del repo (MigraTeam: core.hooksPath=.githooks)
    merge = next(a for a in w.argv("merge") if "--no-ff" in a)
    assert any(x.startswith("core.hooksPath=") and ".githooks" not in x for x in merge)
    # nunca se empuja nada y el worktree temporal se borra
    assert not [a for a, _ in w.calls if a[0] == "git" and "push" in a]
    assert any("remove" in a and "--force" in a and a[-1].endswith("int-t_1") for a in w.argv("worktree"))


def test_same_head_does_not_rerun_gates(tmp_path):
    integ, w, *_ = make(tmp_path)
    integ.run_pass()
    n = len(w.argv("merge"))
    assert integ.run_pass(force=True) == {}
    assert len(w.argv("merge")) == n


def test_throttle_between_passes(tmp_path):
    integ, w, h, *_ = make(tmp_path, comments=[])
    integ.run_pass()
    integ.run_pass()
    assert h.comments == []  # sin aprobación: no hay nada
    assert integ._last_pass == 0.0


def test_conflict_blocks_without_running_tests(tmp_path):
    w = World()
    w.conflict = True
    integ, w, h, tg, _, _ = make(tmp_path, world=w)
    assert integ.run_pass() == {"t_1": "gates_failed"}
    assert "⛔ no se puede integrar PR #7" in tg.sent[-1]["text"] and "conflictos" in tg.sent[-1]["text"]
    assert tg.sent[-1]["markup"] is None
    assert not [c for c in w.calls if c[1].get("shell")]
    assert any("gates fallidos" in t for _, t, _ in h.comments)


def test_failing_tests_block(tmp_path):
    w = World()
    w.test_rc = 1
    integ, w, h, tg, _, _ = make(tmp_path, world=w)
    assert integ.run_pass() == {"t_1": "gates_failed"}
    assert "los tests del carril fallan" in tg.sent[-1]["text"]
    shell = next(c for c in w.calls if c[1].get("shell"))
    assert "checks.py" in shell[0] and "origin/master" in shell[0] and shell[1]["cwd"].endswith("int-t_1")


def test_secret_blocks_and_value_never_leaks(tmp_path):
    w = World()
    w.diff = diff("src/cfg.py", f"+x = 1\n+KEY = '{SECRET}'", start=4)
    integ, w, h, tg, _, _ = make(tmp_path, world=w)
    assert integ.run_pass() == {"t_1": "gates_failed"}
    assert "src/cfg.py:5 (clave sk-)" in tg.sent[-1]["text"]
    assert SECRET not in tg.all_text and "Z" * 20 not in tg.all_text
    assert all(SECRET not in t for _, t, _ in h.comments)


@pytest.mark.parametrize("line,rule", [
    (f"k = '{SECRET}'", "clave sk-"), ("t = 'ghp_" + "a" * 36 + "'", "token de GitHub"),
    ("DB=postgresql://postgres:hunter2@db.x.supabase.co:5432/postgres", "URL de base de datos con contraseña"),
    ("jwt = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.abcdefghijklmnop'",
     "JWT largo (eyJ…)"),
    ("SUPABASE_SERVICE_ROLE_KEY=abcdefghijklmnopqrstuvwxyz123456", "service_role con valor"),
    ("serviceRoleKey = 'abcdefghijklmnopqrstuvwxyz123456'", "service_role con valor"),
    ("STRIPE=sk_live_" + "a1" * 15, "clave de Stripe live"), ("id=AKIAABCDEFGHIJKLMNOP", "clave de AWS"),
    ("BOT=123456789:AA" + "b" * 33, "token de bot de Telegram"),
    ("DB=postgresql+psycopg://app:pw@db:5432/x", "URL de base de datos con contraseña"),
    ("-----BEGIN RSA PRIVATE KEY-----", "clave privada")])
def test_secret_rules_detect(line, rule):
    assert scan_secrets(diff("f.env", f"+{line}")) == [("f.env", 1, rule)]


@pytest.mark.parametrize("line", ["task-sk-list = 1", "sk-short", "key = os.environ['SERVICE_ROLE_KEY']",
                                  "service_role_key = os.getenv('X')", "postgres://localhost/db",
                                  "url = 'postgresql://user@host/db'"])
def test_secret_rules_ignore_harmless(line):
    assert scan_secrets(diff("f.py", f"+{line}")) == []


def test_secret_scan_ignores_removed_lines():
    assert scan_secrets(diff("f.py", f"-k = '{SECRET}'")) == []


# --- migraciones ---------------------------------------------------------------------------------------

def mig(rev, down):
    return f'"""m"""\nrevision = {rev!r}\ndown_revision = {down!r}\n'


def test_parse_revision_variants():
    assert parse_revision(mig("a1", None)) == ("a1", ())
    assert parse_revision('revision: str = "b2"\ndown_revision: Union[str, None] = "a1"\n') == ("b2", ("a1",))
    assert parse_revision("revision = 'm'\ndown_revision = ('a', 'b')\n") == ("m", ("a", "b"))


def test_alembic_heads_static(tmp_path):
    (tmp_path / "1.py").write_text(mig("a", None))
    (tmp_path / "2.py").write_text(mig("b", "a"))
    assert alembic_heads(tmp_path) == ({"b"}, [])
    (tmp_path / "3.py").write_text(mig("c", "a"))
    assert alembic_heads(tmp_path)[0] == {"b", "c"}
    (tmp_path / "4.py").write_text(mig("c", "b"))
    assert any("duplicada" in p for p in alembic_heads(tmp_path)[1])


def test_migrateam_two_alembic_heads_block(tmp_path):
    w = World(MGT_SLUG)
    w.changed = ["backend/alembic/versions/2.py"]
    w.alembic_files = {"1.py": mig("a", None), "2.py": mig("b", "a"), "3.py": mig("c", "a")}
    integ, w, h, tg, _, _ = make(tmp_path, name="claude-migrateam", world=w)
    assert integ.run_pass() == {"t_1": "gates_failed"}
    assert "Alembic inconsistentes (2 heads)" in tg.sent[-1]["text"]


def test_migrateam_with_migration_has_no_button(tmp_path):
    w = World(MGT_SLUG)
    w.changed = ["backend/alembic/versions/2.py", "backend/models.py"]
    w.alembic_files = {"1.py": mig("a", None), "2.py": mig("b", "a")}
    integ, w, h, tg, _, _ = make(tmp_path, name="claude-migrateam", world=w)
    assert integ.run_pass() == {"t_1": "offered"}
    msg = tg.sent[-1]
    assert msg["markup"] is None and NEEDS_MIGRATION in msg["text"]
    assert "✔ Alembic con un solo head" in msg["text"] and "requiere migración de Alembic" in msg["text"]


def test_migrateam_always_explicit_deploy_button_and_warning(tmp_path):
    integ, w, h, tg, _, _ = make(tmp_path, name="claude-migrateam", world=World(MGT_SLUG))
    integ.run_pass()
    assert buttons(tg.sent[-1]["markup"]) == ["🚀 Fusionar y desplegar a producción"]
    assert MIGRATEAM_WARNING in tg.sent[-1]["text"]


def test_migrateam_rejects_plain_merge_action(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path, name="claude-migrateam", world=World(MGT_SLUG))
    integ.run_pass()
    rec = {"kind": "integrate", "task_id": "t_1", "lane": "claude-migrateam", "pr_number": 7, "head_sha": HEAD,
           "base_sha": BASE, "pr_url": "u", "migration": None}
    assert integ.on_button(INT_MERGE, rec, {"chat_id": "-100", "message_id": 1}, d) is False
    assert integ.on_button(INT_MERGE_DEPLOY, {**rec, "migration": "alembic"}, {}, d) is False
    assert not [a for a in w.argv(GH) if "merge" in a]


def test_supabase_migration_allows_merge_but_never_deploy(tmp_path):
    w = World()
    w.changed = ["supabase/migrations/20260928_x.sql"]
    integ, w, h, tg, d, _ = make(tmp_path, world=w)
    integ.run_pass()
    assert buttons(tg.sent[-1]["markup"]) == ["🔀 Fusionar"]
    assert "requiere migración manual" in tg.sent[-1]["text"]
    press(d, tg)
    assert "✅ fusionado · ddddddd" in tg.edits[-1]["text"] and NEEDS_MIGRATION in tg.edits[-1]["text"]
    assert tg.edits[-1]["markup"] is None  # sin 🚀 Desplegar


# --- botones y merge -----------------------------------------------------------------------------------

def test_button_from_other_user_does_nothing(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.run_pass()
    n = len(w.calls)
    press(d, tg, user=12345)
    assert tg.answers[-1] == NOT_OWNER
    assert len(w.calls) == n and not any(t.startswith("INTEGRADO ") for _, t, _ in h.comments)


def test_merge_button_squash_with_match_head_and_no_forbidden_flags(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.run_pass()
    press(d, tg)
    merge = next(a for a in w.argv(GH) if a[1:3] == ["pr", "merge"])
    assert merge[3] == "7" and "--squash" in merge and merge[merge.index("--match-head-commit") + 1] == HEAD
    assert "--delete-branch" in merge and merge[merge.index("--repo") + 1] == OHQ_SLUG
    for a, kw in w.calls:
        if isinstance(a, list) and a[0] == GH:
            assert not {"--force", "--admin", "--auto"} & set(a)
            assert kw["cwd"] == tempfile.gettempdir()  # nunca dentro de un clon (--delete-branch)
        elif isinstance(a, list) and "--force" in a:  # solo `worktree remove --force` de los temporales propios
            assert a[3:5] == ["worktree", "remove"] and Path(a[-1]).name.startswith(("int-", "intdep-"))
        assert not (isinstance(a, list) and a[0] == "git" and "push" in a)
    assert (("t_1", f"INTEGRADO {MERGE} · PR https://github.com/{OHQ_SLUG}/pull/7", INTEGRATOR_AUTHOR)
            in h.comments)
    assert "✅ fusionado · ddddddd" in tg.edits[-1]["text"]
    assert buttons(tg.edits[-1]["markup"]) == ["🚀 Desplegar"]
    assert not [a for a in w.argv(RW) if "up" in a]  # fusionar no despliega


def test_double_press_merges_once(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.run_pass()
    markup = tg.sent[-1]["markup"]
    press(d, tg, markup=markup)
    press(d, tg, markup=markup)
    assert len([a for a in w.argv(GH) if a[1:3] == ["pr", "merge"]]) == 1


def test_merge_refused_if_pr_head_changed(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.run_pass()
    w.pr["headRefOid"] = "e" * 40
    press(d, tg)
    assert not [a for a in w.argv(GH) if a[1:3] == ["pr", "merge"]]
    assert "commits nuevos" in tg.edits[-1]["text"]
    assert integ._state("t_1")["status"] == "stale"


def test_merge_reruns_gates_when_base_moved(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.run_pass()
    n = len([a for a in w.argv("merge") if "--no-ff" in a])
    w.remote_base = w.base = BASE2
    w.test_rc = 1
    press(d, tg)
    assert len([a for a in w.argv("merge") if "--no-ff" in a]) == n + 1
    assert not [a for a in w.argv(GH) if a[1:3] == ["pr", "merge"]]
    assert "⛔ no fusionado" in tg.edits[-1]["text"]


def test_merge_after_base_moved_and_gates_ok(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.run_pass()
    w.remote_base = w.base = BASE2
    press(d, tg)
    assert [a for a in w.argv(GH) if a[1:3] == ["pr", "merge"]]


def test_github_merge_failure_restores_button(tmp_path):
    w = World()
    w.merge_rc = 1
    integ, w, h, tg, d, _ = make(tmp_path, world=w)
    integ.run_pass()
    press(d, tg)
    assert not any(t.startswith("INTEGRADO ") for _, t, _ in h.comments)
    assert buttons(tg.markups[-1]) == ["🔀 Fusionar"]


def test_gh_forbidden_flags_and_git_push_raise(tmp_path):
    integ, *_ = make(tmp_path)
    with pytest.raises(PermissionError):
        integ._gh("pr", "merge", "7", "--admin")
    with pytest.raises(PermissionError):
        integ._git("C:/repo", "push", "origin", "master")


# --- deploy Oscar HQ -----------------------------------------------------------------------------------

def merged_and_press_deploy(tmp_path, world=None, health=None):
    integ, w, h, tg, d, clock = make(tmp_path, world=world, health=health)
    integ.run_pass()
    press(d, tg)
    press(d, tg)  # 🚀 Desplegar
    return integ, w, h, tg, d, clock


def test_deploy_railway_up_polls_the_new_deployment(tmp_path):
    integ, w, h, tg, d, clock = merged_and_press_deploy(tmp_path)
    up = next(a for a in w.argv(RW) if a[1] == "up")
    assert "--detach" in up and "-y" not in up and "--new" not in up
    assert up[up.index("-p") + 1] == PROJECT and up[up.index("-s") + 1] == SERVICE
    assert up[up.index("-e") + 1] == ENVIRONMENT
    up_kw = next(kw for a, kw in w.calls if a and a[0] == RW and a[1] == "up")
    assert str(up_kw["cwd"]).replace("\\", "/").endswith("intdep-t_1")  # desde el worktree limpio del merge
    assert "🚀 desplegado · ddddddd" in tg.edits[-1]["text"]
    assert any(t.startswith(f"DESPLEGADO {MERGE} · railway deployment {NEW_DEP} SUCCESS") for _, t, _ in h.comments)
    assert clock.t > 0  # pasó por BUILDING antes de SUCCESS: no se quedó con el SUCCESS viejo


def test_old_success_is_never_taken_as_the_new_deploy(tmp_path):
    w = World()
    w.after_up = [{"id": NEW_DEP, "status": "BUILDING"}]  # el nuevo nunca termina; el viejo sigue en SUCCESS
    integ, w, h, tg, d, clock = merged_and_press_deploy(tmp_path, world=w)
    assert "⛔ deploy falló · sin terminar en 15 min" in tg.edits[-1]["text"]
    assert clock.t >= 900


@pytest.mark.parametrize("status", ["FAILED", "CRASHED", "REMOVED", "SKIPPED"])
def test_deploy_failure_statuses(tmp_path, status):
    w = World()
    w.after_up = [{"id": NEW_DEP, "status": status}]
    integ, w, h, tg, d, _ = merged_and_press_deploy(tmp_path, world=w)
    assert f"⛔ deploy falló · {status}" in tg.edits[-1]["text"]
    assert any(t.startswith("DEPLOY-FALLIDO") for _, t, _ in h.comments)


def test_deploy_id_from_list_diff_when_output_has_none(tmp_path):
    w = World()
    w.up_out = "Uploading..."
    w.after_up = [{"id": NEW_DEP, "status": "SUCCESS"}]
    integ, w, h, tg, d, _ = merged_and_press_deploy(tmp_path, world=w)
    assert "🚀 desplegado" in tg.edits[-1]["text"]


def test_deploy_refused_if_cli_not_linked_to_project(tmp_path):
    w = World()
    w.linked = "otro-proyecto"
    integ, w, h, tg, d, _ = merged_and_press_deploy(tmp_path, world=w)
    assert not [a for a in w.argv(RW) if a[1] == "up"]
    assert "no está enlazada" in tg.edits[-1]["text"] and buttons(tg.markups[-1]) == ["🚀 Desplegar"]


def _worktree_sha(w):
    return next(a[-1] for a, _ in w.calls if a and a[0] == "git" and "worktree" in a and "add" in a
                and str(a[a.index("--detach") + 1]).replace("\\", "/").endswith("intdep-t_1"))


def test_deploy_takes_the_tip_of_the_base_not_the_merge_of_the_card(tmp_path):
    """28-09: 🚀 de a50b290 dejó fuera la fusión posterior 6f46b0d (/leads). Ahora se despliega la punta."""
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.run_pass()
    press(d, tg)
    w.base = BASE2  # otra fusión en master después de la de esta ficha
    press(d, tg)
    assert _worktree_sha(w) == BASE2
    assert "🚀 desplegado · ccccccc" in tg.edits[-1]["text"]
    assert any(t.startswith(f"DESPLEGADO {BASE2} · railway deployment {NEW_DEP} SUCCESS") and MERGE[:12] in t
               for _, t, _ in h.comments)
    assert integ._state("t_1")["deployed_sha"] == BASE2


def _merged_card(tmp_path, integ, tid="t_2", sha=BASE2, pr=8, migration=None):
    integ._save(tid, status="merged", lane="claude-oscarhq", merge_sha=sha, pr=pr,
                pr_url=f"https://github.com/{OHQ_SLUG}/pull/{pr}", title="Leads", migration=migration)
    integ._notices.store.put(tid, {"chat_id": "-100", "thread_id": "398", "message_id": 900, "token": "tok2"})


def test_deploy_marks_every_merged_card_contained_in_the_tip_as_deployed(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    _merged_card(tmp_path, integ)
    (tmp_path / "cb").mkdir(exist_ok=True)
    (tmp_path / "cb" / "tok2.json").write_text("{}", encoding="utf-8")  # su 🚀 Desplegar pendiente
    integ.run_pass()
    press(d, tg)
    w.base = BASE2
    press(d, tg)
    assert integ._state("t_2")["status"] == "deployed" and integ._state("t_2")["deployed_sha"] == BASE2
    assert any(tid == "t_2" and t.startswith(f"DESPLEGADO {BASE2}") and "t_1" in t for tid, t, _ in h.comments)
    assert any("🚀 desplegado · ccccccc" in e["text"] and "con el deploy de t_1" in e["text"] for e in tg.edits)
    assert not (tmp_path / "cb" / "tok2.json").exists()  # botón retirado
    assert "también PR #8" in tg.edits[-1]["text"]


def test_merged_card_not_in_the_tip_is_left_alone(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    _merged_card(tmp_path, integ, sha="e" * 40)
    w.not_ancestor.add(("e" * 40, BASE2))
    integ.run_pass()
    press(d, tg)
    w.base = BASE2
    press(d, tg)
    assert integ._state("t_2")["status"] == "merged"
    assert "también PR" not in tg.edits[-1]["text"]


def test_deploy_refused_if_the_tip_includes_a_merge_with_pending_migration(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    _merged_card(tmp_path, integ, migration="supabase")
    integ.run_pass()
    press(d, tg)
    w.base = BASE2
    press(d, tg)
    assert not [a for a in w.argv(RW) if a[1] == "up"]
    assert "PR #8 con migración" in tg.edits[-1]["text"]


def test_deploy_refused_if_commits_after_the_merge_touch_migrations_or_infra(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.run_pass()
    press(d, tg)
    w.base = BASE2
    w.between = ["supabase/migrations/20260928_x.sql"]
    press(d, tg)
    assert not [a for a in w.argv(RW) if a[1] == "up"]
    assert "trae migración o infraestructura" in tg.edits[-1]["text"]


def test_deploy_refused_if_the_base_no_longer_contains_the_merge(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.run_pass()
    press(d, tg)
    w.base = BASE2
    w.not_ancestor.add((MERGE, BASE2))  # force-push / revert en master
    press(d, tg)
    assert not [a for a in w.argv(RW) if a[1] == "up"]
    assert "ya no contiene esta fusión" in tg.edits[-1]["text"]


def test_busy_reports_deploy_and_gates_locks(tmp_path):
    integ, *_ = make(tmp_path)
    assert integ.busy() == []
    with integ._deploying:
        assert integ.busy() == ["deploy en curso"]


# --- MigraTeam: merge = deploy -------------------------------------------------------------------------

def test_migrateam_merge_and_verify_health_commit(tmp_path):
    answers = iter([(200, json.dumps({"status": "healthy", "commit_sha": BASE})),
                    (502, ""),
                    (200, json.dumps({"status": "healthy", "commit_sha": MERGE}))])
    integ, w, h, tg, d, clock = make(tmp_path, name="claude-migrateam", world=World(MGT_SLUG),
                                     health=lambda url: next(answers))
    integ.run_pass()
    press(d, tg)
    assert [a for a in w.argv(GH) if a[1:3] == ["pr", "merge"]]
    assert not w.argv(RW)  # CLI de Railway no enlazada para MigraTeam: nunca se usa
    assert "🚀 desplegado · ddddddd · health OK" in tg.edits[-1]["text"]
    assert clock.t == 40  # dos respuestas que no valen (contenedor viejo, 502)


def test_migrateam_health_with_old_commit_times_out(tmp_path):
    integ, w, h, tg, d, clock = make(tmp_path, name="claude-migrateam", world=World(MGT_SLUG),
                                     health=lambda url: (200, json.dumps({"status": "healthy", "commit_sha": BASE})))
    integ.run_pass()
    press(d, tg)
    assert "⛔ deploy sin confirmar en 15 min" in tg.edits[-1]["text"]
    assert any(t.startswith("DEPLOY-SIN-CONFIRMAR") for _, t, _ in h.comments)


# --- sin bot / en seco / sin integrador ----------------------------------------------------------------

def test_without_lanes_bot_notifies_without_buttons(tmp_path):
    integ, w, h, tg, _, _ = make(tmp_path, desk=False)
    assert integ.run_pass() == {"t_1": "offered"}
    assert tg.sent[-1]["markup"] is None and "fusiona a mano" in tg.sent[-1]["text"]
    assert not [a for a in w.argv(GH) if a[1:3] == ["pr", "merge"]]


def test_dry_run_writes_nothing(tmp_path):
    lines = []
    integ, w, h, tg, _, _ = make(tmp_path, enabled=False, dry_run=True, out=lines.append)
    assert integ.run_pass() == {"t_1": "dry-run:ok"}
    assert h.comments == [] and tg.sent == [] and not (tmp_path / "state" / "t_1.json").exists()
    assert not [a for a in w.argv(GH) if a[1:3] == ["pr", "merge"]]
    assert any("GATES OK" in l for l in lines) and any("🔀 Fusionar" in l for l in lines)


def test_desk_without_integrator_ignores_int_buttons(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.run_pass()
    d.integrator = None
    press(d, tg)
    assert not [a for a in w.argv(GH) if a[1:3] == ["pr", "merge"]]


# --- hallazgos del security-auditor (28-09) --------------------------------------------------------------

def test_forged_approval_comment_without_local_record_is_ignored(tmp_path):
    integ, w, h, tg, _, _ = make(tmp_path, approve=False)
    assert integ.run_pass() == {}
    assert not w.argv("merge") and tg.sent == []
    assert "sin registro local" in integ._state("t_1")["pr_problem"]


def test_commits_after_approval_are_not_integrated(tmp_path):
    w = World()
    w.pr["headRefOid"] = "e" * 40  # el worker empujó después del ✅
    integ, w, h, tg, _, _ = make(tmp_path, world=w)
    assert integ.run_pass() == {}
    assert not w.argv("merge") and integ._state("t_1")["pr_problem"] == "hay commits después de tu aprobación"


def test_pr_head_must_be_the_reviewed_commit(tmp_path):
    integ, w, h, tg, _, _ = make(tmp_path)
    h.reviewed = "f" * 40
    assert integ.run_pass() == {}
    assert "revisó el carril review" in integ._state("t_1")["pr_problem"]


def test_record_approval_from_desk_approve(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path, approve=False)
    assert integ.record_approval(integ.lanes["claude-oscarhq"], "t_1", 7)
    assert integ._approved_record("t_1")["head_sha"] == HEAD
    assert not integ.record_approval(integ.lanes["claude-oscarhq"], "../x", 7)


@pytest.mark.parametrize("path", ["py.bat", "tools/git.exe", "run.cmd", "scripts/argparse.py", "scripts/json.py"])
def test_executables_and_stdlib_shadowing_block_before_tests(tmp_path, path):
    w = World()
    w.changed = [path]
    integ, w, h, tg, _, _ = make(tmp_path, world=w)
    assert integ.run_pass() == {"t_1": "gates_failed"}
    assert "suplantan" in tg.sent[-1]["text"]
    assert not [c for c in w.calls if c[1].get("shell")]  # test_cmd nunca se ejecuta


def test_forbidden_paths_of_the_lane_block(tmp_path):
    w = World()
    w.changed = [".github/workflows/deploy.yml"]
    integ, w, h, tg, _, _ = make(tmp_path, world=w)
    integ.lanes["claude-oscarhq"] = Lane(**{**integ.lanes["claude-oscarhq"].__dict__,
                                            "forbidden_paths": (".github/workflows/",)})
    assert integ.run_pass() == {"t_1": "gates_failed"}
    assert "rutas vetadas" in tg.sent[-1]["text"]


def test_env_file_and_gitattributes_block(tmp_path):
    w = World()
    w.changed = ["backend/.env.production"]
    integ, w, h, tg, _, _ = make(tmp_path, world=w)
    assert integ.run_pass() == {"t_1": "gates_failed"}


def test_sensitive_infra_in_migrateam_has_no_button(tmp_path):
    w = World(MGT_SLUG)
    w.changed = ["backend/Procfile"]
    integ, w, h, tg, _, _ = make(tmp_path, name="claude-migrateam", world=w)
    integ.settings.policies["claude-migrateam"] = Policy(**{**MGT_POLICY.__dict__,
                                                           "sensitive_paths": ("backend/Procfile",)})
    assert integ.run_pass() == {"t_1": "offered"}
    assert tg.sent[-1]["markup"] is None and "toca infraestructura de deploy" in tg.sent[-1]["text"]


def test_binary_file_is_flagged_by_secret_scan():
    d = "diff --git a/x.sqlite b/x.sqlite\nnew file mode 100644\nBinary files /dev/null and b/x.sqlite differ\n"
    assert scan_secrets(d) == [("x.sqlite", 0, "archivo binario sin revisar")]


def test_added_line_that_looks_like_header_does_not_blind_the_scan():
    body = f"+++ /dev/null\n+k = '{SECRET}'"
    assert scan_secrets(diff("a.py", body))[0][2] == "clave sk-"


def test_secret_scan_diff_flags(tmp_path):
    integ, w, *_ = make(tmp_path)
    integ.run_pass()
    scan = next(a for a in w.argv("diff") if "-U0" in a)
    assert {"--text", "--no-textconv", "--no-ext-diff", "--no-color"} <= set(scan)


def test_test_cmd_runs_with_minimal_env_without_cwd_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("CARRILES_BOT_TOKEN", "123:secreto")
    integ, w, *_ = make(tmp_path)
    integ.run_pass()
    env = next(c[1]["env"] for c in w.calls if c[1].get("shell"))
    assert env["NoDefaultCurrentDirectoryInExePath"] == "1" and "CARRILES_BOT_TOKEN" not in env


def test_test_output_never_reaches_the_card(tmp_path):
    w = World()
    w.test_rc = 1
    integ, w, h, tg, _, _ = make(tmp_path, world=w)
    integ.run_pass()
    assert all("tests rotos" not in t for _, t, _ in h.comments)


def test_all_commands_have_closed_stdin(tmp_path):
    integ, w, *_ = make(tmp_path)
    integ.run_pass()
    assert all(kw.get("stdin") == subprocess.DEVNULL for _, kw in w.calls)


def test_worktree_hooks_neutralised_on_add_and_abort(tmp_path):
    w = World()
    w.conflict = True
    integ, w, *_ = make(tmp_path, world=w)
    integ.run_pass()
    for a in [a for a in w.argv("worktree") if "add" in a] + [a for a in w.argv("merge") if "--abort" in a]:
        assert any(x.startswith("core.hooksPath=") for x in a)


def test_foreign_worktree_is_never_removed(tmp_path):
    integ, *_ = make(tmp_path)
    with pytest.raises(PermissionError):
        integ._remove_worktree("C:/repo", Path(integ.settings.worktree_root) / "lane-t_1")
    with pytest.raises(PermissionError):
        integ._remove_worktree("C:/repo", tmp_path / "otro" / "int-t_1")


def test_health_url_must_be_https(tmp_path):
    y = tmp_path / "l.yaml"
    y.write_text("lanes: {}\nintegrator:\n  lanes:\n    x: {deploy: on_merge, health_url: 'http://h/health'}\n")
    with pytest.raises(ValueError):
        load_integrator_settings(y, env={})


def test_merge_aborted_if_base_moves_again_during_regates(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.run_pass()
    w.remote_base = BASE2  # ls-remote ve una base nueva, pero el fetch de los gates repetidos ve otra
    w.base = BASE2
    orig = integ.run_gates

    def regates(*a, **kw):
        r = orig(*a, **kw)
        w.remote_base = "9" * 40  # alguien empuja a master mientras se repetían
        return r
    integ.run_gates = regates
    press(d, tg)
    assert not [a for a in w.argv(GH) if a[1:3] == ["pr", "merge"]]
    assert "cambió durante la comprobación" in tg.edits[-1]["text"]


def test_second_deploy_while_one_runs_is_refused(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.run_pass()
    press(d, tg)
    integ._deploying.acquire()
    try:
        press(d, tg)
    finally:
        integ._deploying.release()
    assert not [a for a in w.argv(RW) if a[1] == "up"]
    assert "ya hay un deploy en curso" in tg.edits[-1]["text"]


def test_integrator_lanes_filter_limits_policies():
    s = load_integrator_settings(env={"INTEGRATOR_LANES": "claude-oscarhq"})
    assert list(s.policies) == ["claude-oscarhq"]


def test_migrateam_goes_through_develop_never_master():
    # 28-09 (Oscar): en MigraTeam todo entra por develop (staging); master solo por release/<fecha>.
    from agent_lanes.config import load_lanes
    lane = load_lanes()["claude-migrateam"]
    assert lane.base == "develop"
    p = load_integrator_settings(env={}).policies["claude-migrateam"]
    assert "develop" in p.merge_label and "producción" not in p.merge_label
    assert "staging" in p.health_url and "production" not in p.health_url
