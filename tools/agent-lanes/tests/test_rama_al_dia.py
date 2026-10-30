"""P4: rama al día con origin/<base> antes de review (repo git temporal real, sin red)."""
from __future__ import annotations

import dataclasses
import subprocess
import sys

from agent_lanes.git_ops import AlDia, GitOps
from agent_lanes.runner import LaneRunner
from agent_lanes.verify import VerifyResult

from tests.test_runner_state import FakeGit, FakeHermes, FakeNotifier, FakeWorker, LANE, ok_outcome

OK_CMD = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
KO_CMD = f'"{sys.executable}" -c "import sys; sys.exit(3)"'


def g(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def escribe(cwd, nombre, texto):
    (cwd / nombre).write_text(texto, encoding="utf-8", newline="\n")
    g(cwd, "add", nombre)
    g(cwd, "commit", "-qm", f"edita {nombre}")


def montar(tmp_path, test_cmd=OK_CMD):
    """origin (bare) + clon de trabajo en lane/t_1 con un commit propio; devuelve (lane, worktree, otro_clon)."""
    origin, wt, otro = tmp_path / "origin.git", tmp_path / "wt", tmp_path / "otro"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(wt)], check=True, capture_output=True)
    g(wt, "checkout", "-q", "-b", "main")
    escribe(wt, "a.txt", "uno\ndos\ntres\n")
    escribe(wt, "b.txt", "b\n")
    g(wt, "push", "-q", "origin", "main")
    g(wt, "checkout", "-q", "-b", "lane/t_1")
    escribe(wt, "b.txt", "b propio\n")
    g(wt, "push", "-q", "origin", "lane/t_1")
    subprocess.run(["git", "clone", "-q", str(origin), str(otro)], check=True, capture_output=True)
    lane = dataclasses.replace(LANE, repo=str(wt), base="main", test_cmd=test_cmd)
    return lane, wt, otro


def avanza_base(otro, nombre, texto):
    g(otro, "checkout", "-q", "main")
    escribe(otro, nombre, texto)
    g(otro, "push", "-q", "origin", "main")


def test_base_sin_avanzar_no_toca_nada(tmp_path):
    lane, wt, _ = montar(tmp_path)
    antes = g(wt, "rev-parse", "HEAD")
    r = GitOps().poner_al_dia(lane, str(wt), "t_1")
    assert r.estado == "al_dia" and g(wt, "rev-parse", "HEAD") == antes


def test_base_avanzada_y_limpia_fusiona_empuja_y_corre_tests(tmp_path):
    lane, wt, otro = montar(tmp_path)
    avanza_base(otro, "a.txt", "uno\ndos\ntres\ncuatro\n")
    r = GitOps().poner_al_dia(lane, str(wt), "t_1")
    assert r.estado == "fusionada" and r.sha == g(wt, "rev-parse", "HEAD")
    assert g(wt, "rev-parse", "origin/lane/t_1") == r.sha  # empujada (merge, sin force)
    assert len(g(wt, "rev-list", "--parents", "-n1", "HEAD").split()) == 3  # commit de merge


def test_conflicto_real_devuelve_ficheros_y_no_toca_la_rama(tmp_path):
    lane, wt, otro = montar(tmp_path)
    avanza_base(otro, "b.txt", "b de la base\n")
    antes = g(wt, "rev-parse", "HEAD")
    r = GitOps().poner_al_dia(lane, str(wt), "t_1")
    assert r.estado == "conflicto" and r.ficheros == ["b.txt"]
    assert g(wt, "rev-parse", "HEAD") == antes and g(wt, "status", "--short") == ""


def test_si_los_tests_fallan_se_deshace_el_merge(tmp_path):
    lane, wt, otro = montar(tmp_path, KO_CMD)
    avanza_base(otro, "a.txt", "uno\ndos\ntres\ncuatro\n")
    antes = g(wt, "rev-parse", "HEAD")
    r = GitOps().poner_al_dia(lane, str(wt), "t_1")
    assert r.estado == "tests" and "exit 3" in r.detalle
    assert g(wt, "rev-parse", "HEAD") == antes and g(wt, "rev-parse", "origin/lane/t_1") == antes


class GitConDia(FakeGit):
    def __init__(self, dia):
        super().__init__()
        self.dia = dia

    def poner_al_dia(self, lane, cwd, tid):
        return self.dia


def _run(dia):
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    ok = lambda lane, tid, cwd, result: VerifyResult(ok=True, test_exit=0)  # noqa: E731
    r = LaneRunner(LANE, hermes=h, git=GitConDia(AlDia(**dia)), worker=FakeWorker([ok_outcome()]), verifier=ok,
                   notify=FakeNotifier())
    return r.run_once(), h


def test_runner_conflicto_vuelve_al_carril_con_los_ficheros():
    res, h = _run({"estado": "conflicto", "ficheros": ["b.txt", "c/d.py"], "detalle": "origin/master choca"})
    assert res == {"t_1": "blocked:transient"}
    assert any("b.txt, c/d.py" in c[3] for c in h.calls if c[0] == "block")
    assert not [c for c in h.calls if c[0] == "review"]


def test_runner_fusionada_pasa_a_review_con_el_sha_nuevo():
    res, h = _run({"estado": "fusionada", "sha": "b" * 40})
    assert res == {"t_1": "review"}
    assert [c for c in h.calls if c[0] == "review"][0][3]["head_sha"] == "b" * 40


def test_limpieza_pendiente_se_registra_una_vez_por_tarea_y_dia(tmp_path, caplog):
    from agent_lanes.review import log_limpieza_pendiente as lp
    f = tmp_path / "l.json"
    with caplog.at_level("WARNING", logger="agent_lanes"):
        assert lp("t_1", "x", hoy="2026-10-30", path=f) is True
        assert lp("t_1", "x", hoy="2026-10-30", path=f) is False   # misma tarea, mismo día
        assert lp("t_2", "x", hoy="2026-10-30", path=f) is True    # otra tarea
        assert lp("t_1", "x", hoy="2026-10-31", path=f) is True    # otro día
    assert len([r for r in caplog.records if "limpieza local pendiente" in r.getMessage()]) == 3
    f.write_text("{no json", encoding="utf-8")
    assert lp("t_1", "x", hoy="2026-10-31", path=f) is True        # estado ilegible: se registra
