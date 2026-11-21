"""PR en borrador tras la aprobación de review (agent_lanes/pr_borrador.py): gh y git simulados."""
from __future__ import annotations

import subprocess

from agent_lanes import pr_borrador as pb
from agent_lanes.config import Lane
from agent_lanes.review import ReviewRunner
from tests.test_phase2 import (FakeNotifier, FakeReviewer, FakeReviewGit, FakeVerifier, REVIEW, LANE, ReviewHermes,
                               verdict)

SLUG = "oscarsao/oscar-hq"
URL = "https://github.com/oscarsao/oscar-hq/pull/99"
ON = pb.Ajustes(activo=True, repos=(SLUG,))
LANE_GH = Lane(name="claude-oscarhq", board="oscarhq", repo="C:/r/oscar-hq", base="master", github=SLUG)


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class Sim:
    """Runner que responde a git/gh según lo configurado y apunta cada comando."""

    def __init__(self, *, remota=True, ahead="2", abiertos="[]", fusionados="[]", create=None, gh_rc=0):
        self.cmds = []
        self.remota, self.ahead, self.gh_rc = remota, ahead, gh_rc
        self.listas = {"open": abiertos, "merged": fusionados}
        self.create = create or cp(0, URL + "\n")

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        if cmd[0] == "git":
            if "ls-remote" in cmd:
                return cp(0, "a" * 40 + "\trefs/heads/lane/t_1\n" if self.remota else "")
            if "rev-list" in cmd:
                return cp(0, self.ahead + "\n")
            return cp(0)
        if self.gh_rc and "list" in cmd:
            return cp(self.gh_rc, "", "boom")
        if "list" in cmd:
            return cp(0, self.listas[cmd[cmd.index("--state") + 1]])
        return self.create

    def creates(self):
        return [c for c in self.cmds if c[0] != "git" and "create" in c]


def abrir(sim, lane=LANE_GH, ajustes=ON):
    return pb.abrir_pr_borrador(lane, "t_1", "Título de la tarjeta", "Resumen de la revisión", ajustes=ajustes,
                                runner=sim, gh_exe="gh", sleep=lambda s: None)


def test_apagado_por_defecto_no_hace_nada():
    sim = Sim()
    assert pb.Ajustes() == pb.Ajustes(activo=False, repos=())
    assert abrir(sim, ajustes=pb.Ajustes()).estado == pb.OMITIDO
    assert abrir(sim, ajustes=pb.Ajustes(activo=False, repos=(SLUG,))).estado == pb.OMITIDO
    assert abrir(sim, ajustes=pb.Ajustes(activo=True, repos=())).estado == pb.OMITIDO
    assert abrir(sim, ajustes=pb.Ajustes(activo=True, repos=("otro/repo",))).estado == pb.OMITIDO
    assert sim.cmds == []


def test_yaml_ausente_invalido_o_correcto(tmp_path):
    assert pb.load_ajustes(tmp_path / "no.yaml") == pb.Ajustes()
    (tmp_path / "mal.yaml").write_text("activo: 'sí'\nrepos: 3\n", encoding="utf-8")
    assert pb.load_ajustes(tmp_path / "mal.yaml") == pb.Ajustes()
    (tmp_path / "ok.yaml").write_text(f"activo: true\nrepos: [{SLUG}, 5]\n", encoding="utf-8")
    assert pb.load_ajustes(tmp_path / "ok.yaml") == pb.Ajustes(activo=True, repos=(SLUG,))
    assert pb.load_ajustes(pb.SETTINGS_PATH) == pb.Ajustes()  # el fichero del repo viene apagado


def test_encendido_con_lista_blanca_abre_el_pr_en_borrador():
    sim = Sim()
    res = abrir(sim)
    assert (res.estado, res.url) == (pb.ABIERTO, URL)
    (cmd,) = sim.creates()
    assert cmd[:3] == ["gh", "pr", "create"] and "--draft" in cmd
    assert cmd[cmd.index("--base") + 1] == "master" and cmd[cmd.index("--head") + 1] == "lane/t_1"
    assert cmd[cmd.index("--repo") + 1] == SLUG and cmd[cmd.index("--title") + 1] == "Título de la tarjeta"
    body = cmd[cmd.index("--body") + 1]
    assert "Resumen de la revisión" in body and "la fusión pide el botón de Oscar" in body


def test_resumen_largo_se_recorta():
    sim = Sim()
    pb.abrir_pr_borrador(LANE_GH, "t_1", "T", "x" * 5000, ajustes=ON, runner=sim, gh_exe="gh", sleep=lambda s: None)
    body = sim.creates()[0][sim.creates()[0].index("--body") + 1]
    assert len(body) < 1800 and "la fusión pide el botón de Oscar" in body


def test_ya_existe_un_pr_no_duplica():
    for sim in (Sim(abiertos='[{"number": 5, "url": "u"}]'), Sim(fusionados='[{"number": 5, "url": "u"}]')):
        assert abrir(sim).estado == pb.OMITIDO
        assert sim.creates() == []


def test_sin_commits_por_delante_no_abre():
    sim = Sim(ahead="0")
    assert abrir(sim).estado == pb.OMITIDO and sim.creates() == []


def test_rama_inexistente_en_el_remoto_no_abre():
    sim = Sim(remota=False)
    res = abrir(sim)
    assert res.estado == pb.OMITIDO and "no existe" in res.motivo and sim.creates() == []


def test_carril_ops_no_abre():
    ops = Lane(name="claude-ops", kind="ops", github=SLUG, repo="C:/r", base="master")
    sim = Sim()
    assert abrir(sim, lane=ops).estado == pb.OMITIDO and sim.cmds == []


def test_fallos_de_gh_no_lanzan():
    assert abrir(Sim(gh_rc=1)).estado == pb.FALLO
    assert abrir(Sim(create=cp(1, "", "denied"))).estado == pb.FALLO

    def revienta(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    res = abrir(revienta)
    assert res.estado == pb.FALLO


# --- enganche en review.py ---------------------------------------------------------------------------------------

def make(res=None, exc=None, complete_ok=True):
    h = ReviewHermes([{"id": "t_1", "title": "T", "body": "B", "assignee": "claude-oscarhq", "status": "review"}])
    if not complete_ok:
        h.complete = lambda tid, result, metadata: (False, "rechazado")
    llamadas = []

    def hook(lane, tid, title, summary):
        llamadas.append((lane, tid, title, summary))
        if exc:
            raise exc
        return res

    r = ReviewRunner(REVIEW, {"claude-oscarhq": LANE}, hermes_for=lambda b: h, git=FakeReviewGit(),
                     reviewer=FakeReviewer([verdict("approve")]), verifier=FakeVerifier(True), notify=FakeNotifier(),
                     sleep=lambda s: None, pr_borrador=hook)
    return r, h, llamadas


def comentarios(h):
    return [c[2] for c in h.calls if c[0] == "comment"]


def test_review_abre_pr_y_comenta_la_url():
    r, h, llamadas = make(pb.Resultado(pb.ABIERTO, url=URL))
    assert r.run_once() == {"t_1": "done"}
    assert len(llamadas) == 1 and llamadas[0][1] == "t_1"
    assert [c for c in comentarios(h) if URL in c] and "complete" in [c[0] for c in h.calls]


def test_review_fallo_de_gh_no_bloquea_y_deja_un_comentario():
    r, h, _ = make(pb.Resultado(pb.FALLO, "gh pr create falló: x"))
    assert r.run_once() == {"t_1": "done"}
    assert len([c for c in comentarios(h) if "No se pudo abrir el PR" in c]) == 1
    assert "block" not in [c[0] for c in h.calls]


def test_review_excepcion_del_enganche_no_rompe_la_aprobacion():
    r, h, _ = make(exc=RuntimeError("pum"))
    assert r.run_once() == {"t_1": "done"}


def test_review_omitido_no_comenta_y_complete_rechazado_no_abre():
    r, h, llamadas = make(pb.Resultado(pb.OMITIDO, "apagado"))
    assert r.run_once() == {"t_1": "done"} and comentarios(h) == []
    r, h, llamadas = make(pb.Resultado(pb.ABIERTO, url=URL), complete_ok=False)
    assert r.run_once() == {"t_1": "blocked:transient"} and llamadas == []
