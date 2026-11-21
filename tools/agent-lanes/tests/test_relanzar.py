"""Relanzado automático de bloqueadas transitorias (agent_lanes/relanzar.py): kanban simulado, sin hermes ni red."""
from __future__ import annotations

import json

import pytest

from agent_lanes.config import Lane
from agent_lanes.relanzar import Ajustes, RelanzarTicker, load_ajustes, pasada


def lane(name, board="hub", kind="implement"):
    return Lane(name=name, board=board, kind=kind)


LANES = {"claude-hub": lane("claude-hub"), "claude-oscarhq": lane("claude-oscarhq", "oscarhq"),
         "review": lane("review", kind="review"), "claude-ops": lane("claude-ops", kind="ops")}


class FakeBoard:
    def __init__(self, fail=None):
        self.cards: dict[str, dict] = {}
        self.shows: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.fail = fail

    def add(self, tid, status="blocked", assignee="claude-hub", kind="transient", reason="test_cmd falló", at=100):
        self.cards[tid] = {"id": tid, "status": status, "assignee": assignee, "created_at": at}
        self.shows[tid] = {"task": {"id": tid, "status": status},
                           "events": [{"kind": "blocked", "created_at": at, "payload": {"kind": kind, "reason": reason}}]}

    def list_status_all(self, status):
        if self.fail == "list":
            raise RuntimeError("kanban caído")
        return [dict(c) for c in self.cards.values() if c["status"] == status]

    def show(self, tid):
        return self.shows[tid]

    def unblock(self, tid):
        self.calls.append(("unblock", tid))
        if self.fail == "unblock":
            return False
        self.cards[tid]["status"] = "ready"
        return True

    def comment(self, tid, text, author=None):
        self.calls.append(("comment", tid, text))
        return True


@pytest.fixture
def kb(tmp_path):
    board = FakeBoard()
    return board, (lambda _b: board), tmp_path / "relanzar.json"


ON = Ajustes(activo=True)


def run(kb, cfg=ON):
    board, hermes_for, state = kb
    return pasada(cfg, LANES, hermes_for, state)


def unblocked(board):
    return [c[1] for c in board.calls if c[0] == "unblock"]


def test_apagado_no_hace_nada_ni_lee(kb):
    board = kb[0]
    board.add("t_1")
    board.fail = "list"  # si leyera, fallaría
    assert run(kb, Ajustes()) == []
    assert board.calls == []


def test_tope_de_running_alcanzado_no_relanza(kb):
    board = kb[0]
    for i in range(3):
        board.add(f"t_r{i}", status="running")
    board.add("t_1")
    assert run(kb) == []
    assert unblocked(board) == []


def test_con_hueco_relanza_y_comenta(kb):
    board = kb[0]
    board.add("t_1", reason="verificación mecánica fallida: test_cmd falló")
    assert run(kb) == ["t_1"]
    assert unblocked(board) == ["t_1"]
    comment = next(c for c in board.calls if c[0] == "comment")
    assert comment[2].startswith("RELANZADA AUTOMÁTICAMENTE (1/1): verificación mecánica fallida")
    assert json.loads(kb[2].read_text(encoding="utf-8"))["relanzadas"] == {"t_1": 1}


def test_solo_la_mas_antigua_y_solo_una(kb):
    board = kb[0]
    board.add("t_nueva", at=300)
    board.add("t_vieja", at=100)
    board.add("t_media", at=200)
    assert run(kb) == ["t_vieja"]
    assert unblocked(board) == ["t_vieja"]


def test_no_supera_el_hueco_aunque_max_por_pasada_sea_mayor(kb):
    board = kb[0]
    board.add("t_r1", status="running")
    board.add("t_r2", status="running")
    board.add("t_a", at=1)
    board.add("t_b", at=2)
    assert run(kb, Ajustes(activo=True, max_por_pasada=5)) == ["t_a"]


def test_no_relanza_needs_input(kb):
    board = kb[0]
    board.add("t_1", kind="needs_input", reason="¿qué hacemos?")
    assert run(kb) == []
    assert unblocked(board) == []


@pytest.mark.parametrize("motivo", ["Falta CONFIGURACIÓN del repo", "limitacion-guard: tope", "toca producción",
                                    "requiere MIGRACION de base"])
def test_no_relanza_motivos_excluidos(kb, motivo):
    board = kb[0]
    board.add("t_1", reason=motivo)
    assert run(kb) == []


def test_respeta_max_auto_por_tarjeta(kb):
    board = kb[0]
    board.add("t_1")
    assert run(kb) == ["t_1"]
    board.cards["t_1"]["status"] = "blocked"  # vuelve a bloquearse
    assert run(kb) == []
    assert unblocked(board) == ["t_1"]
    assert run(kb, Ajustes(activo=True, max_auto_por_tarjeta=2)) == ["t_1"]
    assert "(2/2)" in [c for c in board.calls if c[0] == "comment"][-1][2]


def test_solo_carriles_de_codigo_y_no_triage(kb):
    board = kb[0]
    board.add("t_oscar", assignee="oscar", at=1)
    board.add("t_integ", assignee="integrador", at=2)
    board.add("t_ops", assignee="claude-ops", at=3)
    board.add("t_rev", assignee="review", at=4)
    board.add("t_triage", status="triage", at=5)
    board.add("t_show_triage", at=6)
    board.shows["t_show_triage"]["task"]["status"] = "triage"
    board.add("t_ok", assignee="claude-oscarhq", at=7)
    assert run(kb) == ["t_ok"]
    assert unblocked(board) == ["t_ok"]


def test_error_de_kanban_no_rompe_el_ticker_y_pausa(kb):
    board, hermes_for, state = kb
    board.add("t_1")
    board.fail = "list"
    ahora = [0.0]
    ticker = RelanzarTicker(LANES, hermes_for, settings_path=state.with_name("r.yaml"), state_path=state,
                            clock=lambda: ahora[0])
    state.with_name("r.yaml").write_text("activo: true\n", encoding="utf-8")
    assert ticker.tick() == []           # captura el error
    board.fail = None
    assert ticker.tick() == []           # en pausa: sin reintento apretado
    assert unblocked(board) == []
    ahora[0] = 301
    assert ticker.tick() == ["t_1"]


def test_unblock_fallido_no_gasta_el_contador(kb):
    board, hermes_for, state = kb
    board.add("t_1")
    board.fail = "unblock"
    with pytest.raises(RuntimeError):
        pasada(ON, LANES, hermes_for, state)
    assert json.loads(state.read_text(encoding="utf-8"))["relanzadas"] == {"t_1": 0}
    board.fail = None
    assert pasada(ON, LANES, hermes_for, state) == ["t_1"]


def test_ajustes_por_defecto_y_validos(tmp_path):
    assert load_ajustes(tmp_path / "no_existe.yaml") == Ajustes()
    assert Ajustes().activo is False
    f = tmp_path / "a.yaml"
    f.write_text("activo: true\ntope_running: 5\nmax_por_pasada: -1\nexcluir_motivos: []\n", encoding="utf-8")
    cfg = load_ajustes(f)
    assert cfg.activo and cfg.tope_running == 5 and cfg.max_por_pasada == 1
    assert "producción" in cfg.excluir_motivos
    f.write_text("[no, un, dict]", encoding="utf-8")
    assert load_ajustes(f) == Ajustes()


def test_relanzar_yaml_del_repo_esta_apagado():
    from agent_lanes.relanzar import SETTINGS_PATH
    assert SETTINGS_PATH.exists() and load_ajustes(SETTINGS_PATH).activo is False
