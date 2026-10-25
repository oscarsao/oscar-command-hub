"""Avance de workflows (t_ff4de3a1): al terminar una tarjeta se crean los pasos desbloqueados, sin intervención manual."""
from types import SimpleNamespace

import pytest

from agent_lanes import workflows as wf
from agent_lanes.avance_workflows import AvanceTicker, AvanceWorkflows, coste_usd


class FakeHermes:
    def __init__(self):
        self.status: dict[str, str] = {}
        self.cost: dict[str, float] = {}
        self.created: list[str] = []

    def create(self, title, body, assignee, *, key=None, created_by=None):
        tid = f"t_{len(self.created) + 1:08d}"
        self.created.append(title)
        self.status[tid] = "running"
        return tid

    def show(self, tid):
        runs = [{"metadata": {"head_sha": "x", "branch": f"lane/{tid}", "cost_usd": self.cost[tid]}}] if tid in self.cost else []
        return {"task": {"id": tid, "status": self.status[tid]}, "runs": runs}


@pytest.fixture
def env(tmp_path):
    store = wf.Store(tmp_path / "wf.json")
    tarjetas = [{"titulo": "A", "carril": "claude-hub", "depende": []},
                {"titulo": "B", "carril": "claude-hub", "depende": ["A"]},
                {"titulo": "C", "carril": "claude-hub", "depende": ["A"]}]
    plan = wf.propose(store, wid="wf_1", titulo="Secuencias", req="req_1", preguntas=[], tarjetas=tarjetas, riesgos=[],
                      prueba="abrir la app", coste_estimado_eur=3, ajustes=wf.Ajustes())
    plan["origen"] = {"chat": "-100", "thread": "7"}
    store.put(plan)
    h, sent = FakeHermes(), []
    av = AvanceWorkflows(store, {"claude-hub": SimpleNamespace(board="default")}, lambda b: h,
                         lambda chat, thread, text, markup=None: sent.append((chat, thread, text)),
                         lambda t, n: t[:n], ajustes=wf.Ajustes)
    wf.approve(store, "wf_1")
    wf.launch(store, "wf_1", lambda item, req: h.create(item["titulo"], "", "claude-hub"), wf.Ajustes())
    return store, h, av, sent


def test_workflow_de_tres_pasos_avanza_solo_y_se_entrega(env):
    store, h, av, sent = env
    assert h.created == ["A"]
    assert av.tick() == []  # A sigue en marcha: nada que hacer
    h.status["t_00000001"], h.cost["t_00000001"] = "done", 1.0
    assert len(av.tick()) == 2  # B y C se crean al terminar A
    assert h.created == ["A", "B", "C"]
    assert "Paso 2/3 en marcha" in sent[0][2] and sent[0][:2] == ("-100", "7")  # al canal del que vino
    assert store.get("wf_1")["gastado_eur"] == pytest.approx(0.92)
    h.status["t_00000002"] = h.status["t_00000003"] = "done"
    av.tick()
    w = store.get("wf_1")
    assert w["estado"] == "entregado" and "Entregado" in sent[-1][2]
    assert av.tick() == [] and len(sent) == 2  # entregado una sola vez, sin repetir avisos


def test_tarjeta_archivada_bloquea_y_avisa(env):
    store, h, av, sent = env
    h.status["t_00000001"] = "archived"
    av.tick()
    assert store.get("wf_1")["estado"] == "bloqueado" and "bloqueado" in sent[0][2]
    av.tick()
    assert h.created == ["A"] and len(sent) == 1


def test_gasto_pausa_y_no_crea_pasos(env):
    store, h, av, sent = env
    h.status["t_00000001"], h.cost["t_00000001"] = "done", 20.0
    av.tick()
    assert store.get("wf_1")["estado"] == "pausado" and h.created == ["A"]
    assert "pausado" in sent[0][2]
    wf.ampliar(store, "wf_1", wf.Ajustes(paso_ampliar_eur=50))
    assert len(av.tick()) == 2


def test_coste_no_se_cuenta_dos_veces(env):
    store, h, av, _ = env
    h.status["t_00000001"], h.cost["t_00000001"] = "done", 1.0
    av.tick()
    av.tick()
    assert store.get("wf_1")["gastado_eur"] == pytest.approx(0.92)
    assert coste_usd({"runs": []}) == 0


def test_lectura_fallida_no_tumba_el_avance(env):
    store, h, av, _ = env
    h.show = lambda tid: (_ for _ in ()).throw(RuntimeError("red"))
    assert av.tick() == []
    assert store.get("wf_1")["estado"] == "en curso"


def test_ticker_respeta_el_intervalo(env):
    _, h, av, _ = env
    t = [1000.0]
    ticker = AvanceTicker(av, minutes=10, clock=lambda: t[0])
    h.status["t_00000001"] = "done"
    assert len(ticker.tick()) == 2
    assert ticker.tick() == []  # misma hora: no vuelve a mirar
