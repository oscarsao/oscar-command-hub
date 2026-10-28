"""Botón 🗑 Quitar aviso, avisos que no se pisan entre sí y filtro `solo_menciones` por persona (t_3e96e204)."""
from __future__ import annotations

from agent_lanes import decisions, telegram
from agent_lanes.notices import MessageStore, TaskNotices
from agent_lanes.people import load_people
from tests.test_runner_state import FakeNotifier

WHERE = {"chat_id": "-1", "thread_id": "5", "message_id": 77}


class Tg:
    def __init__(self, delete=True, edit=True, boom=False):
        self.answers, self.deleted, self.edited = [], [], []
        self._delete, self._edit, self._boom = delete, edit, boom

    def answer_callback(self, cid, text=None, *, alert=False):
        self.answers.append((text, alert))

    def delete(self, chat_id, message_id):
        if self._boom:
            raise RuntimeError("telegram caído")
        self.deleted.append(message_id)
        return self._delete

    def edit(self, chat_id, message_id, text, **kw):
        self.edited.append((message_id, text))
        return self._edit


def desk(tg):
    d = decisions.DecisionDesk.__new__(decisions.DecisionDesk)
    d.notifier = tg
    return d


# --- borrar -------------------------------------------------------------------------------------------

def test_quitar_borra_el_mensaje_y_responde():
    tg = Tg()
    desk(tg)._quitar("cb", WHERE)
    assert tg.deleted == [77] and tg.edited == [] and tg.answers == [("Aviso quitado", False)]


def test_quitar_si_no_puede_borrar_lo_marca():
    tg = Tg(delete=False)
    desk(tg)._quitar("cb", WHERE)
    assert tg.edited == [(77, "🗑 Aviso quitado")]
    assert tg.answers[0][1] is False and "marcado" in tg.answers[0][0]


def test_quitar_si_todo_falla_avisa_con_alerta():
    tg = Tg(boom=True, edit=False)
    desk(tg)._quitar("cb", WHERE)
    assert tg.answers == [("No pude quitar el aviso: bórralo a mano (Telegram no me deja)", True)]


def test_quitar_button_usa_la_accion_registrada():
    assert decisions.QUITAR_BUTTON["action"] == decisions.QUITAR


# --- no pisar avisos ----------------------------------------------------------------------------------

class MarkupNotifier(FakeNotifier):
    def __init__(self):
        super().__init__()
        self.cleared = []

    def edit_markup(self, chat_id, message_id, markup):
        self.cleared.append(message_id)
        return True


def test_un_aviso_nuevo_no_borra_otro_aviso_anterior():
    n = MarkupNotifier()
    tn = TaskNotices(n, MessageStore())
    tn.publish("t_1", "pregunta importante", alert=True)
    tn.publish("t_1", "otro aviso distinto", alert=True)
    assert n.deleted == [] and n.cleared == [101]


def test_un_aviso_sigue_sustituyendo_al_mensaje_de_progreso():
    n = MarkupNotifier()
    tn = TaskNotices(n, MessageStore())
    tn.publish("t_1", "progreso")
    tn.publish("t_1", "necesita a Oscar", alert=True)
    assert n.deleted == [101] and n.cleared == []


# --- solo_menciones -----------------------------------------------------------------------------------

def _people(tmp_path, extra=""):
    f = tmp_path / "people.yaml"
    f.write_text("people:\n  ana:\n    name: Ana\n    user_id: 555\n    role: dev\n" + extra, encoding="utf-8")
    return load_people("1", f)


def test_solo_menciones_por_defecto_apagado(tmp_path):
    assert _people(tmp_path).filtros_menciones() == {}


def test_solo_menciones_necesita_mencion_valida(tmp_path):
    assert _people(tmp_path, "    solo_menciones: true\n").filtros_menciones() == {}
    assert _people(tmp_path, '    solo_menciones: "si"\n    mencion: "ana"\n').filtros_menciones() == {}
    p = _people(tmp_path, '    solo_menciones: true\n    mencion: "ana_dev"\n')
    assert p.filtros_menciones() == {"555": "@ana_dev"}


def test_filtro_descarta_avisos_sin_mencion_y_deja_pasar_los_demas(monkeypatch):
    sent = []
    monkeypatch.setattr(telegram.TelegramNotifier, "_api",
                        lambda self, method, payload, timeout=None: sent.append(payload) or {"message_id": 9})
    n = telegram.TelegramNotifier("tok", "-1")
    n.solo_menciones = {"555": "@ana_dev"}
    assert n.send_to("555", None, "aviso general") is None and sent == []
    assert n.send_to("555", None, "Te toca, @Ana_Dev") is not None and len(sent) == 1
    assert n.send_to("999", None, "aviso general") is not None and len(sent) == 2  # otra persona: como hoy


# --- extremo a extremo: teclado -> callback -> borrar --------------------------------------------------

def _pulsar_quitar(tmp_path, spec):
    from agent_lanes.decisions import CallbackStore
    store = CallbackStore(tmp_path / "cb")
    token, markup = store.issue({"task_id": "t_1"}, spec)
    botones = [b for fila in markup["inline_keyboard"] for b in fila]
    quitar = [b for b in botones if "Quitar aviso" in b["text"]]
    assert len(quitar) == 1  # el botón existe una sola vez
    assert store.get(token)["buttons"][int(quitar[0]["callback_data"].split(":")[1])]["action"] == decisions.QUITAR
    tg = Tg()
    d = decisions.DecisionDesk(tg, store, lanes={}, hermes_for=lambda b: None, owner_id="1",
                               spawn=lambda fn: fn(), messages=MessageStore(tmp_path / "msgs"))
    d._callback({"id": "cq", "from": {"id": 1}, "data": quitar[0]["callback_data"],
                 "message": {"chat": {"id": -1}, "message_thread_id": 5, "message_id": 77}})
    return tg


def test_extremo_a_extremo_el_aviso_de_tarea_se_quita(tmp_path):
    tg = _pulsar_quitar(tmp_path, decisions.keyboard_spec("done"))
    assert tg.deleted == [77] and tg.answers == [("Aviso quitado", False)]


def test_extremo_a_extremo_la_tarjeta_de_decision_se_quita(tmp_path):
    tg = _pulsar_quitar(tmp_path, decisions.card_keyboard_spec(2, 0, ["uno", "dos"]))
    assert tg.deleted == [77] and tg.answers == [("Aviso quitado", False)]


def test_avisos_sin_botones_siguen_sin_botones():
    assert decisions.keyboard_spec("running") is None
