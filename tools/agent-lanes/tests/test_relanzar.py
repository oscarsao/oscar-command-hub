"""Relanzado automático de bloqueadas transitorias (agent_lanes/relanzar.py): kanban simulado, sin hermes ni red."""
from __future__ import annotations

import json

import pytest

from agent_lanes.config import Lane
from agent_lanes import relanzar as rz
from agent_lanes.relanzar import (DESCONOCIDA, SIN_RAMA, Ajustes, RelanzarTicker, load_ajustes, pasada,
                                  rama_con_trabajo)


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
        self.fail_ids: set[str] = set()
        self.show_calls: list[str] = []

    def add(self, tid, status="blocked", assignee="claude-hub", kind="transient", reason="test_cmd falló", at=100):
        self.cards[tid] = {"id": tid, "status": status, "assignee": assignee, "created_at": at}
        self.shows[tid] = {"task": {"id": tid, "status": status},
                           "events": [{"kind": "blocked", "created_at": at, "payload": {"kind": kind, "reason": reason}}]}

    def list_status_all(self, status):
        if self.fail == "list":
            raise RuntimeError("kanban caído")
        return [dict(c) for c in self.cards.values() if c["status"] == status]

    def show(self, tid):
        self.show_calls.append(tid)
        sh = self.shows[tid]
        if self.cards[tid]["status"] != "blocked":  # un show manual en triage sobre una blocked se respeta
            sh["task"]["status"] = self.cards[tid]["status"]
        return sh

    def unblock(self, tid):
        self.calls.append(("unblock", tid))
        if self.fail == "unblock" or tid in self.fail_ids:
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


def sin_rama(_lane, _tid):
    return SIN_RAMA


def run(kb, cfg=ON, **kw):
    board, hermes_for, state = kb
    return pasada(cfg, LANES, hermes_for, state, rama_de=kw.pop("rama_de", sin_rama), **kw)


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
                            clock=lambda: ahora[0], rama_de=sin_rama)
    state.with_name("r.yaml").write_text("activo: true\n", encoding="utf-8")
    assert ticker.tick() == []           # captura el error
    board.fail = None
    assert ticker.tick() == []           # en pausa: sin reintento apretado
    assert unblocked(board) == []
    ahora[0] = 301
    assert ticker.tick() == ["t_1"]


def test_unblock_fallido_no_gasta_el_contador_ni_para_el_ticker(kb):
    board, hermes_for, state = kb
    board.add("t_1")
    board.fail = "unblock"
    assert pasada(ON, LANES, hermes_for, state, rama_de=sin_rama) == []  # ya no lanza RuntimeError
    assert json.loads(state.read_text(encoding="utf-8"))["relanzadas"] == {"t_1": 0}
    board.fail = None
    pasado_el_descarte = 10_000_000_000  # tras la caducidad del descarte provisional por unblock fallido
    assert pasada(ON, LANES, hermes_for, state, rama_de=sin_rama, ahora=lambda: pasado_el_descarte) == ["t_1"]


def test_unblock_fallido_continua_con_la_siguiente(kb):
    board = kb[0]
    board.add("t_a", at=1)
    board.add("t_b", at=2)
    board.fail_ids = {"t_a"}
    assert run(kb, Ajustes(activo=True, max_por_pasada=1)) == ["t_b"]
    assert unblocked(board) == ["t_a", "t_b"]


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


# --- una tarjeta que YA tiene rama lane/<id> se integra, no se relanza (Integradora, 21-11) ---------------------------

def run_con_rama(kb, ramas, cfg=ON):
    board, hermes_for, state = kb
    return pasada(cfg, LANES, hermes_for, state, rama_de=lambda lane_, tid: ramas.get(tid, SIN_RAMA))


def comentarios(board):
    return [c[2] for c in board.calls if c[0] == "comment"]


def test_con_rama_con_commits_no_se_relanza_y_se_avisa_una_sola_vez(kb):
    board = kb[0]
    board.add("t_1")
    assert run_con_rama(kb, {"t_1": 3}) == []
    assert unblocked(board) == []
    assert len(comentarios(board)) == 1 and "NO RELANZADA" in comentarios(board)[0] and "3 commits" in comentarios(board)[0]
    run_con_rama(kb, {"t_1": 3})  # segunda pasada: no repite el comentario ni relanza
    assert unblocked(board) == [] and len(comentarios(board)) == 1


def test_con_rama_ya_integrada_tampoco_se_relanza(kb):
    board = kb[0]
    board.add("t_1")
    assert run_con_rama(kb, {"t_1": 0}) == [] and unblocked(board) == []


def test_sin_rama_se_relanza_como_siempre(kb):
    board = kb[0]
    board.add("t_1")
    assert run_con_rama(kb, {}) == ["t_1"] and unblocked(board) == ["t_1"]


def test_la_tarjeta_con_rama_no_gasta_el_hueco_de_otra(kb):
    board = kb[0]
    board.add("t_1", at=100)  # la mas antigua, con rama
    board.add("t_2", at=200)  # sin rama: es la que se relanza
    assert run_con_rama(kb, {"t_1": 2}) == ["t_2"]


# --- revisión independiente (21-11): los 6 hallazgos --------------------------------------------------------------------

def test_rama_desconocida_no_relanza_ni_comenta(kb):
    board = kb[0]
    board.add("t_1")
    assert run(kb, rama_de=lambda _l, _t: DESCONOCIDA) == []
    assert run(kb, rama_de=lambda _l, _t: None) == []  # algo que no se entiende tambien es «desconocido»
    assert unblocked(board) == [] and comentarios(board) == []


class _CP:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _git_falso(monkeypatch, respuestas):
    """respuestas: subcomando git -> _CP (o excepcion)."""
    def fake(cmd, **_kw):
        r = respuestas[cmd[3]]
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(rz._proc, "run_retrying", fake)


L = Lane(name="claude-hub", board="hub", repo="R", base="main")


@pytest.mark.parametrize("ls, esperado", [
    (_CP(2), SIN_RAMA),
    (_CP(128, err="fatal: Couldn" + chr(39) + "t find remote ref refs/heads/lane/t_1"), SIN_RAMA),
    (_CP(128, err="fatal: unable to access: Could not resolve host"), DESCONOCIDA),
    (_CP(1, err="otra cosa"), DESCONOCIDA),
    (_CP(-9), DESCONOCIDA),   # timeout
    (TimeoutError("git colgado"), DESCONOCIDA),
])
def test_rama_con_trabajo_tres_resultados_segun_ls_remote(monkeypatch, ls, esperado):
    _git_falso(monkeypatch, {"ls-remote": ls})
    assert rama_con_trabajo(L, "t_1") == esperado


def test_rama_con_trabajo_existe_cuenta_commits_y_fallos_son_desconocidos(monkeypatch):
    ok = {"ls-remote": _CP(0), "fetch": _CP(0), "rev-list": _CP(0, "3\n")}
    _git_falso(monkeypatch, ok)
    assert rama_con_trabajo(L, "t_1") == 3
    _git_falso(monkeypatch, {**ok, "fetch": _CP(1)})
    assert rama_con_trabajo(L, "t_1") == DESCONOCIDA
    _git_falso(monkeypatch, {**ok, "rev-list": _CP(128)})
    assert rama_con_trabajo(L, "t_1") == DESCONOCIDA
    _git_falso(monkeypatch, {**ok, "rev-list": _CP(0, "no-numero")})
    assert rama_con_trabajo(L, "t_1") == DESCONOCIDA
    assert rama_con_trabajo(Lane(name="x", board="hub"), "t_1") == DESCONOCIDA  # sin repo


def test_ready_de_carril_de_codigo_ocupa_hueco_y_las_de_otros_no(kb):
    board = kb[0]
    board.add("t_r1", status="running")
    board.add("t_r2", status="ready")  # recien desbloqueada, aun sin reclamar
    board.add("t_r3", status="ready")
    board.add("t_1")
    assert run(kb) == []
    board.cards["t_r3"]["status"] = "done"
    board.add("t_o", status="ready", assignee="claude-ops")  # un carril de ops en ready no cuenta
    assert run(kb) == ["t_1"]


def test_decision_descartada_se_cachea_y_caduca(kb):
    board = kb[0]
    board.add("t_1", kind="needs_input")
    t = [1000.0]
    assert run(kb, ahora=lambda: t[0]) == [] and board.show_calls == ["t_1"]
    t[0] += 60
    run(kb, ahora=lambda: t[0])
    assert board.show_calls == ["t_1"]  # cacheada: no se repite show
    assert "t_1" in json.loads(kb[2].read_text(encoding="utf-8"))["decisiones"]
    t[0] += rz.CACHE_SEGUNDOS + 1
    run(kb, ahora=lambda: t[0])
    assert board.show_calls == ["t_1", "t_1"]  # caducada: se vuelve a mirar


def test_tarjeta_con_rama_cacheada_no_repite_fetch(kb):
    board = kb[0]
    board.add("t_1")
    llamadas = []

    def rama(_l, tid):
        llamadas.append(tid)
        return 2
    t = [1000.0]
    run(kb, rama_de=rama, ahora=lambda: t[0])
    run(kb, rama_de=rama, ahora=lambda: t[0] + 5)
    assert llamadas == ["t_1"] and board.show_calls == ["t_1"]


def test_limita_comprobaciones_de_rama_por_pasada(kb):
    board = kb[0]
    for i in range(rz.MAX_RAMAS + 3):
        board.add(f"t_{i:02d}", at=i)
    vistas = []

    def rama(_l, tid):
        vistas.append(tid)
        return DESCONOCIDA
    run(kb, rama_de=rama)
    assert len(vistas) == rz.MAX_RAMAS


@pytest.mark.parametrize("contenido", ["{no es json", "[1, 2]", ""])
def test_estado_corrupto_desactiva_la_pasada_y_no_se_resetea(kb, contenido, caplog):
    board, _h, state = kb
    board.add("t_1")
    state.write_text(contenido, encoding="utf-8")
    with caplog.at_level("WARNING", logger="agent_lanes"):
        assert run(kb) == []
    assert unblocked(board) == []
    assert state.read_text(encoding="utf-8") == contenido
    assert "NO se resetea" in caplog.text


def test_contador_sobrevive_a_borrar_el_estado_leyendo_los_comentarios(kb):
    board = kb[0]
    board.add("t_1")
    board.shows["t_1"]["comments"] = [
        {"body": "NO RELANZADA AUTOMÁTICAMENTE: ya tiene la rama"},  # este no cuenta
        {"body": "RELANZADA AUTOMÁTICAMENTE (1/1): test_cmd falló"}]
    assert not kb[2].exists()
    assert run(kb) == [] and unblocked(board) == []
    assert json.loads(kb[2].read_text(encoding="utf-8"))["relanzadas"] == {"t_1": 1}
    board.add("t_2")
    board.shows["t_2"]["comments"] = [{"body": "NO RELANZADA AUTOMÁTICAMENTE: x"}]
    assert run(kb) == ["t_2"]


def test_si_ya_no_esta_blocked_antes_del_unblock_no_se_gasta_el_contador(kb):
    board = kb[0]
    board.add("t_1")

    def rama(_l, tid):
        board.cards[tid]["status"] = "running"  # otro agente la mueve mientras se mira la rama
        return SIN_RAMA
    assert run(kb, rama_de=rama) == []
    assert unblocked(board) == []
    estado = json.loads(kb[2].read_text(encoding="utf-8"))
    assert estado.get("relanzadas", {}).get("t_1", 0) == 0
