"""Recolector nocturno (t_88f59559): limpio y fusionado se quita; sucio, sin empujar, sin fusionar o de tarjeta viva se
conserva; --seco no borra; jamás --force ni -D. Todo con un git falso: sin repos, red ni hermes reales."""
import json
from datetime import datetime
from types import SimpleNamespace

from agent_lanes.recolector import Ajustes, Recolector, RecolectorTicker, load, main
from agent_lanes.resumen_carriles import MADRID

REPO = "C:/repos/hub"
RAIZ = "C:/repos/_lanes"


class FakeGit:
    """Responde a los `git -C <dir> ...` que usa el recolector según el escenario por tid."""

    def __init__(self, tids, sucios=(), sin_empujar=(), sin_fusionar=()):
        self.tids, self.sucios, self.sin_empujar, self.sin_fusionar = tids, set(sucios), set(sin_empujar), set(sin_fusionar)
        self.llamadas = []

    def __call__(self, cmd, **kw):
        self.llamadas.append(cmd)
        d, args = cmd[2], cmd[3:]
        out, rc = "", 0
        tid = d.rsplit("lane-", 1)[-1] if "lane-" in d else None
        if args[:2] == ["worktree", "list"]:
            out = "\n".join([f"worktree {REPO}", *(f"worktree {RAIZ}/lane-{t}" for t in self.tids), ""])
        elif args[0] == "status":
            out = " M x.py\n" if tid in self.sucios else ""
        elif args[0] == "rev-list":
            out = "2\n" if tid in self.sin_empujar else "0\n"
        elif args[0] == "rev-parse":
            rc = 0
        elif args[:2] == ["merge-base", "--is-ancestor"]:
            rc = 1 if args[2].split("/")[-1] in self.sin_fusionar else 0
        elif args[0] == "for-each-ref":
            out = "\n".join(f"lane/{t}" for t in self.tids)
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")

    def con(self, *prefijo):
        return [c for c in self.llamadas if tuple(c[3:3 + len(prefijo)]) == prefijo]


class Hermes:
    def __init__(self, hechas=(), archivadas=()):
        self.por_estado = {"done": [{"id": t} for t in hechas], "archived": [{"id": t} for t in archivadas]}

    def list_status(self, assignee, status, sort="priority"):
        return self.por_estado.get(status, [])


def lanes():
    lane = SimpleNamespace(name="claude-hub", board="default", repo=REPO, base="main", remote="origin", kind="implement",
                           worktree_root=RAIZ, base_ref="origin/main")
    return {"claude-hub": lane}


def recolector(git, hermes, tmp_path, merged_pr=None):
    return Recolector(lanes(), lambda board: hermes, run=git, merged_pr=merged_pr, state_path=tmp_path / "rec.json")


def test_limpio_y_fusionado_se_quita_y_borra_rama_con_d(tmp_path):
    git = FakeGit(["t_ok"])
    inf = recolector(git, Hermes(hechas=["t_ok"]), tmp_path).recoger(seco=False)
    assert inf.quitados == ["hub: lane-t_ok"] and inf.conservados == []
    assert len(git.con("worktree", "remove")) == 1 and git.con("branch", "-d", "lane/t_ok")
    todas = [a for c in git.llamadas for a in c]
    assert "--force" not in todas and "-D" not in todas and "-f" not in todas


def test_sucio_sin_empujar_sin_fusionar_y_viva_se_conservan(tmp_path):
    tids = ["t_sucio", "t_noemp", "t_nofus", "t_viva", "t_arch"]
    git = FakeGit(tids, sucios=["t_sucio"], sin_empujar=["t_noemp"], sin_fusionar=["t_nofus"])
    inf = recolector(git, Hermes(hechas=["t_sucio", "t_noemp", "t_nofus"], archivadas=["t_arch"]), tmp_path).recoger(seco=False)
    motivos = dict(inf.conservados)
    assert "sin commitear" in motivos["hub: lane-t_sucio"]
    assert "sin empujar" in motivos["hub: lane-t_noemp"]
    assert "sin fusionar" in motivos["hub: lane-t_nofus"]
    assert "done/archived" in motivos["hub: lane-t_viva"]  # running/ready/review: no está cerrada
    assert inf.quitados == ["hub: lane-t_arch"]
    assert len(git.con("worktree", "remove")) == 1


def test_pr_fusionado_vale_aunque_la_rama_no_sea_ancestro(tmp_path):
    git = FakeGit(["t_sq"], sin_fusionar=["t_sq"])
    sin_pr = recolector(git, Hermes(hechas=["t_sq"]), tmp_path).recoger(seco=True)
    assert sin_pr.quitados == []
    con_pr = recolector(FakeGit(["t_sq"], sin_fusionar=["t_sq"]), Hermes(hechas=["t_sq"]), tmp_path,
                        merged_pr=lambda lane, tid: "https://pr/1").recoger(seco=True)
    assert con_pr.quitados == ["hub: lane-t_sq"]


def test_seco_no_borra_nada_y_deja_registro(tmp_path):
    git = FakeGit(["t_ok"])
    inf = recolector(git, Hermes(hechas=["t_ok"]), tmp_path).recoger(seco=True)
    assert inf.quitados == ["hub: lane-t_ok"] and inf.seco
    assert not git.con("worktree", "remove") and not git.con("branch", "-d", "lane/t_ok")
    assert "SECO" in inf.texto() and "Quitaría" in inf.texto()
    assert json.loads((tmp_path / "rec.json").read_text(encoding="utf-8"))["ultimo"]["quitados"] == ["hub: lane-t_ok"]


def test_si_no_se_pueden_listar_tarjetas_se_conserva_todo(tmp_path):
    class Roto:
        def list_status(self, *a, **k):
            raise RuntimeError("hermes caído")
    git = FakeGit(["t_ok"])
    inf = recolector(git, Roto(), tmp_path).recoger(seco=False)
    assert inf.quitados == [] and inf.conservados and not git.con("worktree", "remove")


def test_main_es_seco_salvo_real(tmp_path):
    git, lineas = FakeGit(["t_ok"]), []
    rec = recolector(git, Hermes(hechas=["t_ok"]), tmp_path)
    assert main([], recolector=rec, out=lineas.append) == 0
    assert not git.con("worktree", "remove") and "SECO" in lineas[0]


def test_ajustes_apagado_por_defecto_y_validos(tmp_path):
    assert load(tmp_path / "no_existe.yaml") == Ajustes() and not Ajustes().activo and Ajustes().seco
    f = tmp_path / "l.yaml"
    f.write_text("recolector: {enabled: true, hora: 25, seco: false}\n", encoding="utf-8")
    assert load(f) == Ajustes(activo=True, hora=3, seco=False)  # hora inválida = defecto


def test_ticker_una_vez_al_dia_y_solo_si_esta_activo(tmp_path):
    git = FakeGit(["t_ok"])
    rec = recolector(git, Hermes(hechas=["t_ok"]), tmp_path)
    t = [datetime(2026, 11, 3, 4, 0, tzinfo=MADRID).timestamp()]
    off = RecolectorTicker(rec, ajustes=lambda: Ajustes(), minutes=0, clock=lambda: t[0])
    assert off.tick() is None  # apagado
    on = RecolectorTicker(rec, ajustes=lambda: Ajustes(activo=True, hora=3), minutes=0, clock=lambda: t[0])
    assert on.tick() is not None and on.tick() is None  # segunda pasada del mismo día: nada
    t[0] += 86400
    assert on.tick() is not None
    assert not git.con("worktree", "remove")  # seco por defecto
