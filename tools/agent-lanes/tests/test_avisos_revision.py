"""Resumen agrupado de tarjetas listas para revisar, silencio, tarjetas mudas y /avisos (avisos_revision.py)."""
import re
from datetime import datetime

import pytest

from agent_lanes import avisos_revision
from agent_lanes.avisos_revision import (AV_1H, AV_3H, AV_MANANA, AV_ON, AV_TAREA, AvisosRevision, AvisosRevisionTicker,
                                         comando, interceptar, parse_duracion)
from agent_lanes.commands import COMMANDS, CommandCenter
from agent_lanes.resumen_carriles import MADRID

H = 3600
T0 = datetime(2026, 10, 23, 10, 0, tzinfo=MADRID).timestamp()


class Reloj:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class Notifier:
    def __init__(self):
        self.edits = []

    def edit(self, chat_id, message_id, text, *, html=True, reply_markup=None):
        self.edits.append((chat_id, message_id, text, reply_markup))
        return True


class Desk:
    notifier = None


def issue(rec, spec):
    return "tok", {"inline_keyboard": [[{"text": b["text"], "action": b["action"], "index": b.get("index")}
                                       for b in row] for row in spec], "rec": rec}


@pytest.fixture
def av(tmp_path):
    reloj, enviados = Reloj(), []
    a = AvisosRevision(lambda text, markup=None: enviados.append((text, markup)), "-100", issue=issue,
                       state_path=tmp_path / "avisos.json", clock=reloj)
    a.reloj, a.enviados = reloj, enviados
    return a


@pytest.fixture
def activo(av, monkeypatch):
    monkeypatch.setattr(avisos_revision, "ACTIVO", av)
    return av


def tarjeta(av, n, resumen="hecho"):
    av.registrar({"id": f"t_{n}", "title": f"Tarea {n}"}, resumen)


def botones(markup):
    return [b for row in markup["inline_keyboard"] for b in row]


def test_tres_tarjetas_en_menos_de_dos_horas_un_solo_mensaje(av):
    for n in (1, 2):
        tarjeta(av, n)
        av.reloj.t += 600
        assert av.tick() == []  # el lote aún no se ha llenado y la ventana no ha vencido
    tarjeta(av, 3, "falta revisar el CSS")
    assert av.tick() == ["t_1", "t_2", "t_3"]
    assert len(av.enviados) == 1
    texto = av.enviados[0][0]
    assert "3 tarjetas listas para revisar" in texto
    assert "t_1 · Tarea 1 · hecho" in texto and "t_3 · Tarea 3 · falta revisar el CSS" in texto
    assert av.tick() == [] and len(av.enviados) == 1  # nada se repite


def test_sale_al_cumplir_la_ventana_aunque_no_haya_lote(av):
    tarjeta(av, 1)
    av.reloj.t += 2 * H - 1
    assert av.tick() == []
    av.reloj.t += 1
    assert av.tick() == ["t_1"]
    assert "1 tarjeta lista para revisar" in av.enviados[0][0]


def test_silencio_1h_no_envia_nada_y_al_terminar_sale_un_resumen(av):
    av.silenciar(av.reloj.t + H)
    for n in (1, 2, 3, 4):
        tarjeta(av, n)
    assert av.tick() == [] and av.enviados == []  # lote lleno pero silenciado
    av.reloj.t += H - 1
    assert av.tick() == [] and av.enviados == []
    av.reloj.t += 2
    assert av.tick() == ["t_1", "t_2", "t_3", "t_4"]
    assert len(av.enviados) == 1


def test_lo_retenido_por_el_silencio_sale_al_terminar_aunque_sea_una_sola_tarjeta(av):
    av.silenciar(av.reloj.t + H)
    tarjeta(av, 1)
    av.reloj.t += H + 1
    assert av.tick() == ["t_1"]


def test_no_avisar_de_esta_tarea_no_vuelve_a_notificar_pero_las_demas_si(av):
    av.mutear("t_2")
    tarjeta(av, 1)
    tarjeta(av, 2)  # tampoco por renotify: registrar la ignora
    tarjeta(av, 3)
    tarjeta(av, 4)
    assert av.tick() == ["t_1", "t_3", "t_4"]
    tarjeta(av, 2, "más actividad")
    av.reloj.t += 3 * H
    assert av.tick() == []


def test_misma_tarjeta_dos_veces_no_duplica_y_conserva_antiguedad(av):
    tarjeta(av, 1, "v1")
    av.reloj.t += H
    tarjeta(av, 1, "v2")
    av.reloj.t += H
    assert av.tick() == ["t_1"]
    assert "v2" in av.enviados[0][0] and "v1" not in av.enviados[0][0]


def test_botones_del_resumen_y_silencio_1h_3h_manana(av):
    for n in (1, 2, 3):
        tarjeta(av, n)
    av.tick()
    texto, markup = av.enviados[0]
    acciones = [b["action"] for b in botones(markup)]
    assert acciones == [AV_1H, AV_3H, AV_MANANA, AV_TAREA, AV_TAREA, AV_TAREA]
    rec, notifier = markup["rec"], Notifier()
    desk = Desk()
    desk.notifier = notifier
    where = {"chat_id": "-100", "message_id": 7}
    assert av.on_button(AV_1H, rec, where, desk) is True
    assert av.silenciado_hasta() == T0 + H
    av.on_button(AV_3H, rec, where, desk)
    assert av.silenciado_hasta() == T0 + 3 * H
    av.on_button(AV_MANANA, rec, where, desk)
    assert av.silenciado_hasta() == datetime(2026, 10, 24, 9, 0, tzinfo=MADRID).timestamp()
    assert "Silenciado hasta 24-10 09:00" in notifier.edits[-1][2]
    assert [b["action"] for b in botones(notifier.edits[-1][3])] == [AV_ON]
    av.on_button(AV_ON, rec, where, desk)
    assert av.silenciado_hasta() is None


def test_boton_no_avisar_de_tarea_silencia_solo_esa(av):
    for n in (1, 2, 3):
        tarjeta(av, n)
    av.tick()
    markup = av.enviados[0][1]
    boton = [b for b in botones(markup) if b["action"] == AV_TAREA][1]  # t_2
    desk = Desk()
    desk.notifier = Notifier()
    assert av.on_button(AV_TAREA, markup["rec"], {"chat_id": "-100", "message_id": 7}, desk, boton["index"]) is True
    assert av._load()["mudas"] == ["t_2"]
    nuevo = desk.notifier.edits[-1]
    assert "t_2: no vuelvo a avisar" in nuevo[2]
    assert [b["text"] for b in botones(nuevo[3]) if b["action"] == AV_TAREA] == [
        "🙈 No avisar de t_1", "🙈 No avisar de t_3"]
    assert av.on_button(AV_TAREA, markup["rec"], None, desk, 99) is False


def test_interceptar_recoge_done_y_review_sin_sonido_y_deja_pasar_el_resto(activo):
    task = {"id": "t_9", "title": "Algo"}
    assert interceptar("done", task, "lista para merge", True, for_oscar="Hecho el cambio") is False
    assert interceptar("review", task, "pendiente de tu validación", True, summary="resumen") is False
    assert interceptar("blocked", task, "x", True) is True       # otros estados siguen sonando
    assert interceptar("done", task, "x", False) is False         # sin alerta no se recoge nada
    p = activo._load()["pendientes"]
    assert [x["tid"] for x in p] == ["t_9"] and p[0]["resumen"] == "resumen"


def test_interceptar_sin_modulo_activo_no_cambia_nada(monkeypatch):
    monkeypatch.setattr(avisos_revision, "ACTIVO", None)
    assert interceptar("done", {"id": "t_1"}, "x", True) is True


def test_interceptar_si_falla_conserva_el_aviso(activo, monkeypatch):
    monkeypatch.setattr(activo, "registrar", lambda *a: (_ for _ in ()).throw(OSError("disco")))
    assert interceptar("done", {"id": "t_1"}, "x", True) is True


def test_tarjeta_muda_en_interceptar_no_suena_ni_se_acumula(activo):
    activo.mutear("t_1")
    assert interceptar("done", {"id": "t_1"}, "x", True) is False
    assert activo._load()["pendientes"] == []


def test_si_el_envio_falla_las_tarjetas_vuelven_a_pendientes(tmp_path):
    def roto(text, markup=None):
        raise RuntimeError("telegram caído")
    reloj = Reloj()
    a = AvisosRevision(roto, "-100", state_path=tmp_path / "a.json", clock=reloj)
    for n in (1, 2, 3):
        tarjeta(a, n)
    assert a.tick() == []
    assert [p["tid"] for p in a._load()["pendientes"]] == ["t_1", "t_2", "t_3"]


def test_ticker_respeta_su_intervalo(av):
    reloj = Reloj()
    t = AvisosRevisionTicker(av, minutes=5, clock=reloj)
    for n in (1, 2, 3):
        tarjeta(av, n)
    assert t.tick() == ["t_1", "t_2", "t_3"]
    tarjeta(av, 4)
    tarjeta(av, 5)
    tarjeta(av, 6)
    assert t.tick() == []
    reloj.t += 301
    assert len(t.tick()) == 3


def test_comando_avisos_off_on_y_estado(activo):
    assert "activos" in comando("")
    assert "silenciados hasta 23-10 11:00" in comando("off 1h") and activo.silenciado_hasta() == T0 + H
    assert "silenciados hasta 23-10 13:00" in comando("off 3h") and activo.silenciado_hasta() == T0 + 3 * H
    assert "silenciados hasta 24-10 09:00" in comando("off mañana")
    assert "apagados" in comando("off") and activo.silenciado_hasta() == "siempre"
    assert "apagados" in comando("")
    assert "reactivados" in comando("on") and activo.silenciado_hasta() is None
    assert "No entiendo" in comando("off cuando sea")
    assert "Uso:" in comando("x")


def test_comando_comparte_estado_con_los_botones(activo):
    comando("off 1h")
    tarjeta(activo, 1)
    activo.reloj.t += 5 * H
    assert activo.silenciado_hasta() is None  # caducó
    assert activo.tick() == ["t_1"]


def test_comando_sin_modulo_activo(monkeypatch):
    monkeypatch.setattr(avisos_revision, "ACTIVO", None)
    assert "no están activos" in comando("off")


def test_parse_duracion():
    assert parse_duracion("1h", T0) == T0 + H
    assert parse_duracion("2,5h", T0) == T0 + 2.5 * H
    assert parse_duracion("siempre", T0) == "siempre"
    assert parse_duracion("manana", T0) == datetime(2026, 10, 24, 9, 0, tzinfo=MADRID).timestamp()
    assert parse_duracion("0h", T0) is None and parse_duracion("pronto", T0) is None


def test_ayuda_lista_todos_los_comandos_y_avisos():
    en_ayuda = {c for c, _ in COMMANDS}
    definidos = {n[4:] for n in dir(CommandCenter) if n.startswith("cmd_")}
    assert definidos - en_ayuda == set(), "comando sin documentar en COMMANDS (/ayuda y menú de Telegram)"
    assert "avisos" in en_ayuda and "/avisos" in CommandCenter.help_text()
    assert re.search(r"/avisos off \[1h\|3h\|mañana\|siempre\]", CommandCenter.help_text())


class FakeMensajes:
    """MessageStore con copias de la ficha: `_tokens` las consultaría si el rec del resumen llevase task_id."""

    def all_messages(self, tid):
        return [{"token": "tok_ficha"}]


def test_botones_del_resumen_pasan_por_decisiondesk_sin_excepcion_y_se_consumen_una_vez(tmp_path, monkeypatch):
    from agent_lanes.decisions import CallbackStore, DecisionDesk
    from tests.test_decisions import LANE, OSCAR, FakeTG

    reloj, enviados, tg = Reloj(), [], FakeTG()
    desk = DecisionDesk(tg, CallbackStore(tmp_path / "cb"), lanes={LANE.name: LANE}, hermes_for=lambda b: None,
                        spawn=lambda fn: fn(), messages=FakeMensajes(), owner_id=str(OSCAR))
    a = AvisosRevision(lambda text, markup=None: enviados.append((text, markup)), "-100", issue=desk.store.issue,
                       state_path=tmp_path / "avisos.json", clock=reloj)
    monkeypatch.setattr(avisos_revision, "ACTIVO", a)
    for n in (1, 2, 3):
        tarjeta(a, n)
    a.tick()

    def pulsar(data):
        desk._callback({"id": "cq", "from": {"id": OSCAR}, "data": data,
                        "message": {"chat": {"id": -100}, "message_id": 7, "message_thread_id": 5}})

    datas = [b["callback_data"] for b in botones(enviados[0][1])]  # 1 h, 3 h, mañana, y una por tarjeta
    pulsar(datas[3])  # 🙈 No avisar de t_1
    assert a._load()["mudas"] == ["t_1"]
    assert desk.store.get(datas[3].partition(":")[0]) is None  # token consumido
    pulsar(datas[3])  # repetirlo no aplica nada otra vez
    assert a._load()["mudas"] == ["t_1"] and tg.answers[-1][1] == "Esta decisión ya no está activa"
    nuevos = [b["callback_data"] for b in botones(tg.edits[-1]["markup"])]
    pulsar(nuevos[0])  # el teclado reeditado sigue vivo: 🔕 1 h
    assert a.silenciado_hasta() == T0 + H
