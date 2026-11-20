"""Prueba en navegador sin bloquear (verificacion.py): el worker termina con etiqueta y no con needs_input, la bandeja y el
panel listan las pendientes con su PR y el integrador distingue «pendiente» de «hecha». Dobles: sin red ni hermes real."""
from __future__ import annotations

from agent_lanes import bandeja, verificacion
from agent_lanes.config import Lane
from agent_lanes.hermes import OSCAR_AUTHOR
from agent_lanes.panel_vivo import PanelSettings, render_sections
from agent_lanes.worker import build_prompt
from tests.test_integrator import OHQ_SLUG, World, approved, make
from tests.test_runner_state import GOOD, ok_outcome
from tests.test_runner_state import make as make_runner

PR_URL = f"https://github.com/{OHQ_SLUG}/pull/7"
PENDIENTE = (f"VERIFICACIÓN PENDIENTE · PR {PR_URL}\nRutas: /panel. Anchos: 390 px. Claro y oscuro. "
             "Arnés: tools/arnes-390.mjs")
HECHA = {"author": "sesion-mejoras", "body": "VERIFICACIÓN HECHA · 390 px claro y oscuro OK · capturas: x.png"}
OMITIDA = {"author": OSCAR_AUTHOR, "body": "VERIFICACIÓN OMITIDA: integra sin la prueba"}


def _show(*bodies, author="carriles"):
    return {"task": {"id": "t_1"}, "comments": [{"author": author, "body": b} for b in bodies], "runs": []}


# --- (a) el worker termina con etiqueta, no con needs_input ----------------------------------------------

def test_el_prompt_del_worker_manda_terminar_con_etiqueta_y_no_pararse():
    lane = Lane(name="claude-hub", board="default", repo="C:/repo", base="main")
    prompt = build_prompt({"id": "t_9", "title": "T", "body": "arnés 390 px"}, lane)
    assert "NO te pares" in prompt and "NO devuelvas `needs_input`" in prompt
    assert "VERIFICACIÓN PENDIENTE" in prompt and "VERIFICACIÓN HECHA" in prompt
    assert "EN BORRADOR" in prompt and "rutas" in prompt and "anchos" in prompt and "arnés" in prompt


def test_tarjeta_con_etiqueta_pasa_a_review_y_no_a_needs_input():
    r, h, *_ = make_runner([ok_outcome({**GOOD, "summary": PENDIENTE})])
    assert r.run_once() == {"t_1": "review"}
    review = next(c for c in h.calls if c[0] == "review")
    assert review[2].startswith("VERIFICACIÓN PENDIENTE")  # la etiqueta llega a la tarjeta con el resumen
    assert not [c for c in h.calls if c[0] == "block"]
    # ...y esa tarjeta se reconoce como pendiente a partir de lo que guarda el kanban
    show = {"task": {"id": "t_1", "result": review[2]}, "comments": [], "runs": [{"metadata": review[3]}]}
    assert verificacion.estado(show) == verificacion.PENDIENTE


# --- estado ------------------------------------------------------------------------------------------------

def test_estado_ultima_marca_manda():
    assert verificacion.estado(_show("hola")) is None
    assert verificacion.estado(_show(PENDIENTE)) == "pendiente"
    assert verificacion.estado(_show(PENDIENTE, HECHA["body"])) == "hecha"
    assert verificacion.estado(_show(PENDIENTE, HECHA["body"], "VERIFICACIÓN FALLIDA en /panel a 390 px")) == "pendiente"
    assert verificacion.estado(_show(HECHA["body"], PENDIENTE)) == "pendiente"  # reabierta y vuelta a dejar la etiqueta


def test_la_marca_solo_cuenta_al_inicio_de_linea():
    assert verificacion.estado(_show("Si falta, deja la etiqueta VERIFICACIÓN PENDIENTE en el PR")) is None
    assert verificacion.estado(_show("resumen\n**VERIFICACIÓN PENDIENTE**: rutas")) == "pendiente"
    assert verificacion.estado(_show("verificacion pendiente: sin tildes")) == "pendiente"


def test_omitida_solo_vale_si_la_escribe_oscar():
    assert verificacion.estado(_show(PENDIENTE, OMITIDA["body"], author="carriles")) == "pendiente"
    show = {"task": {}, "comments": [{"author": "carriles", "body": PENDIENTE}, OMITIDA], "runs": []}
    assert verificacion.estado(show) == "omitida"


def test_la_etiqueta_del_resumen_vale_hasta_que_alguien_la_haga():
    show = {"task": {"id": "t_1", "result": PENDIENTE}, "comments": [], "runs": []}
    assert verificacion.estado(show) == "pendiente"
    assert verificacion.estado({**show, "comments": [HECHA]}) == "hecha"


# --- (b) bandeja y panel listan las pendientes con su PR ------------------------------------------------------

class FakeHermes:
    def __init__(self, shows):
        self.shows = shows

    def show(self, tid):
        if tid == "t_roto":
            raise RuntimeError("hermes caído")
        return self.shows[tid]


def test_bandeja_lista_solo_las_pendientes_con_su_pr():
    h = FakeHermes({"t_a": _show(PENDIENTE), "t_b": _show(PENDIENTE, HECHA["body"]), "t_c": _show("nada"),
                    "t_d": _show("VERIFICACIÓN PENDIENTE")})
    tasks = [{"id": t, "title": f"Tarjeta {t}"} for t in ("t_a", "t_b", "t_c", "t_d", "t_roto")]
    entries = bandeja.verificacion_pendiente(h, tasks)
    assert [e["id"] for e in entries] == ["t_a", "t_d"]  # t_roto no tumba la lista
    assert entries[0]["pr"] == 7 and entries[0]["url"] == PR_URL and entries[1]["pr"] is None
    text = bandeja.render_verificacion(entries)
    assert "Esperan prueba en navegador" in text and "t_a" in text and "PR #7" in text and "sin PR" in text
    assert "t_b" not in text and bandeja.render_verificacion([]) == ""


def test_panel_muestra_la_seccion_solo_si_hay_pendientes():
    base = {"running": [], "queue": [], "done": [], "conflicts": [], "now": 1e6, "cfg": PanelSettings(enabled=True)}
    assert "PRUEBA EN NAVEGADOR" not in render_sections(**base)
    text = render_sections(**base, verificacion=[{"id": "t_a", "title": "Panel", "pr": 7, "url": PR_URL}])
    assert "ESPERAN PRUEBA EN NAVEGADOR" in text and "#7" in text and "t_a" in text


# --- (c) la regla de integración las distingue ---------------------------------------------------------------

def _pasada(tmp_path, comments):
    integ, w, *_ = make(tmp_path, comments=comments, world=World())
    return integ, w, integ.run_pass()


def test_integrador_no_integra_con_verificacion_pendiente(tmp_path):
    integ, w, res = _pasada(tmp_path, [approved(), {"author": "carriles", "body": PENDIENTE}])
    assert res == {} and not w.argv("merge")
    st = integ._state("t_1")
    assert st["status"] == "pr_problem" and "VERIFICACIÓN PENDIENTE" in st["pr_problem"]


def test_integrador_integra_con_verificacion_hecha(tmp_path):
    _, _, res = _pasada(tmp_path, [approved(), {"author": "carriles", "body": PENDIENTE}, HECHA])
    assert res == {"t_1": "offered"}


def test_integrador_integra_con_decision_expresa_de_oscar(tmp_path):
    _, _, res = _pasada(tmp_path, [approved(), {"author": "carriles", "body": PENDIENTE}, OMITIDA])
    assert res == {"t_1": "offered"}


def test_integrador_sin_etiqueta_sigue_igual(tmp_path):
    _, _, res = _pasada(tmp_path, [approved()])
    assert res == {"t_1": "offered"}


def test_problema_integracion_texto():
    assert "VERIFICACIÓN HECHA" in verificacion.problema_integracion(_show(PENDIENTE))
    assert verificacion.problema_integracion(_show(PENDIENTE, HECHA["body"])) is None
    assert verificacion.problema_integracion(_show("sin etiqueta")) is None


# --- (b') el comando de la bandeja (/decisiones) envía la lista como mensaje propio -------------------------------

class _HermesReview(FakeHermes):
    def __init__(self, shows, review):
        super().__init__(shows)
        self.review = review

    def list_status_all(self, status):
        return self.review if status == "review" else []


def _bandeja_cmd(tmp_path, shows, review):
    from tests.test_commands import OSCAR, center, command
    cc, desk, bot, _, _ = center(tmp_path, {})
    cc.hermes_for = lambda b: _HermesReview(shows, review)
    command(desk, "/decisiones", user=OSCAR, chat=OSCAR)
    return bot


def test_comando_de_la_bandeja_envia_la_lista_de_pendientes(tmp_path):
    shows = {"t_a": _show(PENDIENTE), "t_b": _show(PENDIENTE, HECHA["body"])}
    bot = _bandeja_cmd(tmp_path, shows, [{"id": "t_a", "title": "Panel"}, {"id": "t_b", "title": "Otra"}])
    lista = [m for m in bot.sent if "Esperan prueba en navegador" in m["text"]]
    assert lista and "t_a" in lista[0]["text"] and "PR #7" in lista[0]["text"] and "t_b" not in lista[0]["text"]


def test_comando_de_la_bandeja_no_envia_nada_si_no_hay_pendientes(tmp_path):
    bot = _bandeja_cmd(tmp_path, {"t_c": _show("nada")}, [{"id": "t_c", "title": "X"}])
    assert not [m for m in bot.sent if "Esperan prueba" in m["text"]]
