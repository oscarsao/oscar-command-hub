"""🚀 Arrancar (31-10): botón de la ficha, /arrancar, las tres listas de /pendientes y la regla de creación sin carril.
Dobles de Telegram y de Hermes; sin red ni hermes real."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_lanes import arrancar as arr
from agent_lanes.hermes import HermesCLI
from tests.test_commands import LANES, KanbanHermes, center, command, press, texts


def _card(tid, title, *, assignee="oscar", status="ready"):
    return {"task": {"id": tid, "title": title, "body": "## Objetivo\nAlgo.\n", "assignee": assignee,
                     "status": status, "created_at": 10}, "runs": [], "events": [], "comments": []}


class Kan(KanbanHermes):
    """Kanban en memoria con las escrituras de Arrancar: reassign no cambia el estado (como Hermes)."""

    def list_status_all(self, status):
        return [{"id": t, **s["task"]} for t, s in self.shows.items() if s["task"]["status"] == status]

    def reassign(self, tid, profile, reason):
        self.calls.append(("reassign", tid, profile, reason))
        self.shows[tid]["task"]["assignee"] = profile
        return True


def _env(tmp_path, shows):
    """Tablero `oscarhq` con las tarjetas dadas; los demás tableros, vacíos."""
    cc, desk, bot, h, _ = center(tmp_path, {})
    kan = Kan(shows)
    vacio = Kan({})
    cc.hermes_for = lambda board: kan if board == "oscarhq" else vacio
    return cc, desk, bot, kan


CODIGO = "[Hub] Añadir columna · implementar"


# --- piezas puras -------------------------------------------------------------------------------------

@pytest.mark.parametrize("title,assignee,esperado", [
    ("[Hub] Algo · implementar", "oscar", True),
    ("Sin formato", "oscar", True),
    ("[Hub] Elegir · decidir", "oscar", False),
    ("[Personal] DNI · tuyo", "oscar", False),
    ("[Hub] Fusionar PR · integrador", "oscar", False),
    ("[Hub] Algo · implementar", "claude-hub", False)])
def test_regla_de_creacion(title, assignee, esperado):
    assert arr.sin_carril_ni_marca(title, assignee) is esperado


def test_grupos_de_pendientes():
    g = lambda t: arr.grupo_pendiente({"title": t})
    assert g("[Hub] x · tuyo") == arr.G_DECIDES and g("[Hub] x · decidir") == arr.G_DECIDES
    assert g("[Hub] x · integrar") == arr.G_INTEGRADOR
    assert g(CODIGO) == arr.G_CODIGO and g("Sin formato") == arr.G_CODIGO


def test_carriles_del_tablero_con_el_recomendado_primero():
    assert arr.carriles_para("oscarhq", LANES) == ["claude-oscarhq", "claude-scraper"]
    assert arr.carriles_para("migrateam", LANES) == ["claude-migrateam"]
    assert arr.resolver_carril("scraper", "oscarhq", LANES) == "claude-scraper"
    assert arr.resolver_carril("nextjobs", "oscarhq", LANES) is None  # otro tablero


# --- regla de creación (hermes.create) ------------------------------------------------------------------

def _crear(title, assignee):
    cmds = []

    def run(cmd, **kw):
        cmds.append(cmd)
        return SimpleNamespace(returncode=0, stdout="t_0000abcd", stderr="")

    HermesCLI("default", exe="hermes", runner=run).create(title, "b", assignee)
    return cmds[0]


def test_create_sin_carril_ni_marca_va_a_triage_y_avisa(caplog):
    with caplog.at_level("WARNING", logger="agent_lanes.arrancar"):
        cmd = _crear(CODIGO, "oscar")
    assert "--triage" in cmd and "triage" in caplog.text


@pytest.mark.parametrize("title,assignee", [("[Hub] Elegir · decidir", "oscar"), (CODIGO, "claude-hub")])
def test_create_con_marca_o_carril_no_toca_nada(title, assignee):
    assert "--triage" not in _crear(title, assignee)


# --- /arrancar ------------------------------------------------------------------------------------------

def test_arrancar_con_carril_reasigna_y_confirma(tmp_path):
    cc, desk, bot, kan = _env(tmp_path, {"t_00000001": _card("t_00000001", CODIGO)})
    command(desk, "/arrancar t_00000001 scraper")
    assert ("reassign", "t_00000001", "claude-scraper", "Arrancada por Oscar desde Telegram") in kan.calls
    assert any("🚀 t_00000001 arrancada en claude-scraper" in m["text"] for m in bot.sent)


def test_arrancar_sin_carril_pide_carril_con_el_recomendado_primero(tmp_path):
    cc, desk, bot, kan = _env(tmp_path, {"t_00000001": _card("t_00000001", CODIGO)})
    command(desk, "/arrancar t_00000001")
    pregunta = bot.sent[-1]
    assert texts(pregunta["markup"]) == ["⭐ claude-oscarhq", "claude-scraper"]
    assert not [c for c in kan.calls if c[0] == "reassign"]  # nunca elige solo
    press(desk, pregunta, 1)  # callback: reassign desde el botón
    assert ("reassign", "t_00000001", "claude-scraper", "Arrancada por Oscar desde Telegram") in kan.calls
    assert any("arrancada en claude-scraper" in e["text"] for e in bot.edits)


def test_arrancar_rechaza_carril_de_otro_tablero(tmp_path):
    cc, desk, bot, kan = _env(tmp_path, {"t_00000001": _card("t_00000001", CODIGO)})
    command(desk, "/arrancar t_00000001 nextjobs")
    assert not [c for c in kan.calls if c[0] == "reassign"]
    assert "no es un carril del tablero" in bot.sent[-1]["text"]


def test_arrancar_programada_la_pasa_a_cola(tmp_path):
    cc, desk, bot, kan = _env(tmp_path, {"t_00000001": _card("t_00000001", CODIGO, status="scheduled")})
    command(desk, "/arrancar t_00000001 oscarhq")
    assert ("unblock", "t_00000001") in kan.calls  # scheduled -> ready (el double de unblock lo deja en ready)
    assert any("arrancada en claude-oscarhq" in m["text"] for m in bot.sent)


@pytest.mark.parametrize("status", ["running", "blocked", "done"])
def test_arrancar_no_vale_para_otros_estados(tmp_path, status):
    cc, desk, bot, kan = _env(tmp_path, {"t_00000001": _card("t_00000001", CODIGO, status=status)})
    command(desk, "/arrancar t_00000001 oscarhq")
    assert not [c for c in kan.calls if c[0] == "reassign"]


def test_arrancar_respeta_la_tabla_de_permisos_si_existe(tmp_path):
    cc, desk, bot, kan = _env(tmp_path, {"t_00000001": _card("t_00000001", CODIGO)})
    desk.puede_lanzar = lambda actor: False
    command(desk, "/arrancar t_00000001 oscarhq")
    assert not [c for c in kan.calls if c[0] == "reassign"]
    assert "permiso" in bot.sent[-1]["text"]


# --- botón de la ficha ------------------------------------------------------------------------------------

def test_ficha_de_codigo_de_oscar_lleva_arrancar_y_el_resto_no(tmp_path):
    shows = {"t_00000001": _card("t_00000001", CODIGO), "t_00000002": _card("t_00000002", "[Hub] Elegir · decidir"),
             "t_00000003": _card("t_00000003", CODIGO, assignee="claude-oscarhq"),
             "t_00000004": _card("t_00000004", CODIGO, status="scheduled")}
    cc, desk, bot, kan = _env(tmp_path, shows)
    for tid, esperado in (("t_00000001", True), ("t_00000002", False), ("t_00000003", False), ("t_00000004", True)):
        bot.sent.clear()
        command(desk, f"/tarea {tid}")
        assert ("🚀 Arrancar" in texts(bot.sent[-1]["markup"])) is esperado, tid


def test_boton_arrancar_de_la_ficha_pide_carril_y_arranca(tmp_path):
    cc, desk, bot, kan = _env(tmp_path, {"t_00000001": _card("t_00000001", CODIGO)})
    command(desk, "/tarea t_00000001")
    ficha = bot.sent[-1]
    ficha = {**ficha, "markup": {"inline_keyboard": [[b for b in row if "callback_data" in b]  # sin el enlace 🗂 Tarjeta
                                                      for row in ficha["markup"]["inline_keyboard"]]}}
    press(desk, ficha, texts(ficha["markup"]).index("🚀 Arrancar"))
    pregunta = bot.sent[-1]
    assert texts(pregunta["markup"])[0] == "⭐ claude-oscarhq"
    press(desk, pregunta, 0, cid="cq2")
    assert ("reassign", "t_00000001", "claude-oscarhq", "Arrancada por Oscar desde Telegram") in kan.calls


# --- /pendientes ----------------------------------------------------------------------------------------

def test_pendientes_lista_las_tres_y_arrancar_solo_en_codigo(tmp_path):
    shows = {"t_00000001": _card("t_00000001", "[Hub] Elegir · decidir"),
             "t_00000002": _card("t_00000002", "[Hub] Fusionar · integrar", status="todo"),
             "t_00000003": _card("t_00000003", CODIGO),
             "t_00000004": _card("t_00000004", "[Hub] Roadmap · épica")}
    cc, desk, bot, kan = _env(tmp_path, shows)
    command(desk, "/pendientes")
    resumen = next(m for m in bot.sent if "Sin carril · oscarhq" in m["text"])
    t = resumen["text"]
    assert "Las decides tú (1)" in t and "Las hace el integrador (1)" in t and "De código sin lanzar (1)" in t
    assert "t_00000004" not in t  # las épicas no son trabajo vivo
    assert texts(resumen["markup"]) == ["🚀 Arrancar t_00000003"]
    press(desk, resumen, 0)  # pide carril de la tarjeta de la lista
    press(desk, bot.sent[-1], 0, cid="cq2")
    assert ("reassign", "t_00000003", "claude-oscarhq", "Arrancada por Oscar desde Telegram") in kan.calls
