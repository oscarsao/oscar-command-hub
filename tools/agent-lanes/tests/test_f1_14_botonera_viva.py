"""F1-14: la botonera estándar llega a las fichas vivas (Dar por hecha, Otro carril, Partir, Aplazar, Más, confirmar,
Liberar). Todo con dobles: ni Hermes ni Telegram reales."""
from __future__ import annotations

import pytest

from agent_lanes import botonera, deps
from agent_lanes import decisions as D
from tests.test_commands import OSCAR, center, press, texts

TID, PARENT = "t_eeeeeee5", "t_abcdef01"


class H:
    """Hermes de mentira: guarda llamadas y un `show` editable."""

    def __init__(self, status="blocked", comments=(), assignee="claude-migrateam"):
        self.status, self.comments, self.assignee, self.calls = status, list(comments), assignee, []

    def show(self, tid):
        return {"task": {"id": tid, "status": self.status, "assignee": self.assignee, "title": "T"},
                "comments": self.comments, "events": [], "parents": [], "children": []}

    def comment(self, tid, text, author=None):
        self.calls.append(("comment", text))
        self.comments.append({"author": author, "body": text})
        return True

    def complete(self, tid, result, meta):
        self.calls.append(("complete", result))
        self.status = "done"
        return True, ""

    def assign(self, tid, profile):
        self.calls.append(("assign", profile))
        return True

    def create(self, title, body, assignee, **kw):
        self.calls.append(("create", title, assignee, body))
        return "t_99999999"

    def unblock(self, tid):
        self.calls.append(("unblock", tid))
        self.status = "ready"
        return True

    def unlink(self, parent, child):
        self.calls.append(("unlink", parent, child))
        return True


def setup(tmp_path, h):
    cc, desk, bot, _h, messages = center(tmp_path, {})
    desk.hermes_for = lambda b: h
    lane = next(l for l in desk.lanes.values() if l.kind == "implement")
    rec = {"task_id": TID, "board": lane.board, "lane": lane.name, "title": "Tarea X"}
    where = {"chat_id": str(OSCAR), "thread_id": "0", "message_id": 7, "user_id": OSCAR}
    return desk, bot, rec, where, lane


def ficha(desk, lane, state="blocked"):
    return desk.markup(state, task={"id": TID, "title": "T", "body": ""}, lane=lane, block_kind="transient",
                       ficha=True)


@pytest.mark.parametrize("state", ["blocked", "needs_input", "done", "review", "stuck"])
def test_ficha_viva_lleva_la_botonera_en_cada_estado(tmp_path, state):
    desk, bot, rec, where, lane = setup(tmp_path, H())
    t = " ".join(texts(ficha(desk, lane, state)))
    assert "➕ Más" in t and "Dar por hecha" in t and ("Responder" in t or "Otra" in t)
    assert "Integrador" in t or "integrador" in t
    # los avisos (sin ficha) no cambian
    aviso = desk.markup(state, task={"id": TID, "title": "T", "body": ""}, lane=lane, block_kind="transient")
    assert "➕ Más" not in texts(aviso)


def test_dar_por_hecha_cierra_con_motivo_y_no_se_repite(tmp_path):
    h = H()
    desk, bot, rec, where, _ = setup(tmp_path, h)
    assert desk._hecha(rec, where, "ya está en main") is True
    assert any(c[0] == "complete" and "ya está en main" in c[1] for c in h.calls)
    assert any(c[0] == "comment" and "Motivo: ya está en main" in c[1] for c in h.calls)
    n = len(h.calls)
    assert desk._hecha(rec, where, "otra vez") is True      # ya cerrada: nada más
    assert len(h.calls) == n


def test_acciones_irreversibles_piden_segundo_toque(tmp_path):
    h = H()
    desk, bot, rec, where, lane = setup(tmp_path, h)
    mk = desk.store.issue(rec, botonera.ficha("ready")["filas"])[1]    # catálogo tal cual: Archivar es irreversible
    sent = {"message_id": 7, "chat_id": str(OSCAR), "thread_id": "0", "markup": mk}
    press(desk, sent, texts(mk).index("🗄 Archivar"))
    nuevo = bot.markups[-1][2]
    assert texts(nuevo)[0].startswith("✅ Sí") and "✖ No" in texts(nuevo)
    assert not [c for c in h.calls if c[0] == "assign"]      # el 1.er toque no hace nada
    press(desk, {**sent, "markup": nuevo}, 1, cid="cq2")     # ✖ No: vuelve el teclado original
    assert "🗄 Archivar" in texts(bot.markups[-1][2])


def test_mas_despliega_lo_menos_usado_sin_gastar_el_boton(tmp_path):
    desk, bot, rec, where, lane = setup(tmp_path, H())
    mk = ficha(desk, lane)
    sent = {"message_id": 7, "chat_id": str(OSCAR), "thread_id": "0", "markup": mk}
    press(desk, sent, texts(mk).index("➕ Más"))
    t = texts(bot.markups[-1][2])
    assert {"🔀 Otro carril", "✂️ Partir en dos", "⏳ Aplazar", "🔎 Detalle"} <= set(t) and "➕ Más" not in t
    press(desk, sent, texts(mk).index("➕ Más"), cid="cq2")   # el original sigue vivo
    assert len(bot.markups) == 2


def test_partir_en_dos_crea_la_segunda_tarjeta_en_el_mismo_carril(tmp_path):
    h = H()
    desk, bot, rec, where, lane = setup(tmp_path, h)
    assert desk._partir(rec, where, "la parte de la migración") is True
    crea = next(c for c in h.calls if c[0] == "create")
    assert crea[1] == "Tarea X · parte 2" and crea[2] == lane.name and "la parte de la migración" in crea[3]
    assert any(c[0] == "comment" and "t_99999999" in c[1] for c in h.calls)
    assert ("unblock", TID) in h.calls            # la original sigue con el resto


def test_otro_carril_reasigna_solo_a_carriles_del_mismo_tablero(tmp_path):
    h = H()
    desk, bot, rec, where, lane = setup(tmp_path, h)
    otro = next((l for l in desk.lanes.values() if l.kind == "implement" and l.board == lane.board
                 and l.name != lane.name), None)
    if otro:
        assert desk._otro_carril(rec, where, otro.name) is True and ("assign", otro.name) in h.calls
    h.calls.clear()
    assert desk._otro_carril(rec, where, "carril-inventado") is False and not h.calls


def test_aplazar_oculta_la_tarjeta_y_solo_acepta_1_3_7(tmp_path):
    h = H()
    desk, bot, rec, where, _ = setup(tmp_path, h)
    assert desk._aplazar(rec, where, 5) is False
    assert desk._aplazar(rec, where, 3) is True and TID in desk.snoozes.hidden()


def test_liberar_quita_la_espera_la_reanuda_y_no_se_repite(tmp_path):
    h = H(status="triage", comments=[{"author": "x", "body": deps.wait_comment(PARENT)}])
    desk, bot, rec, where, _ = setup(tmp_path, h)
    assert deps.declared_waits(h.show(TID)) == [PARENT]
    assert desk._liberar(rec, where) is True
    assert ("unlink", PARENT, TID) in h.calls
    assert deps.declared_waits(h.show(TID)) == []                    # el runner ya no la retiene
    assert not deps.resolution_unannounced(h.show(TID))              # y no lo anuncia otra vez
    n = len(h.calls)
    assert desk._liberar(rec, where) is True and len(h.calls) == n   # segunda vez: no hace nada


def test_reintentar_no_se_ofrece_si_el_bloqueo_ya_se_repitio(tmp_path):
    desk, bot, rec, where, lane = setup(tmp_path, H())
    assert "🔄 Reintentar" in texts(ficha(desk, lane))
    desk.retries.mark(TID, "mismo motivo")
    assert "🔄 Reintentar" not in texts(ficha(desk, lane))


def test_acciones_del_catalogo_existen_en_el_desk():
    for bid, d in botonera.cargar()["botones"].items():
        assert hasattr(D, d["accion"]), bid
