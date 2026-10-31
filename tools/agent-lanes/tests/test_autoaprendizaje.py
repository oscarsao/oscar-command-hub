"""Autoaprendizaje (t_0f1d02ce): la retro propone reglas con evidencia, lecciones con id, contador de disparos y resumen
en el prompt del worker. Todo con dobles: sin red, sin hermes, sin Telegram."""
import logging
from datetime import datetime
from types import SimpleNamespace

from agent_lanes import bloqueos, lecciones, retro
from agent_lanes.worker import build_prompt

NOW = datetime(2026, 10, 26, 10, 0).timestamp()
FRONT = "---\nid: {id}\nfecha: {fecha}\norigen: tarjeta t_1\nregla: \"bloqueos.yaml: x\"\nestado: {estado}\n---\n# {t}\n\ncuerpo\n"


def lec(d, id="L-0002", fecha="2026-10-01", estado="activa", t="Titulo"):
    (d / f"{id}.md").write_text(FRONT.format(id=id, fecha=fecha, estado=estado, t=t), encoding="utf-8")


def blocked(n, reason="la base de datos 17 es read-only token=abc123456", age_days=1):
    return [{"id": f"t_{k:08x}", "block_reason": reason.replace("17", str(k)), "updated_at": NOW - age_days * 86400}
            for k in range(n)]


# --- propuestas ------------------------------------------------------------------------------------------------------

def test_propuesta_solo_sobre_umbral_y_ventana():
    assert retro.propuestas(blocked(4), NOW, 5) == []
    p = retro.propuestas(blocked(5), NOW, 5)
    assert len(p) == 1 and p[0]["n"] == 5 and len(p[0]["ids"]) == 5
    assert retro.propuestas(blocked(5, age_days=9), NOW, 5, days=7) == []  # fuera de la ventana


def test_propuesta_ignora_lo_que_ya_tiene_regla_y_oculta_secretos():
    assert retro.propuestas(blocked(6, "toca rutas vetadas: x"), NOW, 5) == []
    p = retro.propuestas(blocked(5), NOW, 5)[0]
    assert "abc123456" not in p["ejemplo"] and "«oculto»" in p["ejemplo"]
    assert "\\d+" in p["regex"]


class Fake:
    def __init__(self, tasks):
        self.cards, self.states, self.tasks = [], {}, tasks

    def _state(self, k):
        return dict(self.states.get(k, {}))

    def _save(self, k, **d):
        self.states[k] = {**self.states.get(k, {}), **d}

    def hermes_for(self, board):
        return SimpleNamespace(create=lambda *a, **kw: self.cards.append((board, a, kw)) or "t_00000001")


def test_tarjeta_unica_por_semana_para_oscar(tmp_path):
    d = tmp_path / "lec"
    d.mkdir()
    lec(d)
    i = Fake(None)
    tasks = {"blocked": blocked(5)}
    ap = retro._autoaprendizaje(i, tasks, NOW, "2026-W44", d)
    board, (title, body, assignee), kw = i.cards[0]
    assert board == "default" and assignee == "oscar" and title.startswith("[DECISIÓN]")
    assert kw["key"] == "retro-propuestas-2026-W44" and "t_00000000" in body and "L-0003" in body and "NO se ha aplicado" in body
    assert ap["propuestas"] == 1 and ap["creadas"] == 1
    retro._autoaprendizaje(i, tasks, NOW, "2026-W44", d)  # misma semana: no repite
    assert len(i.cards) == 1
    sig = {"blocked": [{**t, "updated_at": NOW + 6 * 86400} for t in tasks["blocked"]]}  # sigue pasando la semana siguiente
    retro._autoaprendizaje(i, sig, NOW + 7 * 86400, "2026-W45", d)
    assert len(i.cards) == 2 and i.states["retro_propuestas"]["creadas"] == 2


def test_sin_propuestas_no_hay_tarjeta_y_un_fallo_no_rompe(tmp_path):
    i = Fake(None)
    ap = retro._autoaprendizaje(i, {"blocked": blocked(2)}, NOW, "2026-W44", tmp_path / "nada")
    assert i.cards == [] and ap["propuestas"] == 0
    i.hermes_for = lambda b: (_ for _ in ()).throw(RuntimeError("boom"))
    assert retro._autoaprendizaje(i, {"blocked": blocked(5)}, NOW, "2026-W44", tmp_path) is None


# --- lecciones -------------------------------------------------------------------------------------------------------

def test_frontmatter_invalido_se_ignora_con_log(tmp_path, caplog):
    lec(tmp_path)
    (tmp_path / "L-0003.md").write_text("sin frontmatter", encoding="utf-8")
    (tmp_path / "L-0004.md").write_text(FRONT.format(id="X-1", fecha="2026-10-01", estado="activa", t="t"), encoding="utf-8")
    (tmp_path / "L-0005.md").write_text(FRONT.format(id="L-0005", fecha="2026-10-01", estado="rara", t="t"), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="agent_lanes"):
        assert [l.id for l in lecciones.cargar(tmp_path)] == ["L-0002"]
    assert caplog.text.count("ignorada") == 3


def test_resumen_con_y_sin_lecciones_en_el_prompt(tmp_path, monkeypatch):
    lane = SimpleNamespace(name="claude-hub", board="default", remote="origin", base="main")
    task = {"id": "t_1", "title": "x", "body": "y"}
    monkeypatch.setattr(lecciones, "LECCIONES_DIR", tmp_path / "vacio")
    sin = build_prompt(task, lane)
    assert "Lecciones del hub" not in sin
    d = tmp_path / "lec"
    d.mkdir()
    lec(d)
    lec(d, "L-0003", estado="retirada")
    lec(d, "L-0004", t="Solo otro carril")
    (d / "L-0004.md").write_text((d / "L-0004.md").read_text(encoding="utf-8").replace("estado: activa", "estado: activa\ncarriles: [claude-scraper]"), encoding="utf-8")
    monkeypatch.setattr(lecciones, "LECCIONES_DIR", d)
    con = build_prompt(task, lane)
    assert con.startswith(sin) and "L-0002" in con and "L-0003" not in con and "L-0004" not in con


def test_resumen_respeta_el_tope(tmp_path):
    for n in range(2, 12):
        lec(tmp_path, f"L-{n:04d}", t="T" * 80)
    txt = lecciones.resumen("", tmp_path, max_chars=400)
    assert len(txt) <= 400 + 60 and "más en docs/lecciones" in txt


# --- contador de disparos --------------------------------------------------------------------------------------------

def test_contador_de_disparos_y_retirables(tmp_path):
    st, d = tmp_path / "st", tmp_path / "lec"
    d.mkdir()
    lec(d, "L-0002", fecha="2026-01-01")
    lec(d, "L-0003", fecha="2026-01-01")
    lecciones.registrar_disparo("L-0003", now=NOW - 10 * 86400, state_dir=st)
    lecciones.registrar_disparo("L-0003", now=NOW - 5 * 86400, state_dir=st)
    lecciones.registrar_disparo("basura", state_dir=st)
    assert lecciones.disparos(st)["L-0003"]["n"] == 2 and "basura" not in lecciones.disparos(st)
    assert [l.id for l in lecciones.retirables(NOW, 60, d, st)] == ["L-0002"]  # nunca disparada y vieja


def test_regla_con_leccion_la_cita_en_el_diagnostico():
    cfg = bloqueos.load_config()
    assert bloqueos.clasificar("toca rutas vetadas", cfg).leccion == "L-0001"
    assert bloqueos.clasificar("timed out", cfg).leccion == ""
