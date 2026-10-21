"""[ATASCOS RÁPIDOS] (t_86c09458): aviso diario de PR esperando lote, tarjetas sobre la rama del PR, conflicto solo en
ficheros append-only (unión + gates + push a la rama del PR) y conflicto de código (vuelve al carril).
Mocks de git/gh/Hermes/Telegram de test_integrator: nada real."""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime

from agent_lanes.atascos import DEFAULT_APPEND_ONLY, PendingNotice, is_append_only, pr_branch_card_body
from agent_lanes.integrator import load_integrator_settings
from agent_lanes.worker import merge_refs, push_refs
from contract.guard import check_bash
from tests.test_integrator import HEAD, FakeHermes, World, cp, make, record

NEW_HEAD = "c" * 40
DAY = 86400.0


# --- ficheros append-only ---------------------------------------------------------------------------------

def test_is_append_only_exacto_y_glob():
    assert is_append_only("docs/kb/CHANGELOG.md", DEFAULT_APPEND_ONLY)
    assert is_append_only("docs/kb/LEDGER_ESTATUS.md", DEFAULT_APPEND_ONLY)
    assert is_append_only("docs/kb/archivo/CHANGELOG.md", DEFAULT_APPEND_ONLY)
    assert not is_append_only("src/app.py", DEFAULT_APPEND_ONLY)
    assert is_append_only("docs/kb/x/NOTAS.md", ("docs/kb/*/NOTAS.md",))
    assert not is_append_only("docs/kb/CHANGELOG.md", ())


def test_lanes_yaml_lista_configurable(tmp_path):
    f = tmp_path / "lanes.yaml"
    f.write_text("integrator:\n  lanes:\n    a: {deploy: none, append_only_paths: [docs/X.md]}\n"
                 "    b: {deploy: none}\n    c: {deploy: none, append_only_paths: []}\n", encoding="utf-8")
    p = load_integrator_settings(f, {}).policies
    assert p["a"].append_only_paths == ("docs/X.md",)
    assert p["b"].append_only_paths == DEFAULT_APPEND_ONLY
    assert p["c"].append_only_paths == ()


# --- aviso diario ---------------------------------------------------------------------------------------

def _lote(tmp_path, hour=10):
    integ, w, h, tg, d, clock = make(tmp_path)
    pol = dataclasses.replace(integ.settings.policies["claude-oscarhq"], batch="daily")
    integ.settings = dataclasses.replace(integ.settings, policies={"claude-oscarhq": pol})
    integ._now = lambda: 1_000_000.0 + 8 * DAY
    integ.batches.clock = lambda: datetime(2026, 10, 21, hour, 0)
    return integ, w, h, tg, d


def _pend(integ, tid, pr, days):
    integ._save(tid, lane="claude-oscarhq", status="batch_pending", pr=pr, pr_url=f"https://x/pull/{pr}",
                head_sha=HEAD, title=f"Cambio {pr}", pending_since=1_000_000.0 + (8 - days) * DAY)


def test_aviso_sin_prs_no_manda_nada(tmp_path):
    integ, w, h, tg, d = _lote(tmp_path)
    assert integ.batches.notice.tick() == []
    assert tg.sent == []


def test_aviso_un_pr_una_linea_y_boton_una_vez_al_dia(tmp_path):
    integ, w, h, tg, d = _lote(tmp_path)
    _pend(integ, "t_1", 143, 7)
    assert integ.batches.notice.tick() == ["claude-oscarhq"]
    text = tg.sent[-1]["text"]
    assert "1 PR aprobado esperando lote" in text and "#143" in text and "7 días" in text
    assert "Fusionar lote" in json.dumps(tg.sent[-1]["markup"], ensure_ascii=False)
    assert integ.batches.notice.tick() == []  # mismo día: no repite
    assert len(tg.sent) == 1


def test_aviso_n_prs_y_no_antes_de_las_9(tmp_path):
    integ, w, h, tg, d = _lote(tmp_path, hour=8)
    _pend(integ, "t_1", 143, 7)
    _pend(integ, "t_2", 171, 1)
    assert integ.batches.notice.tick() == []
    integ.batches.clock = lambda: datetime(2026, 10, 21, 9, 5)
    assert integ.batches.notice.tick() == ["claude-oscarhq"]
    text = tg.sent[-1]["text"]
    assert "2 PR aprobados esperando lote" in text and "#143" in text and "#171" in text and "1 día" in text
    print(text)


def test_boton_fusionar_lote_monta_el_lote(tmp_path):
    integ, w, h, tg, d = _lote(tmp_path)
    _pend(integ, "t_1", 143, 3)
    integ.batches.notice._spawn = lambda fn: fn()
    seen = []
    integ.batches.assemble = lambda lane, policy, manual=False: seen.append(manual) or "Lote montado: PR #9"
    integ.batches.notice.tick()
    from tests.test_integrator import press
    press(d, tg)
    assert seen == [True]
    assert any("Lote montado" in e["text"] for e in tg.edits)


# --- tarjetas sobre la rama del PR ----------------------------------------------------------------------

def test_tarjeta_de_rebase_trabaja_sobre_la_rama_del_pr():
    body = pr_branch_card_body("lane/t_abc", 143, "master", "release/lote-x", rebase=True)
    assert "Rama-origen: lane/t_abc" in body and "Rama-push: lane/t_abc" in body
    assert "git push origin HEAD:lane/t_abc" in body and "force" in body.lower()
    task = {"body": body}
    assert push_refs(task) == ["lane/t_abc"]
    from agent_lanes.config import Lane
    assert "origin/lane/t_abc" in merge_refs(Lane(name="x", board="b", repo="r", base="master"), task)


def test_push_refs_solo_ramas_lane():
    assert push_refs({"body": "Rama-push: main\nRama-push: lane/../x\nRama-push: feature/a"}) == []
    assert push_refs({"body": "Rama-push: lane/t_1\nRama-push: lane/t_1"}) == ["lane/t_1"]


def test_guard_push_a_la_rama_del_pr_acotado():
    ok = ["lane/t_old"]
    cmd = lambda c, **k: check_bash(c, None, "t_new", ["origin/lane/t_old"], **k)
    assert cmd("git push origin HEAD:lane/t_old", push_ok=ok) is None
    assert cmd("git push -u origin lane/t_new", push_ok=ok) is None
    assert cmd("git push origin HEAD:lane/t_old") is not None            # sin permiso explícito
    assert cmd("git push origin HEAD:lane/t_otra", push_ok=ok) is not None
    assert cmd("git push origin HEAD:main", push_ok=["main"]) is not None  # nunca la base
    assert cmd("git push origin HEAD:master", push_ok=["master"]) is not None
    assert cmd("git push --force origin HEAD:lane/t_old", push_ok=ok) is not None
    assert cmd("git push origin +HEAD:lane/t_old", push_ok=ok) is not None


# --- fusión automática append-only ----------------------------------------------------------------------

class ConflictHermes(FakeHermes):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.created = []

    def create(self, title, body, assignee, **kw):
        self.created.append((title, body, assignee, kw))
        return "t_new1"


class ConflictWorld(World):
    """PR en CONFLICTING. `unmerged`: ficheros que siguen en conflicto tras el merge (lo que union no resuelve)."""

    def __init__(self):
        super().__init__()
        self.pr = {**self.pr, "mergeable": "CONFLICTING"}
        self.unmerged: list[str] = []
        self.push_rc, self.push_err = 0, ""
        self.pushes, self.pr_comments = [], []

    def _git(self, args):
        if "push" in args:
            self.pushes.append(args)
            return cp(self.push_rc, "", self.push_err)
        if "rev-parse" in args and args[-1] == "HEAD":
            return cp(0, NEW_HEAD + "\n")
        if "merge" in args and "--abort" not in args and self.unmerged:
            return cp(1, "", "CONFLICT")
        if "--diff-filter=U" in args:
            return cp(0, "\n".join(self.unmerged) + "\n")
        return super()._git(args)

    def _gh(self, args):
        if args[1:3] == ["pr", "comment"]:
            self.pr_comments.append(args)
            return cp()
        return super()._gh(args)


def _conflict(tmp_path, world):
    integ, w, h, tg, d, clock = make(tmp_path, world=world)
    h2 = ConflictHermes(h.tasks, h.comments_by)
    integ.hermes_for = lambda b: h2
    return integ, w, h2, tg


def test_conflicto_solo_append_only_se_une_y_se_empuja_a_la_rama_del_pr(tmp_path):
    integ, w, h, tg = _conflict(tmp_path, ConflictWorld())
    assert integ.run_pass() == {"t_1": "autoresolved"}
    assert len(w.pushes) == 1 and w.pushes[0][-1] == "HEAD:refs/heads/lane/t_1"
    assert w.pushes[0][-2] == "origin" and not any("force" in a or a.startswith("+") for a in w.pushes[0])
    # el merge se hizo con el driver union para los ficheros configurados
    merge = next(a for a, _ in w.calls if "merge" in a and "--no-ff" in a)
    attrs = next(x for x in merge if x.startswith("core.attributesFile="))
    assert "docs/kb/CHANGELOG.md merge=union" in open(attrs.split("=", 1)[1], encoding="utf-8").read()
    # la aprobación de Oscar pasa a la nueva cabeza, comentarios en tarjeta y en el PR
    assert json.loads((tmp_path / "state" / "approvals" / "t_1.json").read_text())["head_sha"] == NEW_HEAD
    assert any("merge=union" in c[1] and c[0] == "t_1" for c in h.comments)
    assert len(w.pr_comments) == 1 and w.pr_comments[0][3] == "7"
    assert integ._state("t_1")["autoresolve_head"] == NEW_HEAD


def test_cabeza_empujada_por_el_integrador_no_pide_reaprobacion(tmp_path):
    integ, w, h, tg = _conflict(tmp_path, ConflictWorld())
    integ.run_pass()
    pr = {**w.pr, "headRefOid": NEW_HEAD}
    show = h.show("t_1")  # lo que revisó el carril review era la cabeza anterior (HEAD)
    assert integ._approval_problem("t_1", 7, pr, show) is None
    integ._save("t_1", autoresolve_head=None)
    assert integ._approval_problem("t_1", 7, pr, show) is not None  # una cabeza ajena SÍ pide volver a aprobar


def test_conflicto_mezclado_con_codigo_no_empuja_y_vuelve_al_carril(tmp_path):
    world = ConflictWorld()
    world.unmerged = ["src/app.py"]
    integ, w, h, tg = _conflict(tmp_path, world)
    h.reopen_ok = True
    assert integ.run_pass() == {"t_1": "sent_back"}
    assert w.pushes == []
    assert any("merge origin/master" in c[1] for c in h.comments)
    assert any("Lo da: el carril claude-oscarhq" in c[1] and "src/app.py" in c[1] for c in h.comments)


def test_conflicto_de_codigo_sin_poder_reabrir_crea_tarjeta_sobre_la_rama_del_pr(tmp_path):
    world = ConflictWorld()
    world.unmerged = ["src/app.py"]
    integ, w, h, tg = _conflict(tmp_path, world)
    h.reopen_ok = False
    assert integ.run_pass() == {"t_1": "sent_back"}
    assert w.pushes == []
    title, body, assignee, _ = h.created[0]
    assert assignee == "claude-oscarhq" and "Rama-origen: lane/t_1" in body and "Rama-push: lane/t_1" in body


def test_gate_fallido_tras_la_union_no_empuja_y_avisa(tmp_path):
    world = ConflictWorld()
    world.test_rc = 1
    integ, w, h, tg = _conflict(tmp_path, world)
    assert integ.run_pass() == {"t_1": "autoresolve_blocked"}
    assert w.pushes == [] and "NO empujada" in tg.all_text
    assert integ.run_pass(force=True) != {"t_1": "autoresolved"}  # misma cabeza: no se reintenta


def test_rama_protegida_no_empuja_y_avisa(tmp_path):
    world = ConflictWorld()
    world.push_rc, world.push_err = 1, "remote: error: GH006: Protected branch update failed"
    integ, w, h, tg = _conflict(tmp_path, world)
    assert integ.run_pass() == {"t_1": "autoresolve_blocked"}
    assert len(w.pushes) == 1 and "protegida" in tg.all_text
    assert integ._state("t_1").get("autoresolve_head") is None


def test_lista_vacia_desactiva_la_union(tmp_path):
    integ, w, h, tg = _conflict(tmp_path, ConflictWorld())
    pol = dataclasses.replace(integ.settings.policies["claude-oscarhq"], append_only_paths=())
    integ.settings = dataclasses.replace(integ.settings, policies={"claude-oscarhq": pol})
    integ.run_pass()
    assert w.pushes == []


def test_el_integrador_sigue_sin_poder_empujar_por_exec(tmp_path):
    import pytest
    integ, *_ = make(tmp_path)
    with pytest.raises(PermissionError):
        integ._exec(["git", "push", "origin", "main"])
    with pytest.raises(PermissionError):
        integ._push_pr_branch("x", "origin", "../main")
