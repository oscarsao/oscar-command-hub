"""D9/D6 (26-10): la aprobación de una marca de Ángel llega también a su tema de Desarrollo y a su DM, con un solo voto
válido; María aprueba como Oscar. Ids de Telegram ficticios; nada sale a Internet."""
from __future__ import annotations

import json

from agent_lanes import canal_avisos
from agent_lanes.notices import MessageStore, TaskNotices
from agent_lanes.people import load_people
from tests.test_botones_tarjetas import BODY, card, comments_of, decisiones, setup
from tests.test_canal_avisos import GESTION, OWNER, TG, _desk
from tests.test_commands import press

ANGEL, MARIA = "5000000011", "5000000012"
DESARROLLO = ("-1009990001", "29")
YAML = f"""people:
  angel:
    name: Ángel
    user_id: "{ANGEL}"
    role: dev
  maria:
    name: María
    user_id: "{MARIA}"
    role: coo
"""
_TODO = ["desarrollo.aprobar", "desarrollo.publicar"]
HQ = {"personas": [  # permisos de Oscar HQ (GET /api/equipo/permisos)
    {"telegram_id": ANGEL, "permisos": {m: _TODO for m in ("Píldora", "Signal", "Hub")}},
    {"telegram_id": MARIA, "permisos": {"*": _TODO}}]}
KB = {"inline_keyboard": [[{"text": "✅ Aprobar", "callback_data": "tokApr123:0"}]]}


def _people(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "people.yaml"
    p.write_text(YAML, encoding="utf-8")
    return load_people(OWNER, p, hq=lambda: HQ)


def _aprobacion(tmp_path, lane):
    """Publica la ficha «Aprobar» de `lane` y devuelve (bot, store, desk)."""
    tg, store = TG(), MessageStore()
    desk = _desk(tmp_path, tg, store)
    desk.people = _people(tmp_path)
    tg.owner_id, tg.collab_view = OWNER, lambda t: "resumen"  # el DM de Ángel está filtrado como colaborador
    tg.collab_chats = {ANGEL}
    _, markup = desk.store.issue({"task_id": "t_1", "board": "default", "lane": lane, "title": "T"},
                                 [[{"action": "approve", "text": "✅ Aprobar"}]])
    canal_avisos.publicar(desk.canal, TaskNotices(tg, store), "aprobar", "t_1", "✅ t_1 · lista", None,
                          DESARROLLO, alert=True, reply_markup=markup)
    return tg, store, desk, markup


def _destinos(tg):
    return {(str(p["chat_id"]), str(p.get("message_thread_id") or "0")) for p in tg.sends() if p.get("reply_markup")}


def test_oscarhq_y_hub_van_a_gestion_desarrollo_y_dm_de_angel(tmp_path):
    for lane in ("claude-oscarhq", "claude-hub"):
        tg, store, _, markup = _aprobacion(tmp_path / lane, lane)
        assert _destinos(tg) == {(GESTION, "777"), DESARROLLO, (ANGEL, "0")}
        dm = [p for p in tg.sends(ANGEL)][0]
        assert dm["reply_markup"] == markup  # botones reales aunque su DM esté filtrado
        assert len(store.all_messages("t_1")) == 3  # principal + copia de Gestión + DM


def test_migrateam_no_se_copia_a_angel(tmp_path):
    tg, store, _, _ = _aprobacion(tmp_path, "claude-migrateam")
    assert not tg.sends(ANGEL) and ANGEL not in {c for c, _ in _destinos(tg)}
    assert (GESTION, "777") in _destinos(tg)


def test_solo_las_aprobaciones_se_copian(tmp_path):
    tg, store, desk, markup = _aprobacion(tmp_path / "a", "claude-oscarhq")
    tg.calls.clear()
    canal_avisos.publicar(desk.canal, TaskNotices(tg, store), "needs_input", "t_2", "❓ t_2", None, DESARROLLO,
                          alert=True, reply_markup=markup)
    assert not tg.sends(ANGEL)


def test_un_solo_voto_y_la_copia_decidida_queda_inerte(tmp_path):
    cc, desk, bot, h = setup(tmp_path, {"t_d0000001": card("t_d0000001", body=BODY)})
    desk.people = _people(tmp_path)
    decisiones(desk)
    msg = bot.sent[-1]
    press(desk, msg, 1, user=int(MARIA), cid="c1")
    assert len(comments_of(h, "complete")) == 1 and "por María" in comments_of(h, "complete")[0][2]
    press(desk, msg, 1, user=int(ANGEL), cid="c2")  # otra copia del mismo teclado, ya decidida
    press(desk, msg, 1, user=int(OWNER), cid="c3")
    assert len(comments_of(h, "complete")) == 1
    assert bot.answers[-2:] == ["Esta decisión ya no está activa"] * 2


def test_maria_aprueba_sin_oscar_y_queda_en_la_auditoria(tmp_path):
    tg, store = TG(), MessageStore()
    desk = _desk(tmp_path, tg, store)
    desk.people = _people(tmp_path)
    desk.hermes_for = lambda b: type("H", (), {"comment": staticmethod(lambda *a, **k: True)})()
    desk._find_pr = lambda slug, tid: (7, "https://example.test/o/r/pull/7")
    desk.links = type("L", (), {"repo_slug": staticmethod(lambda lane: "o/r"),
                                "__call__": lambda self, *a, **k: []})()
    desk._edit = lambda *a, **k: None
    rec = {"task_id": "t_1", "board": "default", "lane": "claude-oscarhq", "title": "T"}
    ok = desk._en_nombre_de(int(MARIA), lambda: desk._approve(rec, {"user_id": int(MARIA), "chat_id": "1",
                                                                     "message_id": 1}))()
    assert ok is True or ok is None
    lineas = [json.loads(x) for x in (tmp_path / "integrador_telegram" / "auditoria.jsonl").read_text("utf-8").splitlines()]
    assert lineas[-1]["paso"] == "aprobar" and lineas[-1]["quien"] == "María" and lineas[-1]["pr"] == "7"
