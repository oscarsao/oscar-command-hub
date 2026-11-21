"""Cierre «ya resuelto» (21-11): el worker prueba que el hallazgo ya está en la base y la tarjeta se cierra sin PR y sin
preguntar a Oscar, SOLO si git confirma que los SHA que cita están en la base. Con una prueba floja se ignora y todo
sigue como antes. Nada sale a Telegram: notificador falso."""
from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

from agent_lanes.git_ops import GitOps
from agent_lanes.runner import LaneRunner
from tests.test_runner_state import GOOD, LANE, FakeGit, FakeHermes, FakeVerifier, FakeWorker, ok_outcome

EVID = "El portal recibe 404 en la ruta de descarga: ver backend/forms_routes.py y tests/test_portal_pdf.py."
BASE = "b" * 40
YA = {"evidence": EVID, "checked_sha": BASE}


class HermesCompleta(FakeHermes):
    def complete(self, task_id, result, metadata):
        self.calls.append(("complete", task_id, result, metadata))
        return True, ""


class GitBase(FakeGit):
    """FakeGit que sabe qué SHA están en la base (lo que en producción responde `git merge-base --is-ancestor`)."""

    def __init__(self, en_base=(BASE,)):
        super().__init__()
        self.en_base = set(en_base)
        self.preguntas = []

    def base_contiene(self, lane, cwd, *shas):
        self.preguntas.append(shas)
        return all(s in self.en_base for s in shas)


class Notif:
    def __init__(self):
        self.sent = []

    def send(self, *a, **k):
        self.sent.append((a, k))
        return None


def _correr(result, git=None, hermes=None):
    h = hermes or HermesCompleta(tasks=[{"id": "t_1", "title": "T", "body": "Cuerpo"}])
    out = LaneRunner(LANE, hermes=h, git=git or GitBase(), worker=FakeWorker([ok_outcome(result)]),
                     verifier=FakeVerifier(ok=False), notify=Notif()).run_once()
    return h, out


def _ya(status, **extra):
    return {**GOOD, "status": status, "changed_files": [], "head_sha": BASE, "already_resolved": YA, **extra}


Q = {"question": "¿Cierro?", "options": ["Sí", "No"], "recommended": 0}


def test_needs_input_con_ya_resuelto_cierra_la_tarjeta_sin_preguntar():
    g = GitBase()
    h, out = _correr(_ya("needs_input", questions=[Q]), git=g)
    assert out == {"t_1": "ya_resuelta"} and g.preguntas == [(BASE, BASE)]
    assert "complete" in h.kinds() and "block" not in h.kinds() and "review" not in h.kinds()
    comment = [c for c in h.calls if c[0] == "comment" and "CERRADA COMO YA RESUELTA" in c[2]]
    assert len(comment) == 1 and EVID in comment[0][2] and ("b" * 12) in comment[0][2]
    meta = [c for c in h.calls if c[0] == "complete"][0][3]
    assert meta["kind"] == "ya_resuelta" and meta["checked_sha"] == BASE and meta["verified"] is False


def test_done_con_ya_resuelto_no_pasa_por_la_verificacion_de_commits():
    h, out = _correr(_ya("done"))  # FakeVerifier(ok=False): sin commits el verificador diría que no
    assert out == {"t_1": "ya_resuelta"} and "complete" in h.kinds() and "review" not in h.kinds()


def test_sha_que_no_esta_en_la_base_se_ignora_y_sigue_la_pregunta():
    """El worker inventa un SHA (no existe o no es de la base): no se cierra nada."""
    h, _ = _correr(_ya("needs_input", questions=[Q]), git=GitBase(en_base=("c" * 40,)))
    assert "complete" not in h.kinds() and "block" in h.kinds()


def test_head_distinto_de_la_base_se_ignora():
    """El HEAD del worktree trae commits propios (no está en la base): no es «ya resuelto»."""
    h, _ = _correr(_ya("needs_input", questions=[Q], head_sha="d" * 40))
    assert "complete" not in h.kinds() and "block" in h.kinds()


def test_sin_comprobacion_de_git_no_se_cierra_nada():
    class SinBase:
        prepared = []

        def prepare_worktree(self, lane, task_id):
            return f"{lane.worktree_root}/lane-{task_id}"

    h, _ = _correr(_ya("needs_input", questions=[Q]), git=SinBase())
    assert "complete" not in h.kinds() and "block" in h.kinds()


def test_evidencia_corta_sha_malo_o_ficheros_cambiados_se_ignoran():
    for cambio in ({"already_resolved": {"evidence": "ya está", "checked_sha": BASE}},
                   {"already_resolved": {"evidence": EVID, "checked_sha": "no-es-un-sha"}},
                   {"changed_files": ["backend/x.py"]}):
        h, _ = _correr({**_ya("needs_input", questions=[Q]), **cambio})
        assert "complete" not in h.kinds() and "block" in h.kinds()


def test_complete_rechazado_bloquea_transitorio_y_no_cierra():
    class Rechaza(HermesCompleta):
        def complete(self, task_id, result, metadata):
            return False, "nope"

    h, _ = _correr(_ya("done"), hermes=Rechaza(tasks=[{"id": "t_1", "title": "T", "body": "Cuerpo"}]))
    assert [c for c in h.calls if c[0] == "block"][0][2] == "transient"


# --- git real: base_contiene con un repo temporal y su «remoto» -------------------------------------------------------

def _g(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_base_contiene_con_git_real(tmp_path):
    remoto = tmp_path / "remoto.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remoto)], check=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "clone", "-q", str(remoto), str(wt)], check=True, capture_output=True)
    (wt / "a.txt").write_text("1\n", encoding="utf-8", newline="\n")
    _g(wt, "add", "-A")
    _g(wt, "commit", "-q", "-m", "uno")
    _g(wt, "branch", "-M", "main")
    uno = _g(wt, "rev-parse", "HEAD")
    _g(wt, "push", "-q", "-u", "origin", "main")
    (wt / "a.txt").write_text("2\n", encoding="utf-8", newline="\n")
    _g(wt, "commit", "-q", "-am", "dos")
    dos = _g(wt, "rev-parse", "HEAD")  # commit propio sin empujar: NO está en la base
    git = GitOps()
    lane = replace(LANE, remote="origin", base="main")
    assert git.base_contiene(lane, str(wt), uno) is True
    assert git.base_contiene(lane, str(wt), dos) is False
    assert git.base_contiene(lane, str(wt), "e" * 40) is False
    assert git.base_contiene(lane, str(wt)) is False
