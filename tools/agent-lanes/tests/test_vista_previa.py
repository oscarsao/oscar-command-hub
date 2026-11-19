"""Enlace a la vista previa y a staging al cerrar tarjetas de interfaz (vista_previa.py). Dobles de gh y de Hermes."""
import json
from types import SimpleNamespace

from agent_lanes.vista_previa import Ajustes, VistaPrevia, es_tarjeta_ui, load, pantallas, ruta_de

PREVIEW = "https://oscar-hq-git-lane-t-ui1-team.vercel.app"
PR = {"number": 7, "url": "https://github.com/o/r/pull/7", "state": "OPEN", "baseRefName": "main",
      "files": [{"path": "dashboard/src/app/(panel)/ajustes/page.tsx"}, {"path": "dashboard/src/app/page.tsx"},
                {"path": "README.md"}]}


class Hermes:
    def __init__(self, tarjetas):
        self.tarjetas, self.comentarios = tarjetas, []

    def list_status(self, assignee, status):
        return self.tarjetas if status == "review" else []

    def show(self, tid):
        return {"body": ""}

    def comment(self, tid, text, author=None):
        self.comentarios.append((tid, text))
        return True


class Gh:
    """Responde a `gh pr view` y a `gh api repos/.../deployments`."""

    def __init__(self, pr=PR, preview=PREVIEW):
        self.pr, self.preview, self.llamadas = pr, preview, []

    def __call__(self, *args):
        self.llamadas.append(args)
        if args[:2] == ("pr", "view"):
            out = self.pr
        elif args[0] == "api" and "/statuses" in args[1]:
            out = [{"state": "success", "environment_url": self.preview}] if self.preview else []
        elif args[0] == "api":
            out = [{"id": 11}] if self.preview else []
        else:
            out = None
        return SimpleNamespace(returncode=0 if out is not None else 1, stdout=json.dumps(out), stderr="")


def montar(tmp_path, gh, tarjetas=None, activo=False, espera=15, base="main"):
    t = [{"id": "t_ui1", "title": "Pantalla de ajustes", "body": "Tipo: UI"}] if tarjetas is None else tarjetas
    h, enviados, reloj = Hermes(t), [], [1000.0]
    lane = SimpleNamespace(name="claude-oscarhq", board="default", github="o/r")
    vp = VistaPrevia({"x": lane}, lambda board: h, lambda text, markup=None: enviados.append((text, markup)), gh=gh,
                     ajustes=lambda: Ajustes(activo=activo, espera_min=espera), state_path=tmp_path / "vp.json",
                     minutes=0, clock=lambda: reloj[0])
    return vp, h, enviados, reloj


def test_detecta_tarjeta_ui_por_cuerpo_o_por_rutas():
    assert es_tarjeta_ui("Algo\nTipo: UI\nmás", [])
    assert es_tarjeta_ui("", ["dashboard/src/components/Boton.tsx"])
    assert es_tarjeta_ui("", ["dashboard/src/app/x/page.tsx"])
    assert not es_tarjeta_ui("Tipo: backend", ["tools/agent-lanes/x.py"])


def test_rutas_de_pantallas():
    assert ruta_de("dashboard/src/app/page.tsx") == "/"
    assert ruta_de("dashboard/src/app/(panel)/ajustes/[id]/page.tsx") == "/ajustes/[id]"
    assert ruta_de("dashboard/src/app/ajustes/layout.tsx") is None
    assert ruta_de("dashboard/src/components/page.tsx") is None
    assert pantallas(["dashboard/src/app/a/page.tsx", "dashboard/src/app/a/page.tsx", "x.py"]) == ["/a"]


def test_modo_seco_lista_comentario_y_aviso_sin_enviar(tmp_path):
    vp, h, enviados, _ = montar(tmp_path, Gh())
    assert vp.tick() == 1
    assert h.comentarios == [] and enviados == []  # seco: nada real
    reg = json.loads((tmp_path / "vp.json").read_text(encoding="utf-8"))["seco"]
    assert len(reg) == 1 and reg[0]["resultado"] == "enviado"
    assert f"{PREVIEW}/ajustes" in reg[0]["comentario"]
    assert "• / → " + PREVIEW in reg[0]["comentario"]
    assert reg[0]["botones"] == [{"text": "Abrir", "url": PREVIEW},
                                 {"text": "Abrir /ajustes", "url": f"{PREVIEW}/ajustes"}]
    assert vp.tick() == 0  # no repite el registro en seco


def test_real_comenta_y_avisa_con_boton_abrir(tmp_path):
    vp, h, enviados, _ = montar(tmp_path, Gh(), activo=True)
    assert vp.tick() == 1
    assert h.comentarios[0][0] == "t_ui1" and f"{PREVIEW}/ajustes" in h.comentarios[0][1]
    texto, markup = enviados[0]
    assert "t_ui1" in texto and markup["inline_keyboard"][0][0] == {"text": "Abrir", "url": PREVIEW}
    assert vp.tick() == 0 and len(enviados) == 1  # una sola vez


def test_staging_solo_si_el_pr_apunta_a_staging(tmp_path):
    pr = {**PR, "baseRefName": "staging"}
    vp, h, _, _ = montar(tmp_path, Gh(pr=pr), activo=True)
    vp.tick()
    assert "https://stage.pildoradigital.com/ajustes" in h.comentarios[0][1]
    vp2, h2, _, _ = montar(tmp_path / "otro", Gh(), activo=True)
    vp2.tick()
    assert "stage.pildoradigital.com" not in h2.comentarios[0][1]


def test_sin_vista_previa_reintenta_y_pasados_los_minutos_avisa_sin_enlace_vacio(tmp_path):
    vp, h, enviados, reloj = montar(tmp_path, Gh(preview=None), activo=True)
    assert vp.tick() == 0 and enviados == []
    reloj[0] += 14 * 60
    assert vp.tick() == 0 and enviados == []  # aún dentro de los 15 minutos
    reloj[0] += 2 * 60
    assert vp.tick() == 1
    texto, markup = enviados[0]
    assert "aún no está lista" in texto and h.comentarios and "aún no está lista" in h.comentarios[0][1]
    assert markup["inline_keyboard"] == [[{"text": "Ver PR", "url": PR["url"]}]]
    assert all(b["url"] for fila in markup["inline_keyboard"] for b in fila)
    assert vp.tick() == 0 and len(enviados) == 1


def test_vista_previa_que_llega_en_el_reintento(tmp_path):
    gh = Gh(preview=None)
    vp, _, enviados, reloj = montar(tmp_path, gh, activo=True)
    vp.tick()
    gh.preview = PREVIEW
    reloj[0] += 60
    assert vp.tick() == 1 and enviados[0][1]["inline_keyboard"][1][0]["url"] == f"{PREVIEW}/ajustes"


def test_ignora_no_ui_pr_cerrado_y_sin_pr(tmp_path):
    no_ui = [{"id": "t_x", "title": "Backend", "body": "Tipo: backend"}]
    pr = {**PR, "files": [{"path": "tools/agent-lanes/x.py"}]}
    vp, h, enviados, _ = montar(tmp_path, Gh(pr=pr), tarjetas=no_ui, activo=True)
    assert vp.tick() == 0 and enviados == []
    vp, _, enviados, _ = montar(tmp_path / "b", Gh(pr={**PR, "state": "MERGED"}), activo=True)
    assert vp.tick() == 0 and enviados == []
    vp, _, enviados, _ = montar(tmp_path / "c", Gh(pr=None), activo=True)
    assert vp.tick() == 0 and enviados == []


def test_tarjeta_ui_solo_por_componentes_enlaza_la_vista_previa(tmp_path):
    pr = {**PR, "files": [{"path": "dashboard/src/components/Boton.tsx"}]}
    vp, _, enviados, _ = montar(tmp_path, Gh(pr=pr), tarjetas=[{"id": "t_c", "title": "Botón", "body": ""}], activo=True)
    assert vp.tick() == 1
    assert enviados[0][1]["inline_keyboard"][0][0]["url"] == PREVIEW


def test_un_fallo_de_gh_no_lanza_ni_avisa(tmp_path):
    def roto(*a):
        raise OSError("sin red")
    vp, _, enviados, _ = montar(tmp_path, roto, activo=True)
    assert vp.tick() == 0 and enviados == []


def test_load_por_defecto_en_seco_y_lee_el_bloque(tmp_path):
    assert load(tmp_path / "no_existe.yaml") == Ajustes()
    assert Ajustes().activo is False
    f = tmp_path / "l.yaml"
    f.write_text("vista_previa: {enabled: true, espera_min: 5, staging_rama: develop}\n", encoding="utf-8")
    assert load(f) == Ajustes(activo=True, espera_min=5, staging_rama="develop")
    from agent_lanes.config import ROOT
    assert load(ROOT / "lanes.yaml").activo is False  # el lanes.yaml del repo trae el modo seco
