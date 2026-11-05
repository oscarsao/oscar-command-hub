"""Recolector nocturno (t_88f59559): limpio y fusionado se quita; sucio, sin empujar, sin fusionar o de tarjeta viva se
conserva; --seco no borra; jamás --force ni -D. Todo con un git falso: sin repos, red ni hermes reales."""
import json
from datetime import datetime
from types import SimpleNamespace

from agent_lanes.recolector import Ajustes, Informe, Recolector, RecolectorTicker, legacy_tras_fallo, load, main
from agent_lanes.resumen_carriles import MADRID

REPO = "C:/repos/hub"
RAIZ = "C:/repos/_lanes"


class FakeGit:
    """Responde a los `git -C <dir> ...` que usa el recolector según el escenario por tid."""

    def __init__(self, tids, sucios=(), sin_empujar=(), sin_fusionar=(), remotas=(), locales=(), pr_refs=(),
                 en_base=()):
        self.tids, self.sucios, self.sin_empujar, self.sin_fusionar = tids, set(sucios), set(sin_empujar), set(sin_fusionar)
        self.remotas, self.locales, self.pr_refs, self.en_base = list(remotas), list(locales), list(pr_refs), set(en_base)
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
        elif args == ["rev-parse", "origin/main"]:
            out = "base0\n"
        elif args[0] == "rev-parse":
            rc = 0
        elif args[:2] == ["merge-base", "--is-ancestor"]:
            rc = 1 if args[2].split("/")[-1] in self.sin_fusionar else 0
        elif args[:3] == ["branch", "-r", "--merged"]:
            out = "\n".join(["  origin/HEAD -> origin/main", *(f"  origin/{b}" for b in self.remotas)])
        elif args[:2] == ["branch", "--merged"]:
            out = "\n".join(self.locales)
        elif args[0] == "for-each-ref" and args[-1] == "refs/remotes/origin/":
            out = "\n".join(f"origin/{b} {'base0' if b in self.en_base else 'sha_' + b}" for b in self.remotas)
        elif args[0] == "for-each-ref" and args[-1] == "refs/remotes/pr/":
            out = "\n".join(f"refs/remotes/pr/{n} {sha}" for n, sha in self.pr_refs)
        elif args[0] == "for-each-ref":
            out = "\n".join(f"lane/{t}" for t in self.tids)
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")

    def con(self, *prefijo):
        return [c for c in self.llamadas if tuple(c[3:3 + len(prefijo)]) == prefijo]


class Hermes:
    def __init__(self, hechas=(), archivadas=(), abiertas=()):
        self.por_estado = {"done": [{"id": t} for t in hechas], "archived": [{"id": t} for t in archivadas],
                           "running": [{"id": t} for t in abiertas]}

    def list_status(self, assignee, status, sort="priority"):
        return self.por_estado.get(status, [])


def lanes():
    lane = SimpleNamespace(name="claude-hub", board="default", repo=REPO, base="main", remote="origin", kind="implement",
                           worktree_root=RAIZ, base_ref="origin/main")
    return {"claude-hub": lane}


def ajustes_de(tmp_path, **kw):
    return lambda: Ajustes(legacy=str(tmp_path / "legacy"), **kw)


def recolector(git, hermes, tmp_path, merged_pr=None, prs=(), fusionados=(), aj=None, **kw):
    return Recolector(lanes(), lambda board: hermes, run=git, merged_pr=merged_pr, state_path=tmp_path / "rec.json",
                      ajustes=aj or ajustes_de(tmp_path), prs_abiertos=lambda lane: set(prs) if prs is not None else None,
                      prs_fusionados=lambda lane: set(fusionados), actividad=lambda p, h: p.endswith("t_activo"), **kw)


def test_limpio_y_fusionado_se_quita_y_borra_rama_con_d(tmp_path):
    git = FakeGit(["t_ok"])
    inf = recolector(git, Hermes(hechas=["t_ok"]), tmp_path).recoger(seco=False)
    assert inf.quitados == ["hub: lane-t_ok"] and inf.conservados == []
    assert len(git.con("worktree", "remove")) == 1 and git.con("branch", "-d", "lane/t_ok")
    todas = [a for c in git.llamadas for a in c]
    assert "--force" not in todas and "-D" not in todas and "-f" not in todas


def test_sucio_sin_empujar_y_sin_fusionar_van_a_legacy_y_la_viva_se_conserva(tmp_path):
    tids = ["t_sucio", "t_noemp", "t_nofus", "t_viva", "t_arch"]
    git = FakeGit(tids, sucios=["t_sucio"], sin_empujar=["t_noemp"], sin_fusionar=["t_nofus"])
    inf = recolector(git, Hermes(hechas=["t_sucio", "t_noemp", "t_nofus"], archivadas=["t_arch"]), tmp_path).recoger(seco=False)
    assert dict(inf.conservados) == {"hub: lane-t_viva": "la tarjeta no está en done/archived"}
    assert len(inf.legacy) == 3 and inf.quitados == ["hub: lane-t_arch"]
    assert len(git.con("worktree", "remove")) == 1 and len(git.con("worktree", "move")) == 3
    mover = git.con("worktree", "move")[0]
    assert mover[6].replace("\\", "/").startswith(str(tmp_path / "legacy").replace("\\", "/") + "/hub/lane-t_")
    leeme = (tmp_path / "legacy" / "hub" / "LEEME.txt").read_text(encoding="utf-8")
    assert "cambios sin guardar: sí" in leeme and "lane/t_nofus" in leeme and "rama fusionada: no" in leeme
    todas = [a for c in git.llamadas for a in c]
    assert "--force" not in todas and "-D" not in todas and "-f" not in todas


def test_protecciones_tarjeta_abierta_actividad_y_pr_abierto(tmp_path):
    git = FakeGit(["t_run", "t_activo", "t_conpr", "t_prot"], sucios=["t_run", "t_activo", "t_conpr", "t_prot"])
    aj = ajustes_de(tmp_path, protegidas=("lane/t_prot",))
    inf = recolector(git, Hermes(hechas=["t_activo", "t_conpr", "t_prot"], abiertas=["t_run"]), tmp_path,
                     prs=["lane/t_conpr"], aj=aj).recoger(seco=False)
    motivos = dict(inf.conservados)
    assert "sigue abierta" in motivos["hub: lane-t_run"]
    assert "actividad" in motivos["hub: lane-t_activo"]
    assert "PR abierto" in motivos["hub: lane-t_conpr"]
    assert "protección" in motivos["hub: lane-t_prot"]
    assert not git.con("worktree", "move") and not git.con("worktree", "remove")


def test_sin_poder_leer_prs_no_se_mueve_ni_se_borra_ninguna_rama(tmp_path):
    git = FakeGit(["t_sucio"], sucios=["t_sucio"], remotas=["feat/x"])
    inf = recolector(git, Hermes(hechas=["t_sucio"]), tmp_path, prs=None).recoger(seco=False)
    assert not git.con("worktree", "move") and not git.con("push") and inf.legacy == []
    assert any("PR abiertos" in m for _, m in inf.conservados)


def test_ramas_remotas_fusionadas_se_borran_salvo_protegidas(tmp_path):
    remotas = ["lane/t_old", "master", "main", "develop", "staging", "integ/ui-g1", "feat/con-pr",
               "lane/t_abc12345", "feat/recien", "integ/2026-11-03"]
    git = FakeGit([], remotas=remotas, en_base=["feat/recien"], locales=["main", "lane/t_old", "integ/ui-g1", "otra"])
    aj = ajustes_de(tmp_path, protegidas=("integ/ui-g*",))
    inf = recolector(git, Hermes(abiertas=["t_abc12345"]), tmp_path, prs=["feat/con-pr"], aj=aj).recoger(seco=False)
    borradas = [a for c in git.con("push", "origin", "--delete") for a in c[6:]]
    assert sorted(borradas) == ["integ/2026-11-03", "lane/t_old"]  # no: base/protegidas, PR abierto, tarjeta abierta, recién creada
    assert inf.remotas == 2 and inf.protegidas == 7
    assert [c[5] for c in git.con("branch", "-d")] == ["lane/t_old", "otra"] and inf.locales == 2


def test_simulacion_cuenta_pero_no_borra_nada(tmp_path):
    git = FakeGit(["t_sucio", "t_ok"], sucios=["t_sucio"], remotas=["lane/t_old"], locales=["lane/t_old"],
                  pr_refs=[(7, "sha7")])
    inf = recolector(git, Hermes(hechas=["t_sucio", "t_ok"]), tmp_path).recoger(seco=True)
    assert inf.remotas == 1 and inf.locales == 1 and inf.refs_pr == 1 and len(inf.legacy) == 1 and inf.quitados
    for orden in (("push",), ("worktree", "move"), ("worktree", "remove"), ("branch", "-d"), ("update-ref",)):
        assert not git.con(*orden), orden
    assert not (tmp_path / "legacy").exists()
    assert "simulación" in inf.resumen()


def test_refs_pr_solo_de_prs_fusionados(tmp_path):
    git = FakeGit([], pr_refs=[(1, "sha1"), (2, "pr_nofus"), (3, "pr_nofus")], sin_fusionar=["pr_nofus"])
    inf = recolector(git, Hermes(), tmp_path, fusionados=[3]).recoger(seco=False)
    borradas = [c[5] for c in git.con("update-ref")]
    assert borradas == ["refs/remotes/pr/1", "refs/remotes/pr/3"] and inf.refs_pr == 2


def test_resumen_unico_con_contadores_y_nada_si_no_hay_nada(tmp_path):
    git = FakeGit(["t_sucio", "t_ok"], sucios=["t_sucio"], remotas=["lane/t_old"])
    inf = recolector(git, Hermes(hechas=["t_sucio", "t_ok"]), tmp_path).recoger(seco=False)
    r = inf.resumen(str(tmp_path / "legacy"))
    assert r.startswith("🧹") and "Borradas: 2" in r and "Movidas a legacy: 1" in r and "legacy" in r.splitlines()[-1]
    assert Informe().resumen() is None


def test_ticker_manda_un_unico_resumen_al_dia(tmp_path):
    git, avisos = FakeGit(["t_ok"]), []
    rec = recolector(git, Hermes(hechas=["t_ok"]), tmp_path)
    t = [datetime(2026, 11, 3, 4, 0, tzinfo=MADRID).timestamp()]
    tk = RecolectorTicker(rec, ajustes=lambda: Ajustes(activo=True, hora=3), minutes=0, clock=lambda: t[0],
                          aviso=avisos.append)
    tk.tick()
    tk.tick()
    assert len(avisos) == 1 and "Borrarían" not in avisos[0] and "simulación" in avisos[0]


def test_borrar_rama_integrada_solo_con_apply_y_para_su_lane(tmp_path):
    pr = {"headRefName": "lane/t_ok", "url": "https://github.com/o/hub/pull/9"}
    git = FakeGit(["t_ok"])
    rec = recolector(git, Hermes(), tmp_path, aj=ajustes_de(tmp_path, activo=True, seco=True))
    rec.lanes["claude-hub"].github = "o/hub"
    assert rec.borrar_rama_integrada(pr, "t_ok", "done") is False and not git.con("push")  # simulación
    rec.ajustes = ajustes_de(tmp_path, activo=True, seco=False)
    assert rec.borrar_rama_integrada(pr, "t_ok", "running") is False  # tarjeta en curso
    assert rec.borrar_rama_integrada({**pr, "headRefName": "feat/x"}, "t_ok", "done") is False
    assert rec.borrar_rama_integrada(pr, "t_ok", "done") is True
    assert git.con("push", "origin", "--delete", "lane/t_ok") and git.con("branch", "-d", "lane/t_ok")


def test_legacy_tras_fallo_solo_con_apply(tmp_path):
    raiz = tmp_path / "_lanes"
    (raiz / "lane-t_x").mkdir(parents=True)
    lane = SimpleNamespace(repo=REPO, worktree_root=str(raiz))
    git = FakeGit(["t_x"], sucios=["t_x"])
    assert legacy_tras_fallo(lane, "t_x", run=git, ajustes=ajustes_de(tmp_path, activo=True, seco=True)) == (False, "")
    assert not git.con("worktree", "move")
    ok, dest = legacy_tras_fallo(lane, "t_x", run=git, ajustes=ajustes_de(tmp_path, activo=True, seco=False))
    assert ok and git.con("worktree", "move") and (tmp_path / "legacy" / "hub" / "LEEME.txt").exists()
    assert legacy_tras_fallo(lane, "t_nada", run=git, ajustes=ajustes_de(tmp_path, activo=True, seco=False)) == (False, "")


def test_sweep_done_a_legacy_en_vez_de_limpieza_pendiente(tmp_path):
    from agent_lanes.review import sweep_done
    (tmp_path / "lane-t_z").mkdir()
    lane = SimpleNamespace(name="claude-hub", worktree_root=str(tmp_path))

    class G:
        def cleanup(self, lane, tid):
            return False, "tiene cambios"
    movidos = []
    h = SimpleNamespace(list_status=lambda a, s: [{"id": "t_z"}])
    assert sweep_done(lane, h, G(), legacy=lambda l, t: (movidos.append(t) or True, "dest")) == ["t_z"]
    assert movidos == ["t_z"]
    assert sweep_done(lane, h, G(), legacy=lambda l, t: (False, "")) == []


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
