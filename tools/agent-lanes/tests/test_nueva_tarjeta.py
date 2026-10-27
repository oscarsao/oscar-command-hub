"""`lanes.py tarjeta nueva`: origen bien formado, veto de marca, duplicada, aviso al solicitante (dobles, sin hermes)."""
from __future__ import annotations

import json
import subprocess

import pytest

from agent_lanes import nueva_tarjeta as nt
from agent_lanes.config import Lane
from agent_lanes.hermes import HermesCLI
from agent_lanes.notices import render
from agent_lanes.people import People, Person
from agent_lanes.telegram import ORIGIN_RE

MIG = Lane(name="claude-migrateam", board="migrateam", repo="C:/m", base="main")
HUB = Lane(name="claude-hub", board="default", repo="C:/h", base="main")
LANES = {"claude-migrateam": MIG, "claude-hub": HUB}
BRANDS = {"claude-migrateam": "MigraTeam"}
HINTS = {"claude-migrateam": ("migrateam",), "claude-hub": ("agent-lanes", "hermes")}
CFG = {"duplicado_umbral": 0.6, "ventana_dias": 14}
PEOPLE = People("1", [
    Person("maria", "María", "5000000001", "coo", ("*",), frozenset(), True),
    Person("angel", "Ángel", "777", "dev", ("Píldora",), frozenset()),
])


class FakeH:
    def __init__(self, existentes=()):
        self.existentes, self.created, self.subs = list(existentes), [], []

    def list_status_all(self, status):
        return [t for t in self.existentes if t["status"] == status]

    def create(self, title, body, assignee, **kw):
        self.created.append((title, body, assignee, kw))
        return "t_abcdef01"

    def subscribe_telegram(self, tid, chat, thread):
        self.subs.append((tid, chat, thread))
        return True


def run(h, **kw):
    base = dict(titulo="[Hub] Cambiar el aviso de cierre", texto="detalle", solicitante="maria", chat=None, thread=None,
                mensaje=None, carril="claude-hub", proyecto=None, repo=None, prioridad=None, forzar=False,
                dry_run=False, people=PEOPLE, lanes=LANES, hermes_for=lambda b: h, brands=BRANDS, hints=HINTS, cfg=CFG)
    return nt.crear(**{**base, **kw})


def test_origen_bien_formado_y_pidio():
    h = FakeH()
    res = run(h, chat="-1003530490339", thread="230", mensaje="991")
    body = h.created[0][1]
    assert body.splitlines()[0] == "Origen-Telegram: chat=-1003530490339 thread=230"
    m = ORIGIN_RE.search(body)
    assert (m.group(1), m.group(2)) == ("-1003530490339", "230")
    assert "Pidió: María" in body and "Mensaje-Telegram: 991" in body
    assert res.task_id == "t_abcdef01" and h.created[0][3]["created_by"] == "maria"


def test_sin_chat_usa_el_dm_de_la_persona():
    h = FakeH()
    run(h)
    assert h.created[0][1].startswith("Origen-Telegram: chat=5000000001 thread=0\n")


def test_origen_no_numerico_se_rechaza():
    with pytest.raises(nt.Rechazo) as exc:
        run(FakeH(), chat="abc")
    assert exc.value.code == 2


@pytest.mark.parametrize("kw", [{"solicitante": "angel", "carril": "claude-migrateam"},  # Ángel solo Píldora
                                {"solicitante": "angel"},                                 # claude-hub = sistema
                                {"solicitante": "nadie"}])
def test_veto_de_marca_y_solicitante_desconocido(kw):
    h = FakeH()
    with pytest.raises(nt.Rechazo) as exc:
        run(h, **kw)
    assert exc.value.code == 2 and h.created == []


def test_oscar_nunca_vetado():
    h = FakeH()
    run(h, solicitante="oscar", carril="claude-migrateam")
    assert len(h.created) == 1


def test_carril_por_proyecto_y_ambiguo():
    h = FakeH()
    assert run(h, carril=None, proyecto="migrateam").lane == "claude-migrateam"
    with pytest.raises(nt.Rechazo):
        run(h, carril=None, titulo="algo", texto="sin pistas")


def test_duplicada_detectada_y_forzada():
    h = FakeH([{"id": "t_1", "title": "[Hub] Cambiar el aviso de cierre", "status": "ready"}])
    with pytest.raises(nt.Rechazo) as exc:
        run(h)
    assert exc.value.code == 3 and "t_1" in str(exc.value) and h.created == []
    assert run(h, forzar=True).task_id and h.created


def test_dry_run_no_crea():
    h = FakeH()
    res = run(h, dry_run=True)
    assert res.task_id is None and h.created == [] and h.subs == []


def test_aviso_al_solicitante_suscribe_su_chat_y_tema():
    h = FakeH()
    res = run(h, chat="-100123", thread="45")
    assert h.subs == [("t_abcdef01", "-100123", "45")] and res.avisado


def test_hermes_subscribe_y_respeta_modo_notify_al_limpiar():
    calls = []
    subs = [{"task_id": "t_1", "platform": "telegram", "chat_id": "9", "thread_id": "4", "delivery_mode": "notify"},
            {"task_id": "t_1", "platform": "telegram", "chat_id": "8", "thread_id": ""}]

    def runner(args, **kw):
        calls.append(args[4:])
        return subprocess.CompletedProcess(args, 0, json.dumps(subs) if args[4] == "notify-list" else "", "")

    h = HermesCLI("default", exe="hermes", runner=runner)
    assert h.subscribe_telegram("t_1", "9", "4")
    assert calls[0] == ["notify-subscribe", "--platform", "telegram", "--chat-id", "9", "--thread-id", "4",
                        "--delivery-mode", "notify", "t_1"]
    assert h.drop_telegram_subs("t_1") == ["8"]


def test_ficha_muestra_quien_pidio():
    body = nt.cuerpo("## Objetivo\nhacer algo", PEOPLE.lookup("5000000001"), "1", "0")
    assert nt.pidio_de(body) == "María"
    assert "Pidió: María" in render("running", "t_1", "Título", "claude-hub", "en curso", body=body)


def test_reparar_origen_propone_sin_aplicar():
    mal = {"id": "t_9", "title": "x", "created_by": "maria", "body": "Origen-Telegram: María (DM)\n\ntexto"}
    con_chat = {"id": "t_8", "title": "y", "body": "Origen-Telegram: chat=-100 tema=5\n"}
    bien = {"id": "t_7", "title": "z", "body": "Origen-Telegram: chat=-100 thread=5\n"}
    assert nt.proponer_origen(mal, PEOPLE) == "Origen-Telegram: chat=5000000001 thread=0"
    assert nt.proponer_origen(con_chat, PEOPLE) == "Origen-Telegram: chat=-100 thread=5"
    assert nt.proponer_origen(bien, PEOPLE) is None

    class H:
        def list_status_all(self, st):
            return [mal, con_chat, bien] if st == "ready" else []

    lines: list[str] = []
    assert nt.reparar_main([], hermes_for=lambda b: H(), out=lines.append, people=PEOPLE) == 0
    text = "\n".join(lines)
    assert "t_9" in text and "t_8" in text and "t_7" not in text and "En seco" in text
