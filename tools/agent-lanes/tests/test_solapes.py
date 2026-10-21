"""Solape de alcance entre tarjetas activas del mismo repo (tableros falsos, sin hermes real ni Telegram)."""
from types import SimpleNamespace

from agent_lanes.solapes import (Ajustes, SolapeTicker, aviso, compartido, detectar, extraer_rutas, load_ajustes)

HUB = "C:/Users/oscar/oscar-command-hub"
LANES = {
    "claude-hub": SimpleNamespace(board="default", kind="implement", repo=HUB),
    "claude-nextjobs": SimpleNamespace(board="default", kind="implement", repo="C:/Users/oscar/nextjobs/autoapply_v5"),
    "claude-oscarhq": SimpleNamespace(board="oscarhq", kind="implement", repo="C:/Users/oscar/dev/oscar-hq"),
    "claude-scraper": SimpleNamespace(board="oscarhq", kind="implement", repo="C:/Users/oscar/dev/Scraper"),
    "claude-ops": SimpleNamespace(board="oscarhq", kind="ops", repo=""),
    "review": SimpleNamespace(board="", kind="review", repo=""),
}
A = Ajustes()


def card(tid, assignee, title, body="", status="ready"):
    return {"id": tid, "assignee": assignee, "title": title, "body": body, "status": status}


class FakeBoard:
    def __init__(self, cards, links=None, broken=False):
        self.cards, self.links, self.broken = cards, links or {}, broken

    def list_status_all(self, status):
        if self.broken:
            raise RuntimeError("hermes caído")
        return [c for c in self.cards if c["status"] == status]

    def show(self, tid):
        return {"parents": self.links.get(tid, {}).get("parents", []), "children": self.links.get(tid, {}).get("children", [])}


def hermes(boards):
    return lambda b: boards.get(b) or FakeBoard([])


def test_extrae_rutas_y_descarta_ruido():
    t = ("Toca `tools/agent-lanes/agent_lanes/commands.py` y lanes.yaml, ver https://x.com/a/b.py; "
         "también agent_lanes/ y/o `create` (2026/10/21). C:/Users/oscar/oscar-command-hub/runner.py")
    got = extraer_rutas(t, A, HUB)
    assert got == {"tools/agent-lanes/agent_lanes/commands.py", "lanes.yaml", "agent_lanes/", "runner.py"}


def test_comparten_fichero_o_carpeta_segun_nivel():
    a = {"tools/agent-lanes/agent_lanes/commands.py"}
    b = {"tools/agent-lanes/agent_lanes/decisions.py"}
    assert compartido(a, b, A) == ["carpeta tools/agent-lanes/agent_lanes/"]
    assert compartido(a, b, Ajustes(nivel="archivo")) == []
    assert compartido(a, {"commands.py"}, Ajustes(nivel="archivo")) == ["archivo tools/agent-lanes/agent_lanes/commands.py"]
    assert compartido({"src/a.py"}, {"src/b.py"}, A) == []  # carpeta de 1 tramo: demasiado genérica


def test_dos_tarjetas_del_mismo_repo_con_solape_se_agrupan():
    boards = {"default": FakeBoard([
        card("t_1", "claude-hub", "[Hub] A", "cambia agent_lanes/decisions.py"),
        card("t_2", "claude-hub", "[Hub] B", "retoca `agent_lanes/decisions.py`", status="running"),
        card("t_3", "claude-hub", "[Hub] C", "toca otra cosa en README.md"),
    ])}
    [g] = detectar(LANES, hermes(boards), A)
    assert [c.tid for c in g.cards] == ["t_1", "t_2"] and "archivo agent_lanes/decisions.py" in g.comparten
    txt = aviso(g, A)
    assert "t_1" in txt and "t_2" in txt and "t_3" not in txt and "fusionarlas en una sola" in txt and "dejarlas" in txt


def test_mismo_tablero_repos_distintos_no_solapan():
    boards = {"default": FakeBoard([
        card("t_1", "claude-hub", "A", "agent_lanes/commands.py"),
        card("t_2", "claude-nextjobs", "B", "agent_lanes/commands.py"),
    ])}
    assert detectar(LANES, hermes(boards), A) == []


def test_otro_assignee_review_y_done_quedan_fuera():
    boards = {"default": FakeBoard([
        card("t_1", "claude-hub", "A", "agent_lanes/commands.py"),
        card("t_2", "oscar", "B", "agent_lanes/commands.py"),
        card("t_3", "claude-hub", "C", "agent_lanes/commands.py", status="review"),
        card("t_4", "claude-hub", "D", "agent_lanes/commands.py", status="done"),
    ])}
    assert detectar(LANES, hermes(boards), A) == []


def test_tarjetas_encadenadas_no_se_avisan_entre_si():
    boards = {"default": FakeBoard([
        card("t_1", "claude-hub", "A", "agent_lanes/commands.py"),
        card("t_2", "claude-hub", "B", "agent_lanes/commands.py"),
    ], links={"t_2": {"parents": [{"id": "t_1"}]}})}
    assert detectar(LANES, hermes(boards), A) == []


def test_tablero_ilegible_no_corta_los_demas():
    boards = {"default": FakeBoard([], broken=True), "oscarhq": FakeBoard([
        card("t_1", "claude-oscarhq", "A", "backend/app/main.py"),
        card("t_2", "claude-oscarhq", "B", "backend/app/main.py"),
    ])}
    [g] = detectar(LANES, hermes(boards), A)
    assert [c.tid for c in g.cards] == ["t_1", "t_2"]


def test_tres_tarjetas_forman_un_solo_aviso():
    boards = {"default": FakeBoard([
        card("t_1", "claude-hub", "A", "runner.py"), card("t_2", "claude-hub", "B", "runner.py y lanes.yaml"),
        card("t_3", "claude-hub", "C", "lanes.yaml")])}
    [g] = detectar(LANES, hermes(boards), A)
    assert [c.tid for c in g.cards] == ["t_1", "t_2", "t_3"]


def test_ticker_avisa_una_vez_por_conjunto(tmp_path):
    boards = {"default": FakeBoard([card("t_1", "claude-hub", "A", "runner.py"), card("t_2", "claude-hub", "B", "runner.py")])}
    sent, now = [], [0.0]
    t = SolapeTicker(LANES, hermes(boards), send=sent.append, state_path=tmp_path / "s.json",
                     settings_path=tmp_path / "no.yaml", clock=lambda: now[0])
    t.tick()
    now[0] = 10_000
    t.tick()
    assert len(sent) == 1
    # un proceso nuevo (runner reiniciado) tampoco repite: el estado está en disco
    t2 = SolapeTicker(LANES, hermes(boards), send=sent.append, state_path=tmp_path / "s.json",
                      settings_path=tmp_path / "no.yaml", clock=lambda: 0.0)
    t2.tick()
    assert len(sent) == 1
    # entra una tarjeta nueva en el conjunto: es otro conjunto y se avisa
    boards["default"].cards.append(card("t_3", "claude-hub", "C", "runner.py"))
    now[0] = 20_000
    t.tick()
    assert len(sent) == 2 and "t_3" in sent[1]


def test_ticker_respeta_intervalo_y_apagado(tmp_path):
    boards = {"default": FakeBoard([card("t_1", "claude-hub", "A", "runner.py"), card("t_2", "claude-hub", "B", "runner.py")])}
    cfg, sent = tmp_path / "solapes.yaml", []
    cfg.write_text("activo: false\n", encoding="utf-8")
    t = SolapeTicker(LANES, hermes(boards), send=sent.append, state_path=tmp_path / "s.json", settings_path=cfg,
                     clock=lambda: 0.0)
    assert t.tick() == [] and sent == []
    cfg.write_text("activo: true\nminutos: 10\n", encoding="utf-8")
    t.tick()
    t.tick()  # dentro del intervalo
    assert len(sent) == 1


def test_ticker_no_lanza_si_falla_el_envio_y_reintenta(tmp_path):
    boards = {"default": FakeBoard([card("t_1", "claude-hub", "A", "runner.py"), card("t_2", "claude-hub", "B", "runner.py")])}
    calls = []

    def boom(text):
        calls.append(text)
        raise RuntimeError("telegram caído")
    state = tmp_path / "s.json"
    SolapeTicker(LANES, hermes(boards), send=boom, state_path=state, settings_path=tmp_path / "x").tick()
    assert len(calls) == 1 and not state.exists()
    ok = []
    SolapeTicker(LANES, hermes(boards), send=ok.append, state_path=state, settings_path=tmp_path / "x").tick()
    assert len(ok) == 1


def test_ajustes_invalidos_caen_al_default(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("nivel: raro\nminutos: -3\nestados: []\nactivo: 'si'\nmax_tarjetas: 2\n", encoding="utf-8")
    a = load_ajustes(p)
    assert (a.nivel, a.minutos, a.activo, a.max_tarjetas) == ("modulo", 10, True, 2) and a.estados == Ajustes().estados
    assert load_ajustes(tmp_path / "no.yaml") == Ajustes()
