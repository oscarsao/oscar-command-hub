"""Recordatorios escalonados de decisiones (recordatorios.py): reloj simulado, sin red ni Telegram."""
from datetime import datetime, timedelta

import pytest

from agent_lanes import ajustes_hub, recordatorios as rc
from agent_lanes.people import People, Person

T0 = datetime(2026, 11, 2, 10, 0, tzinfo=rc.MADRID)  # lunes 10:00


class Notifier:
    def __init__(self):
        self.sent, self.edited = [], []

    def send_to(self, chat, thread, text, **kw):
        self.sent.append((chat, text, kw.get("reply_to")))
        return {"message_id": 100 + len(self.sent)}

    def edit(self, chat, mid, text, **kw):
        self.edited.append((chat, mid, text))
        return True


class Vetos:
    def __init__(self, st=None):
        self.st = st or {}

    def state(self, tid):
        return self.st.get(tid)

    def set_state(self, tid, s):
        self.st[tid] = s


def esc(**kw):
    base = dict(activa=True, horas=(1.0, 2.0, 4.0, 24.0), destinos=("dueno",) * 4, silencio_ini="22:00",
                silencio_fin="08:00", max_avisos=4, aplicar_reversible=True, escalado=("maria", "oscar"), duenos={})
    base.update(kw)
    return rc.Escalera(**base)


def people():
    maria = Person("maria", "María", "222", "coo", ("*",), operador=True)
    ana = Person("ana", "Ana", "333", "dev", ("MigraTeam",), operador=True)
    return People("111", [maria, ana])


def make(tmp_path, reversible=True, e=None, vetos=None, aplicado=None):
    aplicado = aplicado if aplicado is not None else []
    clock = {"now": T0}
    n = Notifier()
    card = {"task": {"id": "t_1", "title": "¿Migrar ya?"}, "board": "b", "since": T0.timestamp()}
    spec = type("S", (), {"reversible": reversible, "has_options": True, "recommended": 1, "options": ["A", "B"]})()
    esc_ = e or esc()
    z = rc.Escalonador(n, people(), rc.LibroEscalera(tmp_path / "escalera.json"), lambda: [card], lambda c: spec,
                       lambda c: "MigraTeam", lambda m: aplicado.append(m) or True, vetos=vetos,
                       now=lambda: clock["now"], cargar_esc=lambda: esc_)
    return z, n, clock, aplicado


def avanza(clock, horas):
    clock["now"] = T0 + timedelta(hours=horas)


def test_escalera_1_2_4_24_etiqueta_al_dueno_y_edita_el_mismo_mensaje(tmp_path):
    z, n, clock, aplicado = make(tmp_path)
    assert z.tick() == []                      # a 0 h no hay nada
    for horas in (1, 2, 4):
        avanza(clock, horas)
        assert z.tick() == ["t_1"]
        assert z.tick() == []                  # no repite en el mismo tic
    assert len(n.sent) == 1 and len(n.edited) == 2    # 1.º envía; 2.º y 3.º editan el mismo mensaje
    assert n.sent[0][0] == "333" and 'tg://user?id=333' in n.sent[0][1] and "Ana" in n.sent[0][1]  # dueño de MigraTeam
    avanza(clock, 24)
    assert z.tick() == ["t_1"]
    assert [m["recommended"] for m in aplicado] == [1]  # reversible: aplicada tras el último aviso
    assert z.tick() == []
    avanza(clock, 48)
    assert z.tick() == [] and len(aplicado) == 1        # no se aplica dos veces


def test_irreversible_escala_a_maria_y_oscar_y_no_aplica(tmp_path):
    z, n, clock, aplicado = make(tmp_path, reversible=False)
    avanza(clock, 24)
    z.tick()
    assert aplicado == []
    chats = [s[0] for s in n.sent]
    assert chats[-2:] == ["222", "111"] and "No es reversible" in n.sent[-1][1]


def test_reversible_sin_aplicacion_activada_no_aplica(tmp_path):
    z, n, clock, aplicado = make(tmp_path, e=esc(aplicar_reversible=False))
    avanza(clock, 24)
    z.tick()
    assert aplicado == []


def test_veto_o_ya_aplicada_no_se_vuelve_a_aplicar(tmp_path):
    z, n, clock, aplicado = make(tmp_path, vetos=Vetos({"t_1": "vetoed"}))
    avanza(clock, 24)
    z.tick()
    assert aplicado == []
    z2, _, c2, ap2 = make(tmp_path / "otro", vetos=Vetos())
    avanza(c2, 24)
    z2.tick()
    assert len(ap2) == 1 and z2.vetos.state("t_1") == "ejecutada"


def test_horario_silencioso_aplaza_sin_perder(tmp_path):
    e = esc(horas=(1.0,), destinos=("dueno",), max_avisos=1, aplicar_reversible=False)
    z, n, clock, _ = make(tmp_path, e=e)
    clock["now"] = T0.replace(hour=23, minute=0)         # el aviso de 1 h ya venció pero es de noche
    assert z.tick() == [] and n.sent == []
    clock["now"] = T0 + timedelta(days=1, hours=-2)      # 08:00 del día siguiente
    assert z.tick() == ["t_1"] and len(n.sent) == 1


def test_en_silencio_cruza_medianoche_y_sin_ventana():
    assert rc.en_silencio(T0.replace(hour=23), "22:00", "08:00")
    assert rc.en_silencio(T0.replace(hour=3), "22:00", "08:00")
    assert not rc.en_silencio(T0.replace(hour=8), "22:00", "08:00")
    assert not rc.en_silencio(T0.replace(hour=23), "00:00", "00:00")
    assert not rc.en_silencio(T0, "", "")


def test_runner_parado_envia_un_solo_aviso_del_ultimo_paso_vencido(tmp_path):
    z, n, clock, _ = make(tmp_path, e=esc(aplicar_reversible=False))
    avanza(clock, 5)                                      # vencieron 1, 2 y 4 h
    assert z.tick() == ["t_1"]
    assert len(n.sent) == 1 and "aviso 3/4" in n.sent[0][1]
    avanza(clock, 6)
    assert z.tick() == []                                 # el siguiente (24 h) aún no toca


def test_max_avisos_corta_la_escalera(tmp_path):
    z, n, clock, aplicado = make(tmp_path, e=esc(max_avisos=2))
    avanza(clock, 30)
    z.tick()
    assert len(n.sent) == 1 and len(aplicado) == 1        # un solo salto al 2.º tope y cierre
    avanza(clock, 40)
    assert z.tick() == []


def test_apagado_no_envia(tmp_path):
    z, n, clock, _ = make(tmp_path, e=esc(activa=False))
    avanza(clock, 30)
    assert z.tick() == [] and n.sent == []


def test_escalera_editada_en_ajustes_usa_los_nuevos_tiempos(tmp_path):
    store = ajustes_hub.Ajustes(tmp_path / "aj.json", tmp_path / "aud.jsonl")
    previo = ajustes_hub.usar(store)
    try:
        assert not rc.cargar().activa                       # por defecto, apagado
        d = rc.cargar(ajustes_hub.valor)
        assert d.horas == (1.0, 2.0, 4.0, 24.0) and d.silencio_ini == "22:00" and d.max_avisos == 4
        store.set("decisiones.recordatorios_activa", 1, "test")
        store.set("decisiones.escalera_horas", "0.5, 3", "test")
        store.set("decisiones.duenos", "migrateam/maria", "test")
        e = rc.cargar()
        assert e.activa and e.horas == (0.5, 3.0) and e.destinos == ("dueno", "dueno")
        z, n, clock, _ = make(tmp_path / "x", e=e)
        clock["now"] = T0 + timedelta(minutes=31)
        assert z.tick() == ["t_1"] and n.sent[0][0] == "222"  # dueño configurado: María, no Ana
    finally:
        ajustes_hub.usar(previo)


def test_dueno_sin_operador_de_la_marca_es_oscar():
    assert rc.dueno(people(), esc(), "Otros").user_id == "111"
    assert rc.dueno(people(), esc(), "migrateam").user_id == "333"
