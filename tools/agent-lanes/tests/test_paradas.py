"""Aviso diario de las 9:00 de tarjetas de carril paradas > 24 h (paradas.py): selección, ficha, botones, ticker y /salud."""
from datetime import datetime
from types import SimpleNamespace

from agent_lanes import health
from agent_lanes.paradas import (PA_ARCHIVE, PA_INTEGRADOR, PA_RETRY, Paradas, ParadasRunner, ParadasTicker,
                                 desde_cuando, duracion)
from agent_lanes.resumen_carriles import MADRID

H = 3600
NOW = datetime(2026, 10, 23, 9, 30, tzinfo=MADRID).timestamp()


def card(tid, status="ready", assignee="claude-hub", age=30, title="Arreglar algo", events=None):
    """Tarjeta con `age` horas desde su último evento (el que fija «desde cuándo»)."""
    ts = NOW - age * H
    return {"id": tid, "title": title, "status": status, "assignee": assignee, "created_at": ts - 100 * H,
            "events": events if events is not None else [{"kind": "blocked", "created_at": ts}]}


class FakeHermes:
    def __init__(self, cards, broken=()):
        self.cards = {c["id"]: c for c in cards}
        self.broken = set(broken)
        self.calls = []

    def list_status(self, assignee, status, sort="priority"):
        if assignee in self.broken:
            raise RuntimeError("hermes caído")
        return [{k: v for k, v in c.items() if k != "events"} for c in self.cards.values()
                if c["assignee"] == assignee and c["status"] == status]

    def show(self, tid):
        c = self.cards[tid]
        return {"task": {k: v for k, v in c.items() if k != "events"}, "events": c["events"], "comments": []}

    def comment(self, tid, text, author=None):
        self.calls.append(("comment", tid, text, author))
        return True

    def assign(self, tid, profile):
        self.calls.append(("assign", tid, profile))
        self.cards[tid]["assignee"] = profile
        return True

    def archive(self, tid):
        self.calls.append(("archive", tid))
        self.cards[tid]["status"] = "archived"
        return True

    def unblock(self, tid):
        self.calls.append(("unblock", tid))
        if self.cards[tid]["status"] != "blocked":
            return False
        self.cards[tid]["status"] = "ready"
        return True

    def requeue_triage(self, tid):
        self.calls.append(("requeue_triage", tid))
        if self.cards[tid]["status"] != "triage":
            return None
        self.cards[tid]["status"] = "ready"
        return "ready"


class Notifier:
    def __init__(self):
        self.edited, self.sent = [], []

    def send_to(self, chat, thread, text, **kw):
        self.sent.append(text)

    def edit(self, chat, message_id, text, **kw):
        self.edited.append((message_id, text))


class Desk:
    def __init__(self):
        self.notifier = Notifier()

    def _who(self):
        return "Oscar"

    def _edit_text(self, where, text):
        self.notifier.edit(where["chat_id"], where["message_id"], text)


LANES = {"claude-hub": SimpleNamespace(board="default"), "claude-oscarhq": SimpleNamespace(board="oscarhq")}


def make(tmp_path, cards, clock=None, broken=()):
    h = FakeHermes(cards, broken)
    sent, issued = [], []

    def issue(rec, spec):
        issued.append(rec)
        return "tok", {"inline_keyboard": [[{"text": b["text"], "callback_data": b["action"]} for b in spec[0]]]}

    r = ParadasRunner(LANES, lambda board: h, lambda t, m=None: sent.append((t, m)), issue=issue,
                      state_path=tmp_path / "paradas.json", clock=clock or (lambda: NOW))
    return r, h, sent, issued


def test_solo_las_paradas_mas_de_24_h_en_las_cuatro_colas(tmp_path):
    r, h, sent, issued = make(tmp_path, [
        card("t_ready001", "ready", age=30), card("t_block001", "blocked", age=50), card("t_triag001", "triage", age=72),
        card("t_revie001", "review", age=26), card("t_joven001", "ready", age=23),
        card("t_runni001", "running", age=99), card("t_sched001", "scheduled", age=99),
        card("t_oscar001", "ready", assignee="oscar", age=99), card("t_perso001", "ready", assignee="maria", age=99)])
    assert [p.tid for p in Paradas(LANES, lambda b: h, lambda: NOW).encontrar()] == [
        "t_triag001", "t_block001", "t_ready001", "t_revie001"]  # la más antigua primero


def test_el_comentario_no_mueve_la_tarjeta_pero_un_evento_si():
    viejo = card("t_aaaaaaaa", events=[{"kind": "blocked", "created_at": NOW - 40 * H},
                                       {"kind": "commented", "created_at": NOW - 1 * H}])
    assert desde_cuando({}, {"task": {"status": "ready"}, "events": viejo["events"]}) == NOW - 40 * H
    sin_eventos = {"status": "review", "completed_at": NOW - 5 * H, "created_at": NOW - 90 * H}
    assert desde_cuando(sin_eventos, {"task": sin_eventos, "events": []}) == NOW - 5 * H


def test_sin_paradas_no_manda_ningun_mensaje(tmp_path):
    r, h, sent, issued = make(tmp_path, [card("t_joven001", age=3), card("t_runni001", "running", age=99)])
    assert r.publicar() == [] and sent == []


def test_ficha_con_id_titulo_estado_desde_cuando_y_tres_botones(tmp_path):
    r, h, sent, issued = make(tmp_path, [card("t_block001", "blocked", age=51, title="Migrar el panel")])
    assert r.publicar() == ["t_block001"]
    assert sent[0][0].startswith("⏳ 1 tarjeta de carril parada más de 24 h") and sent[0][1] is None
    text, markup = sent[1]
    assert text == "t_block001 · Migrar el panel · bloqueada · hace 2 d 3 h · carril claude-hub"
    assert [b["text"] for b in markup["inline_keyboard"][0]] == ["🔁 Reintentar", "🛠 Integrador", "🗄 Archivar"]
    assert [b["callback_data"] for b in markup["inline_keyboard"][0]] == [PA_RETRY, PA_INTEGRADOR, PA_ARCHIVE]
    assert issued[0]["task_id"] == "t_block001" and issued[0]["board"] == "default"
    assert not [c for c in h.calls if c[0] != "comment"]  # publicar nunca toca el kanban


def test_tope_de_fichas_y_cabecera_con_el_total(tmp_path):
    cards = [card(f"t_{i:08d}", age=30 + i) for i in range(10)]
    r, h, sent, issued = make(tmp_path, cards)
    assert len(r.publicar()) == 8
    assert "10 tarjetas de carril paradas" in sent[0][0] and "muestro las 8 más antiguas" in sent[0][0]
    assert len(sent) == 9


def test_un_tablero_roto_no_tumba_el_aviso(tmp_path):
    r, h, sent, issued = make(tmp_path, [card("t_ready001", "ready", age=30)], broken=("claude-oscarhq",))
    assert r.publicar() == ["t_ready001"]


def test_reintentar_reencola_bloqueada_y_triage_y_no_toca_una_ready(tmp_path):
    r, h, sent, issued = make(tmp_path, [card("t_block001", "blocked"), card("t_triag001", "triage"),
                                         card("t_ready001", "ready")])
    desk, where = Desk(), {"chat_id": 1, "thread_id": 398, "message_id": 7}
    assert r.on_button(PA_RETRY, {"task_id": "t_block001", "board": "default"}, where, desk)
    assert h.cards["t_block001"]["status"] == "ready" and "reencolada" in desk.notifier.edited[-1][1]
    assert r.on_button(PA_RETRY, {"task_id": "t_triag001", "board": "default"}, where, desk)
    assert ("requeue_triage", "t_triag001") in h.calls and h.cards["t_triag001"]["status"] == "ready"
    assert r.on_button(PA_RETRY, {"task_id": "t_ready001", "board": "default"}, where, desk)
    assert "nada que reintentar" in desk.notifier.edited[-1][1] and ("unblock", "t_ready001") not in h.calls


def test_integrador_reasigna_y_archivar_archiva_con_comentario(tmp_path):
    r, h, sent, issued = make(tmp_path, [card("t_block001", "blocked"), card("t_ready001", "ready")])
    desk, where = Desk(), {"chat_id": 1, "thread_id": None, "message_id": 7}
    assert r.on_button(PA_INTEGRADOR, {"task_id": "t_block001", "board": "default"}, where, desk)
    assert ("assign", "t_block001", "oscar-e2") in h.calls and "integrador" in desk.notifier.edited[-1][1]
    assert r.on_button(PA_ARCHIVE, {"task_id": "t_ready001", "board": "default"}, where, desk)
    assert h.cards["t_ready001"]["status"] == "archived"
    assert any(c[:2] == ("comment", "t_ready001") and "Archivada por Oscar" in c[2] for c in h.calls)
    assert r.on_button(PA_ARCHIVE, {"task_id": "t_ready001", "board": "default"}, where, desk)  # idempotente
    assert len([c for c in h.calls if c[0] == "archive"]) == 1


def test_no_toca_una_tarjeta_en_curso(tmp_path):
    r, h, *_ = make(tmp_path, [card("t_runni001", "running")])
    desk = Desk()
    for action in (PA_RETRY, PA_INTEGRADOR, PA_ARCHIVE):
        assert r.on_button(action, {"task_id": "t_runni001", "board": "default"}, {"chat_id": 1, "message_id": 2},
                           desk) is False
    assert not [c for c in h.calls if c[0] != "comment"] and "en curso" in desk.notifier.edited[-1][1]


def test_ticker_una_vez_al_dia_pasada_la_hora(tmp_path):
    t = [datetime(2026, 10, 23, 8, 30, tzinfo=MADRID).timestamp()]
    r, h, sent, _ = make(tmp_path, [card("t_ready001", age=30)], clock=lambda: NOW)
    tk = ParadasTicker(r, minutes=0, clock=lambda: t[0])
    assert tk.tick() == [] and sent == []  # antes de las 9:00
    t[0] = datetime(2026, 10, 23, 9, 5, tzinfo=MADRID).timestamp()
    assert tk.tick() == ["t_ready001"] and len(sent) == 2
    assert tk.tick() == [] and len(sent) == 2  # ya enviado hoy (también tras un reinicio: estado en disco)
    assert ParadasTicker(r, minutes=0, clock=lambda: t[0]).tick() == []
    t[0] += 24 * H
    assert ParadasTicker(r, minutes=0, clock=lambda: t[0]).tick() == ["t_ready001"]


def test_ticker_sin_paradas_no_envia_pero_marca_el_dia(tmp_path):
    r, h, sent, _ = make(tmp_path, [card("t_joven001", age=2)])
    tk = ParadasTicker(r, minutes=0, clock=lambda: NOW)
    assert tk.tick() == [] and sent == []


def test_resumen_para_salud_y_seccion_del_mensaje(tmp_path):
    h = FakeHermes([card("t_block001", "blocked", age=50), card("t_ready001", "ready", age=30)])
    line = Paradas(LANES, lambda b: h, lambda: NOW).resumen()
    assert line == "2 paradas más de 24 h (1 bloqueada, 1 en cola sin worker) · la más antigua hace 2 d 2 h"
    assert Paradas(LANES, lambda b: FakeHermes([]), lambda: NOW).resumen() == "ninguna tarjeta parada"
    base = dict(runner=lambda: "vivo", rows=lambda: [], gateway=lambda: "ok", monitor=lambda: ("ok", []),
                drift=lambda: "ok", machine=lambda: "ok", pending=lambda: (0, 0, 0))
    assert "<b>Paradas</b> · 2 paradas" in health.build(**base, paradas=lambda: line)
    assert "Paradas" not in health.build(**base)
    roto = health.build(**base, paradas=lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert "<b>Paradas</b> · " in roto


def test_duracion():
    assert duracion(3 * H) == "3 h" and duracion(26 * H) == "1 d 2 h" and duracion(-5) == "0 h"
