"""F1-03 (3b): siembra del tema 🎯 Decisiones con lo de lo_tuyo, preguntas con opciones → botones aunque mencionen el
guard, recordatorio de la cola a 24 h + [✅ Ya está resuelta], botones de tarjeta Hecho/Aparcar/Integrador/Archivar,
releer el estado antes de un botón de bloqueo (FL-14) y reintento ante 429. Nada sale a Telegram."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from agent_lanes import canal_avisos, lo_tuyo
from agent_lanes.canal_avisos import CanalAvisos
from agent_lanes.decisions import (CARD_DONE, CARD_INTEGRADOR, CARD_PARK, CARD_SNOOZE, COLA_RESUELTA, DecisionDesk,
                                   seed_card_keyboard_spec)
from agent_lanes.guard_route import GuardQueue, GuardRouter
from agent_lanes.telegram import TelegramAPIError, TelegramNotifier
from tests.test_canal_avisos import OWNER, TEMA, TG
from tests.test_runner_state import GOOD, FakeHermes, ok_outcome
from tests.test_runner_state import make as make_runner

H = 3600
MADRID = canal_avisos.MADRID


def ts(h, m=0, day=20):
    return datetime(2026, 10, day, h, m, tzinfo=MADRID).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def kb(tok):
    return {"inline_keyboard": [[{"text": "x", "callback_data": f"{tok}:0"}]]}


def items_3(now):
    """1 needs_input, 1 entrada de la cola del integrador y 1 tarjeta ops en review (la prueba del plan)."""
    return [lo_tuyo.item("pregunta", "t_aaaa0001", "¿Qué esquema?", board="oscarhq", since=now - 2 * H),
            lo_tuyo.item("cola", "t_aaaa0002", "Instalar dep", board="oscarhq", since=now - 30 * H, extra="pnpm"),
            lo_tuyo.item("validar", "t_aaaa0003", "Limpiar disco", board="default", since=now - 27 * H)]


def sembrador_de(items_fn, now_fn, gastados):
    def fn():
        out = []
        for i, it in enumerate(items_fn()):
            tok = f"tok{it['tid'][-4:]}"
            gastados.append(tok)
            out.append(canal_avisos.semilla(it, (lambda t=tok: kb(t)), lo_tuyo.verbo(it), now_fn()))
        return out
    return fn


def canal(tmp_path, tg, clock, items_fn, sleeps=None, vivos=None):
    sleeps = [] if sleeps is None else sleeps
    vivos = set() if vivos is None else vivos
    return CanalAvisos(tg, OWNER, TEMA, tmp_path, vivo=lambda t: t in vivos, clock=clock,
                       sembrador=sembrador_de(items_fn, clock, []), sleep=sleeps.append)


def fijado(tg):
    return [p["text"] for m, p in tg.calls if m in ("sendMessage", "editMessageText") and p["text"].startswith("🎯 <b>")]


# --- siembra del tema (FL-02) ----------------------------------------------------------------------------------

def test_refrescar_siembra_3_mensajes_con_botones_y_el_contador_dice_3(tmp_path):
    tg, clock, sleeps = TG(), Clock(ts(10)), []
    c = canal(tmp_path, tg, clock, lambda: items_3(clock()), sleeps)
    c.refrescar()
    sent = tg.sends(*TEMA)
    assert len(sent) == 3 and all(p.get("reply_markup", {}).get("inline_keyboard") for p in sent)
    assert sleeps == [1.0, 1.0]  # 1 s entre mensajes (TG-07)
    assert "3 decisiones pendientes" in fijado(tg)[-1]
    assert {e["clave"] for e in c.pendientes()} == {"pregunta:t_aaaa0001", "cola:t_aaaa0002", "validar:t_aaaa0003"}


def test_la_siembra_no_repite_lo_ya_abierto_ni_tras_reiniciar(tmp_path):
    tg, clock = TG(), Clock(ts(10))
    canal(tmp_path, tg, clock, lambda: items_3(clock())).refrescar()
    c2 = canal(tmp_path, tg, clock, lambda: items_3(clock()))  # reinicio del runner: mismo .state
    c2.refrescar()
    assert len(tg.sends(*TEMA)) == 3


def test_lo_que_deja_de_estar_pendiente_se_cierra_en_el_recalculo(tmp_path):
    tg, clock = TG(), Clock(ts(10))
    vivos = {"tok0001", "tok0002", "tok0003"}
    items = items_3(clock())
    c = canal(tmp_path, tg, clock, lambda: list(items), vivos=vivos)
    c.refrescar()
    items.pop(1)  # la entrada de la cola se resolvió
    clock.t += canal_avisos.REVISAR_SEMILLAS + 1
    c.refrescar()
    assert len(c.pendientes()) == 2 and "2 decisiones pendientes" in fijado(tg)[-1]
    assert any("Ya no está pendiente" in p["text"] for p in tg.edits())
    assert len(tg.sends(*TEMA)) == 3  # el recálculo no publica nada nuevo


def test_las_semillas_no_caducan_por_su_teclado_gastado(tmp_path):
    tg, clock = TG(), Clock(ts(10))
    c = canal(tmp_path, tg, clock, lambda: items_3(clock()), vivos=set())  # ningún teclado «vivo»
    c.refrescar()
    clock.t += canal_avisos.GRACIA + 60
    c.refrescar()
    assert len(c.pendientes()) == 3


def test_ver_la_ficha_no_cierra_la_semilla_y_decidir_si(tmp_path):
    tg, clock = TG(), Clock(ts(10))
    c = canal(tmp_path, tg, clock, lambda: items_3(clock()))
    c.refrescar()
    assert c.resuelta("tok0001", {"text": "👁 Ver", "action": "lt_open"}, "Oscar")
    assert len(c.pendientes()) == 3
    c.resuelta("tok0002", {"text": "✅ Ya está resuelta", "action": COLA_RESUELTA}, "María")
    assert len(c.pendientes()) == 2 and any("Ya está resuelta por María" in p["text"] for p in tg.edits())


def test_no_siembra_lo_que_ya_tiene_aviso_vivo_de_su_tarea(tmp_path):
    tg, clock = TG(), Clock(ts(10))
    c = canal(tmp_path, tg, clock, lambda: items_3(clock()), vivos={"livetok"})
    c._registrar({"tipo": "needs_input", "rol": "solo", "tid": "t_aaaa0001", "token": "livetok", "head": "h",
                  "chat_id": TEMA[0], "thread_id": TEMA[1], "message_id": 5})
    c.refrescar()
    assert len(tg.sends(*TEMA)) == 2 and len(c.pendientes()) == 3


def test_siembra_al_arrancar_y_otra_vez_a_las_9(tmp_path):
    tg, clock = TG(), Clock(ts(7))
    items = []
    c = canal(tmp_path, tg, clock, lambda: list(items))
    c.refrescar()  # arranque a las 7:00: siembra (vacía) y aún no cuenta como la de las 9:00
    assert c._toca() is None
    items.extend(items_3(clock()))
    clock.t = ts(9, 5)
    assert c._toca() == "diario"
    c.refrescar()
    assert len(tg.sends(*TEMA)) == 3 and c._toca() is None


def test_sin_tema_o_sin_sembrador_no_siembra(tmp_path):
    tg, clock = TG(), Clock(ts(10))
    CanalAvisos(tg, OWNER, None, tmp_path, vivo=lambda t: False, clock=clock,
                sembrador=lambda: [canal_avisos.semilla(items_3(clock())[0], kb("a"), "x", clock())]).refrescar()
    CanalAvisos(tg, OWNER, TEMA, tmp_path, vivo=lambda t: False, clock=clock).refrescar()
    assert tg.sends(*TEMA) == []


def test_un_sembrador_roto_no_tumba_la_pasada(tmp_path):
    tg = TG()

    def roto():
        raise RuntimeError("kanban caído")

    CanalAvisos(tg, OWNER, TEMA, tmp_path, vivo=lambda t: False, sembrador=roto).refrescar()  # no lanza


def test_semilla_texto_clave_y_markup_diferido():
    it = lo_tuyo.item("tuyo", "t_bbbb0001", "Decidir precio", board="default", since=60.0)
    llamado = []
    s = canal_avisos.semilla(it, lambda: llamado.append(1) or kb("z"), "👁 Ver", 3 * H + 60)
    assert s["clave"] == "tuyo:t_bbbb0001" and s["tid"] == "t_bbbb0001" and s["tipo"] == "tuyo"
    assert "t_bbbb0001" in s["texto"] and "hace 3 h" in s["texto"] and llamado == []
    ext = canal_avisos.semilla(lo_tuyo.item("externo", "oscar-hq#5", "PR"), None, "🔗 PR", 0)
    assert ext["tid"] is None


def test_lo_tuyo_incluye_las_validaciones_del_carril_ops():
    class Lane:
        def __init__(self, name, kind, board):
            self.name, self.kind, self.board = name, kind, board

    class H:
        def list_status(self, assignee, status):
            return [{"id": "t_cccc0001", "title": "Limpiar disco", "created_at": 1.0}] \
                if (assignee, status) == ("claude-ops", "review") else []

    lanes = {"claude-ops": Lane("claude-ops", "ops", "default"), "claude-hub": Lane("claude-hub", "implement", "default")}
    got = lo_tuyo.de_validar_ops(lanes, lambda b: H())
    assert [(i["kind"], i["tid"]) for i in got] == [("validar", "t_cccc0001")]
    assert lo_tuyo.verbo(got[0]) == "✅ Validar"


# --- preguntas con opciones y el guard (FL-03) ------------------------------------------------------------------

def test_pregunta_con_3_opciones_que_menciona_el_guard_va_con_botones():
    out = ok_outcome({**GOOD, "status": "needs_input", "summary": "El guard no me deja elegir solo: ¿cómo sigo?",
                      "questions": [{"question": "¿Cómo me das el esquema? (el guard me bloquea leerlo)",
                                     "options": ["Pegarlo", "Ruta del fichero", "Que lo genere"], "recommended": 0}]})
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    runner, _, _, _ = make_runner([out], hermes=h)
    assert runner.run_once() == {"t_1": "blocked:needs_input"}


def test_sin_opciones_el_heuristico_del_guard_sigue_mandando_a_consola():
    out = ok_outcome({**GOOD, "status": "needs_input", "summary": "El guard me bloquea pip install",
                      "questions": ["No puedo instalar pytest-asyncio por el guard"]})
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    runner, _, _, _ = make_runner([out], hermes=h)
    assert runner.run_once() == {"t_1": "blocked:limitacion_guard"}


# --- cola del integrador: recordatorio a 24 h y «Ya está resuelta» (FL-04) ---------------------------------------

def _pend(tid="t_1"):
    return SimpleNamespace(tid=tid, lane=SimpleNamespace(name="claude-hub", board="default"), guard=True,
                           task={"id": tid, "title": "Arreglar X"}, need="Instalar", since=0)


def test_recordatorio_a_las_24h_una_sola_vez(tmp_path):
    now = 1_000_000.0 + 25 * H
    clock = Clock(now)
    q = GuardQueue(tmp_path / "cola.json", clock)
    q.add(_pend())
    q.update("t_1", queued_at=now - 25 * H, escalated_at=now - 23 * H, announced=True)
    calls = []
    r = GuardRouter(lambda: [_pend()], lambda p, e: True, lambda p, e: calls.append(("oscar", p.tid)) or True, q,
                    now=clock, hours=2, remind_hours=24)
    assert r.tick() == ["recordar t_1"] and calls == [("oscar", "t_1")]
    assert r.tick() == []
    clock.t += 24 * H + 1
    assert r.tick() == ["recordar t_1"]  # al día siguiente, otra vez


def test_recordatorio_respeta_el_aplazado(tmp_path):
    now = 1_000_000.0 + 25 * H
    clock = Clock(now)
    q = GuardQueue(tmp_path / "cola.json", clock)
    q.add(_pend())
    q.update("t_1", queued_at=now - 25 * H, escalated_at=now - 23 * H, announced=True, snooze_until=now + H)
    r = GuardRouter(lambda: [_pend()], lambda p, e: True, lambda p, e: True, q, now=clock, hours=2, remind_hours=24)
    assert r.tick() == []


class FakeH:
    def __init__(self, status):
        self.status, self.assigned, self.completed, self.comments = status, [], [], []

    def show(self, tid):
        return {"task": {"id": tid, "status": self.status}, "comments": [], "events": []}

    def assign(self, tid, profile):
        self.assigned.append((tid, profile))
        return True

    def complete(self, tid, result, metadata):
        self.completed.append((tid, result))
        return True, ""

    def comment(self, tid, body, author=None):
        self.comments.append(body)
        return True


def _desk(h, tmp_path=None):
    edits = []
    desk = SimpleNamespace(_hermes=lambda rec: h, _who=lambda: "Oscar", _by=lambda: "",
                           _edit=lambda rec, where, state, text, **k: edits.append((state, text)), edits=edits,
                           guard_queue=GuardQueue(tmp_path / "c.json") if tmp_path else None, _trace=lambda h, t: None)
    desk._ya_resuelta = lambda rec, where: DecisionDesk._ya_resuelta(desk, rec, where)
    desk._has_comment = DecisionDesk._has_comment
    return desk


def test_ya_esta_resuelta_cierra_la_tarjeta_y_la_saca_de_la_cola(tmp_path):
    h = FakeH("blocked")
    desk = _desk(h, tmp_path)
    desk.guard_queue.add(_pend())
    assert DecisionDesk._cola_resuelta(desk, {"task_id": "t_1"}, {}) is True
    assert h.completed and "cola del integrador" in h.completed[0][1]
    assert "t_1" not in desk.guard_queue.entries()


def test_boton_de_integrador_sobre_tarjeta_ya_hecha_no_reasigna(tmp_path):
    h = FakeH("done")
    desk = _desk(h)
    assert DecisionDesk._to_integrador(desk, {"task_id": "t_1"}, {}) is True
    assert h.assigned == [] and "ya resuelta" in desk.edits[-1][1]


def test_boton_de_integrador_sobre_tarjeta_bloqueada_si_reasigna():
    h = FakeH("blocked")
    desk = _desk(h)
    assert DecisionDesk._to_integrador(desk, {"task_id": "t_1"}, {}) is True and h.assigned


def test_ya_esta_resuelta_sobre_tarjeta_cerrada_solo_edita(tmp_path):
    h = FakeH("done")
    desk = _desk(h, tmp_path)
    desk.guard_queue.add(_pend())
    assert DecisionDesk._cola_resuelta(desk, {"task_id": "t_1"}, {}) is True
    assert h.completed == [] and "t_1" not in desk.guard_queue.entries()


# --- botones de tarjeta sembrada (FL-12) --------------------------------------------------------------------------

def test_botones_de_tarjeta_sembrada():
    spec = seed_card_keyboard_spec()
    assert [b["action"] for row in spec for b in row] == [CARD_DONE, CARD_SNOOZE, CARD_INTEGRADOR, CARD_PARK,
                                                                       "quitar_aviso"]


def test_integrador_de_tarjeta_reasigna_y_comenta():
    h = FakeH("ready")
    desk = _desk(h)
    desk._now = datetime.now
    rec = {"task_id": "t_1", "board": "default", "card": True, "head": "h"}
    assert DecisionDesk._card_act(desk, rec, {"action": CARD_INTEGRADOR}, {}) is True
    assert h.assigned and h.assigned[0][1] == "oscar-e2" and h.comments


def test_tarjeta_ya_decidida_no_hace_nada():
    h = FakeH("done")
    desk = _desk(h)
    desk._now = datetime.now
    rec = {"task_id": "t_1", "board": "default", "card": True, "head": "h"}
    assert DecisionDesk._card_act(desk, rec, {"action": CARD_INTEGRADOR}, {}) is True and h.assigned == []


# --- 429 (TG-07) --------------------------------------------------------------------------------------------------

class TG429(TelegramNotifier):
    def __init__(self, fallos):
        super().__init__("123:fake", OWNER, None)
        self.fallos, self.calls, self.slept = fallos, 0, []
        self.sleep = self.slept.append

    def _api(self, method, payload, timeout=None):
        self.calls += 1
        if self.fallos:
            self.fallos -= 1
            raise TelegramAPIError("telegram HTTP 429", "Too Many Requests: retry after 3")
        return {"message_id": 9}


def test_send_reintenta_una_vez_tras_un_429():
    tg = TG429(1)
    assert tg.send_to(OWNER, None, "hola")["message_id"] == 9
    assert tg.calls == 2 and tg.slept == [3.5]


def test_send_con_dos_429_seguidos_sube_el_error():
    tg = TG429(2)
    try:
        tg.send_to(OWNER, None, "hola")
    except TelegramAPIError:
        pass
    else:
        raise AssertionError("debía fallar")
    assert tg.calls == 2


def test_otro_error_no_se_reintenta():
    class Bad(TG429):
        def _api(self, method, payload, timeout=None):
            self.calls += 1
            raise TelegramAPIError("telegram HTTP 400", "Bad Request: chat not found")

    tg = Bad(0)
    try:
        tg.send_to(OWNER, None, "x")
    except TelegramAPIError:
        pass
    assert tg.calls == 1 and tg.slept == []


# --- robustez (revisión) ------------------------------------------------------------------------------------------

def test_refrescar_no_lanza_con_el_estado_de_siembra_roto(tmp_path):
    import json
    tg, clock = TG(), Clock(ts(10))
    (tmp_path / "decisiones_tema.json").write_text(json.dumps({"abiertas": {}, "sembrado": {"revisado_at": "x"}}),
                                                   encoding="utf-8")
    c = canal(tmp_path, tg, clock, lambda: items_3(clock()))
    c._inicio_pendiente = False
    c.refrescar()  # no lanza: _toca() falla con float("x") y se registra
    c.enviar("monitor", "🔴 caída")  # y los avisos siguen saliendo
    assert any(p["text"] == "🔴 caída" for p in tg.sends())


def test_tarjeta_tuya_conserva_su_estado_para_elegir_botones():
    got = lo_tuyo.de_tarjetas([{"board": "default", "task": {"id": "t_dddd0001", "title": "x", "status": "running"},
                                "since": 1.0}])
    assert got[0]["status"] == "running"


def test_el_render_real_admite_estado_none():
    from agent_lanes.notices import render
    assert "t_1" in render(None, "t_1", "T", "claude-hub", "✅ ya resuelta (ahora en done); no hago nada")


def test_ya_esta_resuelta_desde_triage_la_saca_y_la_cierra(tmp_path):
    class TriageH(FakeH):
        def __init__(self):
            super().__init__("triage")
            self.requeued = []

        def complete(self, tid, result, metadata):
            if self.status == "triage":
                return False, "not in a completable status"
            return super().complete(tid, result, metadata)

        def requeue_triage(self, tid):
            self.requeued.append(tid)
            self.status = "ready"
            return "ready"

    h = TriageH()
    desk = _desk(h, tmp_path)
    desk.guard_queue.add(_pend())
    assert DecisionDesk._cola_resuelta(desk, {"task_id": "t_1"}, {}) is True
    assert h.requeued == ["t_1"] and h.completed
