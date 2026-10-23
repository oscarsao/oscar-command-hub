"""Menos ruido del runner (23-10): sin PR si la rama no tiene commits, sin tocar tarjetas ya cerradas y un solo
aviso de log por tarjeta de Telegram de otro tablero."""
from __future__ import annotations

import logging
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent_lanes import telegram_lane
from agent_lanes.config import Lane
from agent_lanes.hermes import OSCAR_AUTHOR
from agent_lanes.worker import WorkerOutcome
from tests.test_carril_telegram import LANE as TG_LANE, TG, task
from tests.test_decisions import DeskHermes, FakeGH, _desk, _done_markup, _press
from tests.test_phase2 import make_review, verdict


# --- 1. aprobar una rama sin commits ------------------------------------------------------------------

class NoCommitsGH(FakeGH):
    def __call__(self, args, **kw):
        if args[1:3] == ["pr", "create"]:
            self.calls.append(args)
            return subprocess.CompletedProcess(
                args, 1, "", "pull request create failed: GraphQL: No commits between master and lane/t_1 (createPullRequest)")
        return super().__call__(args, **kw)


class ArchHermes(DeskHermes):
    def archive(self, tid):
        self.calls.append(("archive", tid))
        return self.ok


def test_aprobar_sin_commits_no_abre_pr_comenta_y_archiva(tmp_path):
    desk, tg, h = _desk(tmp_path, gh=NoCommitsGH(), hermes=ArchHermes())
    _press(desk, _done_markup(desk), 0)
    assert [c[0] for c in h.calls] == ["comment", "archive"]
    assert "Sin cambios respecto a la base" in h.calls[0][2] and h.calls[0][3] == OSCAR_AUTHOR
    assert not any("APROBADO-OSCAR" in str(c) for c in h.calls)
    assert "sin cambios" in tg.edits[-1]["text"] and tg.edits[-1]["markup"] is None


def test_aprobar_sin_commits_y_sin_poder_archivar_avisa(tmp_path):
    desk, tg, h = _desk(tmp_path, gh=NoCommitsGH(), hermes=ArchHermes(ok=False))
    _press(desk, _done_markup(desk), 0)
    assert "no se pudo archivar" in tg.edits[-1]["text"]


# --- 2. la revisión no reabre ni bloquea tarjetas cerradas ---------------------------------------------

def test_revision_ignora_tarjeta_ya_cerrada():
    r, h, g, n, rv = make_review([verdict("approve")])
    orig_show = h.show

    def show(tid):
        h.tasks[0]["status"] = "done"  # ...pero al releerla el integrador ya la cerró
        return orig_show(tid)

    h.show = show
    assert r.run_once() == {"t_1": "skip:done"}
    assert rv.calls == 0 and h.calls == [] and n.msgs == []


def test_revision_no_bloquea_si_se_cierra_mientras_revisa():
    r, h, g, n, rv = make_review([WorkerOutcome(False, "error", None, 0.0, "tiempo agotado")])
    run = rv.run

    def run_y_cierra(*a, **kw):
        h.tasks[0]["status"] = "archived"
        return run(*a, **kw)

    rv.run = run_y_cierra
    assert r.run_once() == {"t_1": "skip:archived"}
    assert "block" not in h.kinds() and n.msgs == []


def test_revision_sigue_bloqueando_si_la_tarjeta_sigue_en_review():
    r, h, *_ = make_review([WorkerOutcome(False, "error", None, 0.0, "tiempo agotado")])
    assert r.run_once() == {"t_1": "blocked:transient"} and "block" in h.kinds()


# --- 3. aviso de log una vez por tarjeta ----------------------------------------------------------------

def test_aviso_de_otro_tablero_se_loguea_una_sola_vez(caplog):
    telegram_lane._AVISADAS.clear()
    lanes = {TG: TG_LANE, "claude-oscarhq": Lane(name="claude-oscarhq", board="oscarhq")}
    h = MagicMock()
    h.list_ready.return_value = [task("[Telegram] c", tid="t_00000003", assignee="claude-oscarhq")]
    with caplog.at_level(logging.INFO, logger="agent_lanes"):
        for _ in range(3):
            assert telegram_lane.reasignar_abiertas(lambda b: h, lanes) == []
    assert [r.getMessage() for r in caplog.records].count(
        "t_00000003: tarjeta de Telegram en el tablero oscarhq; claude-telegram trabaja en %s: no se mueve"
        % TG_LANE.board) == 1


# --- 4. t_cce5da81: no se pide decisión de PR sobre una rama sin commits ------------------------------------------------

def _revision_sin_commits(tmp_path, ahead, merged=None):
    """Re-verificación fallida en review (h.block no puede mover una tarea en review) -> escalada con [✅ Aprobar].
    Con la rama sin commits sobre la base, la escalada no debe salir."""
    from agent_lanes.review import ReviewRunner
    from tests.test_phase2 import LANE as L, REVIEW, FakeReviewGit, FakeVerifier
    from tests.test_review_escalada import FailingBlockHermes, SpyDecisions
    from tests.test_integrator import FakeTg

    class Git(FakeReviewGit):
        def commits_ahead(self, lane, tid):
            return ahead

        def merged_pr(self, lane, tid):
            return merged

    h = FailingBlockHermes([{"id": "t_1", "title": "T", "body": "B", "assignee": "claude-oscarhq", "status": "review"}])
    n, d = FakeTg(), SpyDecisions()
    r = ReviewRunner(REVIEW, {"claude-oscarhq": L}, hermes_for=lambda b: h, git=Git(), reviewer=None,
                     verifier=FakeVerifier(False), notify=n, decisions=d, state_dir=tmp_path / "rb")
    return r, h, n, d


def test_rama_sin_commits_vuelve_al_carril_sin_preguntar(tmp_path):
    r, h, n, d = _revision_sin_commits(tmp_path, ahead=0)
    assert r.run_once() == {"t_1": "changes:sin_commits"}
    assert d.markups == []  # ninguna decisión con [✅ Aprobar] a Oscar
    assert ("reopen", "t_1") in h.calls
    comment = next(c for c in h.calls if c[0] == "comment")
    assert "no tiene commits sobre la base" in comment[2] and "empuja el trabajo" in comment[2]
    assert all(s["markup"] is None for s in n.sent)


def test_rama_sin_commits_ya_integrada_se_cierra(tmp_path):
    r, h, n, d = _revision_sin_commits(tmp_path, ahead=0, merged="https://github.com/o/r/pull/9")
    assert r.run_once() == {"t_1": "done:integrada"}
    assert d.markups == [] and [c[0] for c in h.calls if c[0] in ("complete", "reopen")] == ["complete"]


def test_rama_con_commits_o_sin_saberlo_escala_como_siempre(tmp_path):
    for ahead in (3, None):
        r, h, n, d = _revision_sin_commits(tmp_path / str(ahead), ahead=ahead)
        assert r.run_once() == {"t_1": "blocked:transient"}
        assert d.markups == [("blocked", "review_stuck")]
