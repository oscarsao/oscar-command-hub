"""Promover MigraTeam a producción (01-10): doble confirmación, firma, caducidad y verificación por /health.
Todo con dobles de gh, HTTP y Telegram: ningún test toca GitHub, producción ni Telegram."""
from __future__ import annotations

import json
import subprocess

import pytest

from agent_lanes.decisions import CallbackStore
from agent_lanes.promote import (PROMO_CONFIRM, PROMO_REVIEW, PromoteConfig, Promoter, load_promote_configs,
                                 STEP_TTL)

REPO = "PildoraDigital/OCR-PDF-and-images"
HEAD, MERGE = "a" * 40, "b" * 40
GH = "gh.exe"
VERS = "backend/alembic/versions"
WHERE = {"chat_id": "-100", "thread_id": "0", "message_id": 9}


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def migration(rev, down, doc="") -> str:
    return f'"""{doc}"""\nrevision = "{rev}"\ndown_revision = {down!r}\n'


class FakeGH:
    def __init__(self):
        self.calls = []
        self.head = HEAD
        self.required = ["lint", "tests", "build", "e2e"]
        self.rollup = [{"name": n, "conclusion": "SUCCESS"} for n in self.required]
        self.merge_rc = 0
        self.files = {"0001.py": migration("r1", None), "0002.py": migration("r2", "r1", "Añade columna x"),
                      "0003.py": migration("r3", "r2", "Índice y")}
        self.compare_files = [{"filename": f"{VERS}/0002.py"}, {"filename": "backend/seeds/plans.py"},
                              {"filename": "backend/Procfile"}]
        self.merged = False

    def __call__(self, args, **kw):
        self.calls.append(args)
        a = args[1:]
        if a[:2] == ["pr", "list"]:
            return cp(0, json.dumps([{"number": 5, "url": f"https://github.com/{REPO}/pull/5", "headRefOid": self.head}]))
        if a[:2] in (["pr", "edit"], ["pr", "create"]):
            return cp()
        if a[:2] == ["pr", "view"]:
            if "mergeCommit" in a[-1]:
                return cp(0, json.dumps({"mergeCommit": {"oid": MERGE}}))
            return cp(0, json.dumps({"statusCheckRollup": self.rollup, "headRefOid": self.head, "state": "OPEN"}))
        if a[:2] == ["pr", "merge"]:
            self.merged = self.merge_rc == 0
            return cp(self.merge_rc, "", "protected" if self.merge_rc else "")
        if a[0] == "api" and "required_status_checks" in a[1]:
            return cp(0, json.dumps({"contexts": self.required}))
        if a[0] == "api" and "/compare/" in a[1]:
            return cp(0, json.dumps({"total_commits": 2, "files": self.compare_files, "commits": [
                {"commit": {"message": "feat: a (#11)"}}, {"commit": {"message": "fix: b (#12)\n\ncuerpo"}}]}))
        if a[0] == "api" and a[1] == "-H":
            name = a[3].split("/")[-1].split("?")[0]
            return cp(0, self.files[name])
        if a[0] == "api" and f"contents/{VERS}?" in a[1]:
            return cp(0, json.dumps([{"name": n} for n in self.files]))
        raise AssertionError(args)

    def merge_args(self):
        return [c for c in self.calls if c[1:3] == ["pr", "merge"]]


class FakeNotifier:
    bot_id = "1"

    def __init__(self):
        self.sent, self.edits, self._n = [], [], 100

    def send_to(self, chat, thread, text, html=True, reply_markup=None, **kw):
        self._n += 1
        self.sent.append({"text": text, "markup": reply_markup, "thread": thread})
        return {"chat_id": chat, "message_id": self._n}

    def edit(self, chat, mid, text, reply_markup=None, **kw):
        self.edits.append({"text": text, "markup": reply_markup})
        return True

    @property
    def last(self):
        return self.sent[-1]


class Desk:
    def __init__(self, tmp):
        self.store = CallbackStore(tmp / "cb")
        self.owner_id = "1"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def build(tmp_path, *, prod_rev="r1", served=None, health_extra=None, cfg=None):
    gh, tg, desk, clock = FakeGH(), FakeNotifier(), Desk(tmp_path), Clock()
    state = {"sha": served or "0" * 40}

    def http(url):
        body = {"status": "healthy", "commit_sha": state["sha"], **(health_extra or {})}
        if prod_rev is not None:
            body["alembic_version"] = prod_rev
        return 200, json.dumps(body)

    cfg = cfg or PromoteConfig(key="migrateam", repo=REPO, health_url="https://api.example/health",
                               seeds=("backend/seeds/",), sensitive_paths=("backend/Procfile",),
                               checks_timeout=100, health_timeout=100, poll_seconds=20)
    p = Promoter({"migrateam": cfg}, notifier=tg, desk=desk, runner=gh, gh_exe=GH, http_get=http,
                 state_dir=tmp_path / "promo", clock=clock, sleep=clock.sleep, now=clock)
    return p, gh, tg, desk, clock, state


def click(p, desk, markup, n=0, mutate=None):
    token, _, idx = markup["inline_keyboard"][n][0]["callback_data"].partition(":")
    rec = desk.store.get(token)
    button = rec["buttons"][int(idx)]
    if mutate:
        mutate(rec)
    return p.on_button(button["action"], rec, WHERE, desk)


def to_review(tmp_path, **kw):
    p, gh, tg, desk, clock, state = build(tmp_path, **kw)
    p.start("migrateam", WHERE)
    return p, gh, tg, desk, clock, state


def test_happy_path_two_confirmations_and_verified_deploy(tmp_path):
    p, gh, tg, desk, clock, state = to_review(tmp_path)
    summary = tg.last["text"]
    assert "PR #5" in summary and "#11" in summary and "#12" in summary and "backend/seeds/plans.py" in summary
    assert "2 migración(es)" in summary and "backend/Procfile" in summary
    assert [b["text"] for b in tg.last["markup"]["inline_keyboard"][0]] == ["✅ Revisado"]
    assert not gh.merge_args()
    click(p, desk, tg.last["markup"])  # 1ª confirmación: Revisado
    second = tg.last["text"]
    assert "0002.py" in second and "Añade columna x" in second and "0003.py" in second and "1." in second
    assert not gh.merge_args()  # con solo "Revisado" no se fusiona
    state["sha"] = MERGE
    click(p, desk, tg.last["markup"])  # 2ª: Sí, promover
    (merge,) = gh.merge_args()
    assert "--merge" in merge and "--admin" not in merge and "--auto" not in merge and "--force" not in merge
    assert merge[merge.index("--match-head-commit") + 1] == HEAD
    assert "producción sirve" in tg.last["text"] and "git revert -m 1" in tg.last["text"]


def test_without_migrations_second_confirmation_says_so(tmp_path):
    p, gh, tg, desk, *_ = to_review(tmp_path, prod_rev="r3")
    click(p, desk, tg.last["markup"])
    assert "Sin migraciones" in tg.last["text"]


@pytest.mark.parametrize("prod_rev", ["zzz", None, "r9"])
def test_alembic_mismatch_or_unreadable_has_no_button(tmp_path, prod_rev):
    p, gh, tg, desk, *_ = to_review(tmp_path, prod_rev=prod_rev)
    assert tg.last["markup"] is None and "escala al coordinador" in tg.last["text"]
    assert not gh.merge_args()


def test_red_check_stops_before_asking(tmp_path):
    p, gh, tg, desk, clock, _ = build(tmp_path)
    gh.rollup = [{"name": "lint", "conclusion": "FAILURE"}]
    p.start("migrateam", WHERE)
    assert "checks en rojo: lint" in tg.last["text"] and tg.last["markup"] is None


def test_checks_that_never_finish_time_out(tmp_path):
    p, gh, tg, desk, clock, _ = build(tmp_path)
    gh.rollup = [{"name": "lint", "conclusion": "SUCCESS"}, {"name": "tests", "status": "IN_PROGRESS"}]
    p.start("migrateam", WHERE)
    assert "no terminaron a tiempo" in tg.last["text"] and tg.last["markup"] is None


def test_forged_signature_is_rejected(tmp_path):
    p, gh, tg, desk, *_ = to_review(tmp_path)
    click(p, desk, tg.last["markup"])
    click(p, desk, tg.last["markup"], mutate=lambda rec: rec.update(sig="0" * 32))
    assert "firma no válida" in tg.last["text"] and not gh.merge_args()


def test_tampered_step_or_head_is_rejected(tmp_path):
    p, gh, tg, desk, *_ = to_review(tmp_path)
    click(p, desk, tg.last["markup"], mutate=lambda rec: rec.update(head="c" * 40))
    assert "firma no válida" in tg.last["text"]


def test_expired_step_is_rejected(tmp_path):
    p, gh, tg, desk, clock, _ = to_review(tmp_path)
    clock.t += STEP_TTL + 1
    click(p, desk, tg.last["markup"])
    assert "caducó" in tg.last["text"] and not gh.merge_args()


def test_flow_expires_after_two_hours_even_with_fresh_step(tmp_path):
    p, gh, tg, desk, clock, _ = to_review(tmp_path)
    markup1 = tg.last["markup"]
    clock.t += 1700
    click(p, desk, markup1)
    markup2 = tg.last["markup"]
    clock.t += 1700 * 4  # >2 h desde el inicio
    click(p, desk, markup2)
    assert "caducó" in tg.last["text"] and not gh.merge_args()


def test_head_change_after_summary_invalidates(tmp_path):
    p, gh, tg, desk, *_ = to_review(tmp_path)
    click(p, desk, tg.last["markup"])
    gh.head = "e" * 40  # alguien empujó a develop entre medias
    click(p, desk, tg.last["markup"])
    assert "cambió desde el resumen" in tg.last["text"] and not gh.merge_args()


def test_replayed_or_skipped_step_is_rejected(tmp_path):
    p, gh, tg, desk, *_ = to_review(tmp_path)
    review_markup = tg.last["markup"]
    click(p, desk, review_markup)
    click(p, desk, review_markup)  # repetir "Revisado"
    assert "paso ya no corresponde" in tg.last["text"]
    assert not gh.merge_args()


def test_merge_refused_is_never_forced(tmp_path):
    p, gh, tg, desk, *_ = to_review(tmp_path)
    click(p, desk, tg.last["markup"])
    gh.merge_rc = 1
    click(p, desk, tg.last["markup"])
    assert len(gh.merge_args()) == 1 and "No se fuerza" in tg.last["text"]


def test_second_press_of_confirm_merges_once(tmp_path):
    p, gh, tg, desk, clock, state = to_review(tmp_path)
    click(p, desk, tg.last["markup"])
    state["sha"] = MERGE
    confirm = tg.last["markup"]
    click(p, desk, confirm)
    click(p, desk, confirm)
    assert len(gh.merge_args()) == 1


def test_health_that_never_reaches_the_sha_is_reported(tmp_path):
    p, gh, tg, desk, *_ = to_review(tmp_path)
    click(p, desk, tg.last["markup"])
    click(p, desk, tg.last["markup"])  # /health sigue sirviendo el commit viejo
    assert "no confirma el despliegue" in tg.last["text"] and "git revert -m 1" in tg.last["text"]


def test_cancel_stops_everything(tmp_path):
    p, gh, tg, desk, *_ = to_review(tmp_path)
    click(p, desk, tg.last["markup"], n=1)
    assert "cancelada" in tg.edits[-1]["text"] and not gh.merge_args()


def test_forbidden_gh_flags_cannot_be_used(tmp_path):
    p, *_ = build(tmp_path)
    for flag in ("--admin", "--force", "--auto"):
        with pytest.raises(PermissionError):
            p._gh("pr", "merge", "5", flag)


def test_unknown_project_and_missing_health_url(tmp_path):
    p, gh, tg, *_ = build(tmp_path)
    p.start("otro", WHERE)
    assert "No hay promoción configurada" in tg.last["text"]
    p2, gh2, tg2, *_ = build(tmp_path / "x", cfg=PromoteConfig(key="migrateam", repo=REPO))
    p2.start("migrateam", WHERE)
    assert "health_url" in tg2.last["text"] and not gh2.calls


def test_real_lanes_yaml_has_migrateam_promotion():
    cfg = load_promote_configs()["migrateam"]
    assert (cfg.head, cfg.base) == ("develop", "master") and cfg.health_url.startswith("https://")
