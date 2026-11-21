"""PR en borrador tras la aprobación de review (agent_lanes/pr_borrador.py): gh y git simulados."""
from __future__ import annotations

import subprocess
from pathlib import Path

from agent_lanes import pr_borrador as pb
from agent_lanes.config import Lane
from agent_lanes.review import ReviewRunner
from tests.test_phase2 import (FakeNotifier, FakeReviewer, FakeReviewGit, FakeVerifier, REVIEW, LANE, ReviewHermes,
                               verdict)

REAL_YAML = Path(pb.ROOT) / "pr_borrador.yaml"
SLUG = "oscarsao/oscar-hq"
URL = "https://github.com/oscarsao/oscar-hq/pull/99"
ON = pb.Ajustes(activo=True, repos=(SLUG,))
LANE_GH = Lane(name="claude-oscarhq", board="oscarhq", repo="C:/r/oscar-hq", base="master", github=SLUG)


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class Sim:
    """Runner que responde a git/gh según lo configurado y apunta cada comando."""

    def __init__(self, *, remota=True, ahead="2", abiertos="[]", fusionados="[]", create=None, gh_rc=0, commits="[]",
                 tip="b" * 40):
        self.cmds = []
        self.remota, self.ahead, self.gh_rc = remota, ahead, gh_rc
        self.commits, self.tip = commits, tip
        self.listas = {"open": abiertos, "merged": fusionados}
        self.create = create or cp(0, URL + "\n")

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        if cmd[0] == "git":
            if "ls-remote" in cmd:
                return cp(0, "a" * 40 + "\trefs/heads/lane/t_1\n" if self.remota else "")
            if "rev-list" in cmd:
                return cp(0, self.ahead + "\n")
            if "rev-parse" in cmd:
                return cp(0, self.tip + "\n" if self.tip else "")
            return cp(0)
        if self.gh_rc and "list" in cmd:
            return cp(self.gh_rc, "", "boom")
        if "number,commits" in cmd:
            return cp(0, self.commits)
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
    assert pb.load_ajustes(REAL_YAML) == pb.Ajustes()  # el fichero del repo viene apagado


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


def test_otro_pr_abierto_que_ya_contiene_sus_commits_no_abre_duplicado():
    tip = "b" * 40
    sim = Sim(tip=tip, commits='[{"number": 7, "commits": [{"oid": "' + tip + '"}]}]')
    res = abrir(sim)
    assert res.estado == pb.OMITIDO and "#7" in res.motivo and sim.creates() == []


def test_otro_pr_abierto_sin_sus_commits_no_lo_impide():
    tip = "b" * 40
    sim = Sim(tip=tip, commits='[{"number": 7, "commits": [{"oid": "' + "c" * 40 + '"}]}]')
    assert abrir(sim).estado == pb.ABIERTO and len(sim.creates()) == 1


def test_fallo_al_listar_prs_abiertos_no_abre_nada():
    sim = Sim(tip="b" * 40, commits="no es json")
    res = abrir(sim)
    assert res.estado == pb.FALLO and sim.creates() == []


# --- acotación en el tiempo (hallazgo 1) -------------------------------------------------------------------------

def test_lectura_con_timeout_corto_y_sin_reintento():
    llamadas = []

    def lento(cmd, **kw):
        llamadas.append(kw.get("timeout"))
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    res = pb.abrir_pr_borrador(LANE_GH, "t_1", "T", "x", ajustes=ON, runner=lento, gh_exe="gh", sleep=lambda s: None)
    assert res.estado == pb.FALLO and "timeout" in res.motivo
    assert llamadas == [pb.TIMEOUT_LECTURA]  # una sola llamada: sin reintento tras timeout


def test_presupuesto_total_agotado_devuelve_fallo():
    t = [0.0]

    def clock():
        return t[0]

    sim = Sim()

    def runner(cmd, **kw):
        assert kw["timeout"] <= pb.TIMEOUT_LECTURA or "create" in cmd
        t[0] += 50  # cada llamada "tarda" 50 s
        return sim(cmd, **kw)

    res = pb.abrir_pr_borrador(LANE_GH, "t_1", "T", "x", ajustes=ON, runner=runner, gh_exe="gh", sleep=lambda s: None,
                               clock=clock)
    assert res.estado == pb.FALLO and "presupuesto" in res.motivo and sim.creates() == []
    assert len(sim.cmds) == 2  # ls-remote y fetch; la tercera lectura ya no cabe


def test_timeout_de_lectura_devuelve_timeout_rc_como_fallo():
    def runner(cmd, **kw):
        return cp(pb._proc.TIMEOUT_RC, "", "timeout (transitorio): x")

    res = pb.abrir_pr_borrador(LANE_GH, "t_1", "T", "x", ajustes=ON, runner=runner, gh_exe="gh", sleep=lambda s: None)
    assert res.estado == pb.FALLO and "ls-remote" in res.motivo


def test_rev_list_no_numerico_es_fallo():
    sim = Sim(ahead="fatal: raro")
    res = abrir(sim)
    assert res.estado == pb.FALLO and "rev-list" in res.motivo and sim.creates() == []


# --- redacción (hallazgo 2) ----------------------------------------------------------------------------------------

def test_cuerpo_y_titulo_se_redactan():
    sim = Sim()
    clave = "sk-ant-abcdefghijklmnopqrstuvwxyz123456"
    pb.abrir_pr_borrador(LANE_GH, "t_1", f"Arreglo con {clave}", f"Falla con API_KEY=hunter2xyz y {clave}", ajustes=ON,
                         runner=sim, gh_exe="gh", sleep=lambda s: None)
    cmd = sim.creates()[0]
    for campo in ("--title", "--body"):
        texto = cmd[cmd.index(campo) + 1]
        assert clave not in texto and "hunter2xyz" not in texto
    assert "[REDACTADO]" in cmd[cmd.index("--body") + 1]


# --- rev-parse sin SHA (hallazgo 3) -------------------------------------------------------------------------------

def test_rev_parse_vacio_o_fallido_no_abre_a_ciegas():
    sim = Sim(tip="")
    res = abrir(sim)
    assert res.estado == pb.FALLO and "rev-parse" in res.motivo and sim.creates() == []

    class RevParseRoto(Sim):
        def __call__(self, cmd, **kw):
            if cmd[0] == "git" and "rev-parse" in cmd:
                self.cmds.append(cmd)
                return cp(128, "", "fatal: bad revision")
            return super().__call__(cmd, **kw)

    sim = RevParseRoto()
    res = abrir(sim)
    assert res.estado == pb.FALLO and "bad revision" in res.motivo and sim.creates() == []


# --- timeout de gh pr create (hallazgo 4) ------------------------------------------------------------------------

class SimCreateTimeout(Sim):
    def __init__(self, existe, **kw):
        super().__init__(**kw)
        self.existe, self.listas_head = existe, 0

    def __call__(self, cmd, **kw):
        if cmd[0] != "git" and "create" in cmd:
            self.cmds.append(cmd)
            raise subprocess.TimeoutExpired(cmd, 60)
        if cmd[0] != "git" and "--head" in cmd and "list" in cmd and "--state" in cmd                 and cmd[cmd.index("--state") + 1] == "open" and any(c[0] != "git" and "create" in c for c in self.cmds):
            self.cmds.append(cmd)
            return cp(0, '[{"number": 9, "url": "%s"}]' % URL if self.existe else "[]")
        return super().__call__(cmd, **kw)


def test_timeout_en_create_con_el_pr_ya_creado_devuelve_abierto():
    res = abrir(SimCreateTimeout(existe=True))
    assert (res.estado, res.url) == (pb.ABIERTO, URL)


def test_timeout_en_create_sin_pr_visible_dice_que_puede_existir():
    res = abrir(SimCreateTimeout(existe=False))
    assert res.estado == pb.FALLO and "no se ve ningún PR" in res.motivo

    class TodoCae(SimCreateTimeout):
        def __call__(self, cmd, **kw):
            if cmd[0] != "git" and "list" in cmd and any(c[0] != "git" and "create" in c for c in self.cmds):
                raise subprocess.TimeoutExpired(cmd, 30)
            return super().__call__(cmd, **kw)

    res = abrir(TodoCae(existe=True))
    assert res.estado == pb.FALLO and "puede existir" in res.motivo


# --- review.py: el PR va DESPUÉS de limpiar y de avisar (hallazgo 1) ------------------------------------------------

def test_review_abre_el_pr_despues_de_limpiar_y_avisar():
    git, notif, vistos = FakeReviewGit(), FakeNotifier(), []
    h = ReviewHermes([{"id": "t_1", "title": "T", "body": "B", "assignee": "claude-oscarhq", "status": "review"}])

    def hook(lane, tid, title, summary):
        vistos.append((list(git.cleaned), len(notif.msgs)))
        return pb.Resultado(pb.OMITIDO)

    r = ReviewRunner(REVIEW, {"claude-oscarhq": LANE}, hermes_for=lambda b: h, git=git,
                     reviewer=FakeReviewer([verdict("approve")]), verifier=FakeVerifier(True), notify=notif,
                     sleep=lambda s: None, pr_borrador=hook)
    assert r.run_once() == {"t_1": "done"}
    assert vistos and vistos[0][0] == ["t_1"] and vistos[0][1] >= 1


def test_gh_por_defecto_reutiliza_la_ruta_de_decisions():
    from agent_lanes import decisions
    assert pb.gh_ejecutable() in (decisions.GH_EXE, "gh")


def test_conftest_aisla_el_yaml_real():
    assert pb.SETTINGS_PATH != REAL_YAML and pb.load_ajustes() == pb.Ajustes()
