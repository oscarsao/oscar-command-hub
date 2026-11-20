"""Aviso de tarjetas asignadas a quien no ejecuta nada (asignaciones.py): selección, aviso diario, repetición y ticker."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from agent_lanes import asignaciones as asg
from agent_lanes.asignaciones import Asignaciones, AsignacionesAviso
from agent_lanes.paradas import ParadasRunner, ParadasTicker
from agent_lanes.resumen_carriles import MADRID

H = 3600
NOW = datetime(2026, 11, 20, 9, 30, tzinfo=MADRID).timestamp()

LANES = {
    "claude-hub": SimpleNamespace(board="default", repo="C:/Users/oscar/oscar-command-hub", kind="implement",
                                  repos={}, aliases=("hub-viejo",)),
    "claude-oscarhq": SimpleNamespace(board="oscarhq", repo="C:/Users/oscar/dev/oscar-hq", kind="implement",
                                      repos={}, aliases=()),
    "claude-migrateam": SimpleNamespace(board="migrateam", repo="C:/Users/oscar/dev/migrateam", kind="implement",
                                        repos={}, aliases=()),
    "review": SimpleNamespace(board="default", repo="", kind="review", repos={}, aliases=()),
}


def card(tid, assignee="oscar-e2", status="ready", age=10, title="Algo", body=""):
    ts = NOW - age * H
    return {"id": tid, "title": title, "status": status, "assignee": assignee, "body": body,
            "created_at": ts - 50 * H, "events": [{"kind": "created", "created_at": ts}]}


class FakeBoard:
    def __init__(self, cards, broken=False):
        self.cards = {c["id"]: c for c in cards}
        self.broken, self.writes = broken, []

    def list_status_all(self, status):
        if self.broken:
            raise RuntimeError("hermes caído")
        return [{k: v for k, v in c.items() if k != "events"} for c in self.cards.values() if c["status"] == status]

    def show(self, tid):
        c = self.cards[tid]
        return {"task": {k: v for k, v in c.items() if k != "events"}, "events": c["events"]}

    def comment(self, *a, **k):
        self.writes.append(("comment", a))

    def assign(self, *a, **k):
        self.writes.append(("assign", a))

    def archive(self, *a, **k):
        self.writes.append(("archive", a))


@pytest.fixture(autouse=True)
def _ajustes_por_defecto(monkeypatch):
    monkeypatch.setattr(asg, "valor", lambda key: {"asignaciones.horas": 4.0,
                                                   "asignaciones.perfiles": ("oscar", "integrador", "apoyo", "codex")}[key])


def make(boards, clock=lambda: NOW):
    return Asignaciones(LANES, lambda b: boards[b], clock)


def test_ready_oscar_e2_de_mas_de_4h_sale_con_sugerencia_de_carril():
    default = FakeBoard([card("t_1", title="Arreglar aviso", body="Repo oscar-command-hub, carril claude-hub")])
    found = make({"default": default, "oscarhq": FakeBoard([]), "migrateam": FakeBoard([])}).encontrar()
    assert [p.tid for p in found] == ["t_1"]
    assert found[0].assignee == "oscar-e2" and found[0].suggestion == "reasignar a claude-hub"


def test_sugerencia_por_repo_en_otro_tablero():
    oh = FakeBoard([card("t_2", status="blocked", body="Repo: oscar-hq, tocar el dashboard")])
    found = make({"default": FakeBoard([]), "oscarhq": oh, "migrateam": FakeBoard([])}).encontrar()
    assert found[0].suggestion == "reasignar a claude-oscarhq"


def test_sin_repo_claro_propone_los_carriles_del_tablero():
    found = make({"default": FakeBoard([card("t_3", body="sin repo")]), "oscarhq": FakeBoard([]),
                  "migrateam": FakeBoard([])}).encontrar()
    assert "claude-hub" in found[0].suggestion and "review" not in found[0].suggestion


def test_no_salen_carril_valido_alias_perfiles_conocidos_ni_recientes_ni_sin_asignado():
    cards = [card("t_ok1", assignee="claude-hub"), card("t_ok2", assignee="hub-viejo"), card("t_ok3", assignee="oscar"),
             card("t_ok4", assignee="Apoyo"), card("t_new", age=1), card("t_ok5", assignee=""),
             card("t_ok6", status="running"), card("t_ok7", status="done")]
    boards = {"default": FakeBoard(cards), "oscarhq": FakeBoard([]), "migrateam": FakeBoard([])}
    assert make(boards).encontrar() == []


def test_estados_todo_y_triage_cuentan():
    boards = {"default": FakeBoard([card("t_a", status="todo"), card("t_b", status="triage", age=30)]),
              "oscarhq": FakeBoard([]), "migrateam": FakeBoard([])}
    assert [p.tid for p in make(boards).encontrar()] == ["t_b", "t_a"]  # la más antigua primero


def test_tablero_caido_no_tumba_el_resto():
    boards = {"default": FakeBoard([], broken=True), "oscarhq": FakeBoard([card("t_x")]), "migrateam": FakeBoard([])}
    assert [p.tid for p in make(boards).encontrar()] == ["t_x"]


def test_ajuste_ausente_o_invalido_usa_valores_por_defecto(monkeypatch):
    monkeypatch.setattr(asg, "valor", lambda key: {"asignaciones.horas": "basura", "asignaciones.perfiles": 7}[key])
    assert asg._horas() == 4.0 and asg._perfiles() == {"oscar", "integrador", "apoyo", "codex"}
    monkeypatch.setattr(asg, "valor", lambda key: {"asignaciones.horas": -3, "asignaciones.perfiles": ()}[key])
    assert asg._horas() == 4.0 and "oscar" in asg._perfiles()
    monkeypatch.setattr(asg, "valor", lambda key: {"asignaciones.horas": None, "asignaciones.perfiles": None}[key])
    assert asg._horas() == 4.0


def test_horas_editable(monkeypatch):
    monkeypatch.setattr(asg, "valor", lambda key: {"asignaciones.horas": 12.0,
                                                   "asignaciones.perfiles": ("oscar",)}[key])
    boards = {"default": FakeBoard([card("t_a", age=8), card("t_b", age=20), card("t_c", assignee="codex", age=20)]),
              "oscarhq": FakeBoard([]), "migrateam": FakeBoard([])}
    assert [p.tid for p in make(boards).encontrar()] == ["t_b", "t_c"]  # codex ya no es perfil conocido


def aviso(tmp_path, boards, sent, clock):
    return AsignacionesAviso(LANES, lambda b: boards[b], lambda text, markup=None: sent.append(text),
                             state_path=tmp_path / "asig.json", clock=clock)


def test_un_aviso_agrupado_por_asignado_y_sin_tocar_el_kanban(tmp_path):
    default = FakeBoard([card("t_1", title="Uno"), card("t_2", assignee="fantasma", title="Dos")])
    boards = {"default": default, "oscarhq": FakeBoard([card("t_3", status="blocked")]), "migrateam": FakeBoard([])}
    sent = []
    assert sorted(aviso(tmp_path, boards, sent, lambda: NOW).publicar()) == ["t_1", "t_2", "t_3"]
    assert len(sent) == 1
    text = sent[0]
    assert "oscar-e2 (perfil de consola, sin worker)" in text and "fantasma (no es un carril)" in text
    assert "t_1" in text and "Uno" in text and "10 h" in text and "reasignar a" in text
    assert text.count("oscar-e2") == 1  # agrupado
    assert all(not b.writes for b in boards.values())  # nada se reasigna, comenta ni archiva


def test_sin_tarjetas_no_manda_nada(tmp_path):
    boards = {b: FakeBoard([]) for b in ("default", "oscarhq", "migrateam")}
    sent = []
    assert aviso(tmp_path, boards, sent, lambda: NOW).publicar() == [] and sent == []


def test_no_se_repite_hasta_cambiar_de_estado_o_pasar_3_dias(tmp_path):
    default = FakeBoard([card("t_1", age=10)])
    boards = {"default": default, "oscarhq": FakeBoard([]), "migrateam": FakeBoard([])}
    sent, t = [], [NOW]
    av = aviso(tmp_path, boards, sent, lambda: t[0])
    assert av.publicar() == ["t_1"]
    t[0] = NOW + 24 * H
    assert av.publicar() == [] and len(sent) == 1  # ya avisada
    default.cards["t_1"]["status"] = "blocked"
    default.cards["t_1"]["events"] = [{"kind": "blocked", "created_at": NOW - 6 * H}]
    assert av.publicar() == ["t_1"] and len(sent) == 2  # cambió de estado
    t[0] = NOW + 24 * H + 3 * 24 * H
    assert av.publicar() == ["t_1"] and len(sent) == 3  # pasaron 3 días


def test_si_falla_el_envio_no_se_marca_como_avisada(tmp_path):
    boards = {"default": FakeBoard([card("t_1")]), "oscarhq": FakeBoard([]), "migrateam": FakeBoard([])}
    av = AsignacionesAviso(LANES, lambda b: boards[b], lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tg")),
                           state_path=tmp_path / "asig.json", clock=lambda: NOW)
    with pytest.raises(RuntimeError):
        av.publicar()
    assert not (tmp_path / "asig.json").exists()


def test_ticker_diario_lanza_la_pasada_extra_y_un_fallo_no_tumba_las_paradas(tmp_path):
    runner = ParadasRunner(LANES, lambda b: FakeBoard([]), lambda *a, **k: None, state_path=tmp_path / "p.json",
                           clock=lambda: NOW)
    llamadas = []
    tk = ParadasTicker(runner, clock=lambda: NOW, extra=lambda: llamadas.append(1))
    tk.tick()
    assert llamadas == [1]
    tk2 = ParadasTicker(runner, clock=lambda: NOW + 24 * H, extra=lambda: 1 / 0)
    assert tk2.tick() == []  # no lanza
    tk3 = ParadasTicker(runner, clock=lambda: NOW + 24 * H + 60, extra=lambda: llamadas.append(2))
    assert tk3.tick() == [] and llamadas == [1]  # el mismo día no se repite (day-gate)


def test_los_ajustes_existen_con_su_valor_por_defecto():
    from agent_lanes.ajustes_hub import SETTINGS
    assert SETTINGS["asignaciones.horas"].default == 4.0
    assert SETTINGS["asignaciones.perfiles"].default == ("oscar", "integrador", "apoyo", "codex")
