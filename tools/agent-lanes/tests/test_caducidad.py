"""Caducidad de lo que espera a Oscar (t_c587d3e1): 3 recordatorios de 24 h, «¿Sigue vigente?», aplazado a 30 d,
Mantener/Archivar, tope de 5 por día y ajustes. Reloj inyectado y dobles: sin Telegram ni Hermes reales."""
from __future__ import annotations

from agent_lanes import bandeja
from agent_lanes import lo_tuyo
from agent_lanes.bandeja import Bandeja
from agent_lanes.caducidad import Escalera
from tests.test_botones_tarjetas import BODY, card, comments_of, setup
from tests.test_commands import OSCAR, press, texts

H = 3600.0
DIA = 86400.0
T0 = 1_800_000_000.0  # 2027-01-15 ~08:00 UTC; solo importa para el corte de día de Madrid


def it(tid, since, kind="tuyo"):
    return lo_tuyo.item(kind, tid, f"Tarjeta {tid}", board="migrateam", since=since)


class Sim:
    """Escalera + sus avisos registrados, con reloj a mano."""

    def __init__(self, tmp_path, cfg=None, now=T0):
        self.now, self.cfg = now, cfg or Bandeja()
        self.esc = Escalera(tmp_path / "caducidad.json", clock=lambda: self.now)
        self.recs, self.vigs, self.auto, self.snoozed = [], [], [], []
        self.items = []

    def tick(self, avanza_h=0.0, completo=True):
        self.now += avanza_h * H
        return self.esc.tick(
            self.items, self.cfg, completo=completo,
            enviar_recordatorios=lambda its: self.recs.append([(i["tid"], i["quedan"]) for i in its]) or True,
            enviar_vigente=lambda i: self.vigs.append(i["tid"]) or True,
            aplazar=lambda i, d: self.snoozed.append((i["tid"], d)),
            avisar_aplazadas=lambda its: self.auto.append([i["tid"] for i in its]))


def test_exactly_three_reminders_24h_apart_then_one_vigente_then_snooze_30d(tmp_path):
    s = Sim(tmp_path)
    s.items = [it("t_a", T0)]                    # empieza a esperar ahora
    s.tick(); s.tick(23)                         # 23 h: aún nada
    assert not s.recs and not s.vigs
    s.tick(1); s.tick(1)                         # 24 h: 1.º (quedan 2); repetir el tick no duplica
    assert s.recs == [[("t_a", 2)]]
    s.tick(24); s.tick(24)                       # 48 h y 72 h
    assert s.recs == [[("t_a", 2)], [("t_a", 1)], [("t_a", 0)]]
    assert not s.vigs
    s.tick(24)                                   # 96 h: ya no hay recordatorios, sale «¿Sigue vigente?»
    assert s.vigs == ["t_a"] and len(s.recs) == 3
    s.tick(10); s.tick(10)                       # dentro de las 24 h de respuesta: nada más
    assert s.vigs == ["t_a"] and not s.auto
    s.tick(5)                                    # pasadas 25 h sin respuesta: se aplaza 30 d con una línea
    assert s.snoozed == [("t_a", 30)] and s.auto == [["t_a"]]
    s.items = []                                 # aplazada: ya no se ve y no vuelve a salir hasta entonces
    s.tick(24 * 29)
    assert s.esc.estado("t_a")["fase"] == "reposo" and len(s.vigs) == 1 and len(s.recs) == 3
    s.tick(24 * 2)                               # pasados los 30 d y fuera de la lista: se limpia
    assert s.esc.estado("t_a") is None


def test_count_starts_when_it_began_waiting_not_at_creation(tmp_path):
    s = Sim(tmp_path)
    s.items = [it("t_a", T0 - 2 * DIA)]          # espera desde hace 2 d (≤7 d): entra en la escalera sin ser «legada»
    s.tick()
    assert s.recs == [[("t_a", 2)]]              # un solo recordatorio de golpe, no tres
    s.tick(24)
    s.tick(24)
    assert [r[0][1] for r in s.recs] == [2, 1, 0]


def test_mantener_restarts_the_counter_after_7d_and_keeps_it_visible(tmp_path):
    s = Sim(tmp_path)
    s.items = [it("t_a", T0)]
    s.tick()
    for _ in range(4):
        s.tick(24)
    assert s.vigs == ["t_a"]
    s.esc.reposo("t_a", 7, now=s.now)            # lo que hace el botón «Mantener 7 d»
    s.tick(24 * 6)                               # sigue en la lista pero sin avisos durante 7 d
    assert len(s.recs) == 3 and len(s.vigs) == 1 and not s.auto
    s.tick(25)                                   # fin del reposo: cuenta desde cero (primer recordatorio 24 h después)
    assert len(s.recs) == 3
    s.tick(24)
    assert s.recs[-1] == [("t_a", 2)] and len(s.recs) == 4


def test_answered_card_leaves_the_ladder_and_restarts_when_it_comes_back(tmp_path):
    s = Sim(tmp_path)
    s.items = [it("t_a", T0)]
    s.tick(); s.tick(24)
    assert len(s.recs) == 1 and s.esc.estado("t_a")["n"] == 1
    s.items = []                                 # contestada o movida
    s.tick(1)
    assert s.esc.estado("t_a") is None
    s.items = [it("t_a", s.now)]                 # vuelve a esperarle
    s.tick(); s.tick(24)
    assert s.recs[-1] == [("t_a", 2)]            # otra vez el primero


def test_incomplete_read_does_not_reset_anything(tmp_path):
    s = Sim(tmp_path)
    s.items = [it("t_a", T0)]
    s.tick(); s.tick(24)
    s.items = []
    s.tick(1, completo=False)                    # una fuente lenta: no se sabe si sigue esperando
    assert s.esc.estado("t_a")["n"] == 1


def test_legacy_cards_go_to_vigente_in_batches_of_five_per_day(tmp_path):
    s = Sim(tmp_path)
    s.items = [it(f"t_{i:02d}", T0 - (10 + i) * DIA) for i in range(12)]   # 12 con más de 7 d esperando
    s.tick()
    assert len(s.vigs) == 5 and not s.recs       # sin recordatorios previos y solo 5 hoy
    assert s.vigs == [f"t_{i:02d}" for i in range(11, 6, -1)]  # las más antiguas primero
    s.tick(2)
    assert len(s.vigs) == 5                      # el tope vale el resto del día (Madrid)
    s.tick(24)
    assert len(s.vigs) == 10
    s.tick(24)
    assert len(s.vigs) == 12 and len(set(s.vigs)) == 12
    assert s.auto and len(s.snoozed) >= 5        # las del primer día, sin respuesta, se aplazaron solas


def test_failed_send_does_not_advance_state(tmp_path):
    s = Sim(tmp_path)
    s.items = [it("t_a", T0 - 2 * DIA)]
    s.esc.tick(s.items, s.cfg, enviar_recordatorios=lambda its: False, enviar_vigente=lambda i: False,
               aplazar=lambda *a: None, avisar_aplazadas=lambda *a: None, now=s.now)
    assert s.esc.estado("t_a")["n"] == 0         # se reintenta en la pasada siguiente
    s.tick()
    assert s.recs == [[("t_a", 2)]]


def test_questions_and_cards_both_climb_but_other_kinds_do_not(tmp_path):
    s = Sim(tmp_path)
    s.items = [it("t_q", T0, "pregunta"), it("t_u", T0, "tuyo"), it("t_p", T0, "externo"), it("t_g", T0, "cola")]
    s.tick(); s.tick(24)
    assert sorted(t for t, _ in s.recs[0]) == ["t_q", "t_u"]


def test_hidden_ids_are_ignored(tmp_path):
    s = Sim(tmp_path)
    s.items = [it("t_a", T0)]
    s.tick()
    s.now += 24 * H
    s.esc.tick(s.items, s.cfg, ocultas={"t_a"}, enviar_recordatorios=lambda its: s.recs.append(its) or True,
               enviar_vigente=lambda i: True, aplazar=lambda *a: None, avisar_aplazadas=lambda *a: None)
    assert not s.recs


def test_custom_settings_change_the_ladder(tmp_path):
    s = Sim(tmp_path, Bandeja(recordatorio_cada_horas=6, recordatorio_veces=1))
    s.items = [it("t_a", T0)]
    s.tick(); s.tick(6)
    assert s.recs == [[("t_a", 0)]]
    s.tick(6)
    assert s.vigs == ["t_a"]


# --- ajustes ---------------------------------------------------------------------------------------------

def test_settings_defaults_when_absent_or_invalid(tmp_path):
    d = Bandeja()
    assert (d.recordatorio_cada_horas, d.recordatorio_veces, d.vigente_tanda_dia) == (24, 3, 5)
    assert bandeja.load(tmp_path / "no-existe.yaml") == d
    f = tmp_path / "b.yaml"
    f.write_text("recordatorio_cada_horas: -3\nrecordatorio_veces: mucho\nvigente_tanda_dia: 0\n"
                 "vigente_aplazar_dias: 0\ncaducidad: quizá\nvigente_legado_dias: true\n", encoding="utf-8")
    assert bandeja.load(f) == d
    f.write_text("recordatorio_cada_horas: 12\nrecordatorio_veces: 0\nvigente_tanda_dia: 2\ncaducidad: false\n",
                 encoding="utf-8")
    cfg = bandeja.load(f)
    assert (cfg.recordatorio_cada_horas, cfg.recordatorio_veces, cfg.vigente_tanda_dia, cfg.caducidad) == (12, 0, 2, False)


# --- integración con CommandCenter y los botones ----------------------------------------------------------

def _center(tmp_path, n=1):
    shows = {f"t_d000000{i}": card(f"t_d000000{i}", body=BODY, created_at=1.0) for i in range(1, n + 1)}
    cc, desk, bot, h = setup(tmp_path, shows)
    clock = [T0]
    cc._now = lambda: clock[0]
    desk.caducidad = Escalera(tmp_path / "caducidad.json", clock=lambda: clock[0])
    desk.snoozes._clock = lambda: clock[0]
    cc.lo_tuyo_items = lambda brand=None, timeout=None: (lo_tuyo.de_tarjetas(
        [{**c, "since": T0 - 1} for c in cc.oscar_cards()[0]]), [])
    return cc, desk, bot, h, clock


def _dm(bot, prefix):
    return [m for m in bot.sent if m["chat_id"] == str(OSCAR) and m["text"].startswith(prefix)]


def test_tick_sends_reminders_then_vigente_with_the_botonera_buttons(tmp_path):
    cc, desk, bot, h, clock = _center(tmp_path)
    cc.caducidad_tick()
    assert not _dm(bot, "⏰")
    for _ in range(3):
        clock[0] += DIA
        cc.caducidad_tick()
    avisos = _dm(bot, "⏰")
    assert len(avisos) == 3 and "quedan 2 recordatorios" in avisos[0]["text"] and "último recordatorio" in avisos[2]["text"]
    clock[0] += DIA
    cc.caducidad_tick()
    [v] = _dm(bot, "❓ ¿Sigue vigente?")
    assert texts(v["markup"]) == ["🗄 Archivar", "⏳ Aplazar 30 d", "📌 Mantener 7 d"]
    clock[0] += 599
    cc.caducidad_tick()                          # menos de 10 min desde la pasada anterior: no relee
    assert len(_dm(bot, "❓")) == 1


def _hasta_vigente(tmp_path):
    cc, desk, bot, h, clock = _center(tmp_path)
    cc.caducidad_tick()
    for _ in range(4):
        clock[0] += DIA
        cc.caducidad_tick()
    return cc, desk, bot, h, clock, _dm(bot, "❓")[0]


def test_archivar_button_archives_the_card(tmp_path):
    cc, desk, bot, h, clock, v = _hasta_vigente(tmp_path)
    press(desk, v, 0)
    assert comments_of(h, "archive") == [("archive", "t_d0000001")]
    assert "archivada" in bot.edits[-1]["text"] and desk.caducidad.estado("t_d0000001") is None


def test_aplazar_button_hides_it_for_30_days(tmp_path):
    cc, desk, bot, h, clock, v = _hasta_vigente(tmp_path)
    press(desk, v, 1)
    assert "t_d0000001" in desk.snoozes.hidden() and desk.caducidad.estado("t_d0000001")["fase"] == "reposo"
    assert desk.snoozes._load()["t_d0000001"] == clock[0] + 30 * DIA
    assert not comments_of(h, "archive")


def test_mantener_button_keeps_it_visible_and_silences_7_days(tmp_path):
    cc, desk, bot, h, clock, v = _hasta_vigente(tmp_path)
    press(desk, v, 2)
    assert "t_d0000001" not in desk.snoozes.hidden()
    st = desk.caducidad.estado("t_d0000001")
    assert st["fase"] == "reposo" and st["hasta"] == clock[0] + 7 * DIA
    n = len(bot.sent)
    clock[0] += 6 * DIA
    cc.caducidad_tick()
    assert len(bot.sent) == n


def test_no_answer_snoozes_30_days_with_one_line(tmp_path):
    cc, desk, bot, h, clock, v = _hasta_vigente(tmp_path)
    clock[0] += 25 * H
    cc.caducidad_tick()
    [aviso] = _dm(bot, "⏳ Sin respuesta")
    assert "t_d0000001" in aviso["text"] and "t_d0000001" in desk.snoozes.hidden()
    cc.caducidad_tick()
    clock[0] += 5 * DIA
    cc.caducidad_tick()
    assert len(_dm(bot, "⏳ Sin respuesta")) == 1 and len(_dm(bot, "❓")) == 1


def test_disabled_does_nothing(tmp_path):
    cc, desk, bot, h, clock = _center(tmp_path)
    cc.bandeja = Bandeja(caducidad=False)
    clock[0] += 10 * DIA
    assert cc.caducidad_tick() is None and not _dm(bot, "⏰") and not _dm(bot, "❓")
