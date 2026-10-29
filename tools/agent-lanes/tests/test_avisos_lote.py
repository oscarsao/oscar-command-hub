"""Aprobación en lote de las tarjetas de bajo riesgo desde el resumen «listas para revisar» (avisos_revision.py)."""
from agent_lanes import avisos_revision
from agent_lanes.avisos_revision import AV_LOTE, AV_TAREA, bajo_riesgo, interceptar
from tests.test_avisos_revision import Desk, Notifier, activo, av, botones  # noqa: F401  (fixtures)


class Lane:
    def __init__(self, name="claude-oscarhq", kind="code"):
        self.name, self.kind, self.board = name, kind, "default"


class Hermes:
    def __init__(self, shows):
        self.shows = shows

    def show(self, tid):
        return self.shows[tid]


class LoteDesk:
    """DecisionDesk mínimo: guarda las aprobaciones que se le piden y simula las que fallan."""

    def __init__(self, shows, lane=None, fallan=()):
        lane = lane or Lane()
        self.lanes, self.integrator, self.fallan = {lane.name: lane}, None, set(fallan)
        self.aprobadas, self.retiradas, self.notifier, self.h = [], [], Notifier(), Hermes(shows)

    def hermes_for(self, board):
        return self.h

    def _approve(self, rec, where):
        self.aprobadas.append(rec)
        return rec["task_id"] not in self.fallan

    def _retire(self, rec):
        self.retiradas.append(rec["task_id"])


def show_done(tid, files=("src/ui/boton.tsx",), findings=(), status="done", review="approve"):
    meta = {"branch": f"lane/{tid}", "head_sha": "abc", "changed_files": list(files),
            "review": {"status": review, "findings": list(findings)}}
    return {"task": {"id": tid, "title": f"UI {tid}", "status": status}, "runs": [{"metadata": meta}], "comments": [],
            "events": []}


def tarjetas(av, n, lane="claude-oscarhq", bajo=True, desde=1):
    for i in range(desde, desde + n):
        av.registrar({"id": f"t_{i}", "title": f"Tarea {i}"}, "hecho", lane=lane, bajo=bajo)


def boton_lote(markup):
    return [b for b in botones(markup) if b["action"] == AV_LOTE]


def test_bajo_riesgo_reglas():
    assert bajo_riesgo(["src/a.tsx"], [], "Mejora de botón") is True
    assert bajo_riesgo(["src/a.tsx"], [{"severity": "minor", "file": "a", "detail": "x"}]) is True
    assert bajo_riesgo(["src/a.tsx"], [{"severity": "major", "file": "a", "detail": "x"}]) is False
    assert bajo_riesgo(["src/a.tsx"], [{"severity": "blocker", "file": "a", "detail": "x"}]) is False
    assert bajo_riesgo(["backend/.env.example"], []) is False  # variables de entorno
    assert bajo_riesgo(["src/stripe/checkout.ts"], []) is False  # pagos
    assert bajo_riesgo(["src/a.tsx"], [], "Arreglar pago duplicado") is False  # lo dice el título
    assert bajo_riesgo(["supabase/migrations/1.sql"], []) is False
    assert bajo_riesgo(None, []) is False and bajo_riesgo(["src/a.tsx"], None) is False  # sin datos: no entra


def test_cuatro_de_bajo_riesgo_no_ofrecen_lote_y_cinco_si(av):
    tarjetas(av, 4)
    assert av.tick() and not boton_lote(av.enviados[0][1])
    tarjetas(av, 3, desde=5)  # otro resumen (lote de 3): cuentan también las de antes
    av.tick()
    assert [b["text"] for b in boton_lote(av.enviados[1][1])] == ["✅ Aprobar todas las de bajo riesgo (7)"]
    assert av.enviados[1][1]["rec"]["lotes"] == [{"lane": "claude-oscarhq", "tids": [f"t_{i}" for i in range(1, 8)]}]


def test_las_de_riesgo_y_las_de_otro_carril_no_entran_en_el_lote(av):
    tarjetas(av, 5)
    tarjetas(av, 2, desde=6, bajo=False)  # tocan producción/pagos…: siguen una por una
    tarjetas(av, 2, desde=8, lane="claude-hub")
    av.tick()
    rec = av.enviados[0][1]["rec"]
    assert rec["lotes"] == [{"lane": "claude-oscarhq", "tids": [f"t_{i}" for i in range(1, 6)]}]
    assert "t_6" in rec["tids"]  # el aviso las sigue listando para su ficha individual


def test_boton_lote_aprueba_las_elegibles_y_deja_fuera_las_que_cambiaron(av):
    tarjetas(av, 5)
    av.tick()
    markup = av.enviados[0][1]
    shows = {f"t_{i}": show_done(f"t_{i}") for i in range(1, 6)}
    shows["t_2"] = show_done("t_2", findings=[{"severity": "major", "file": "a", "detail": "x"}])  # cambió su riesgo
    shows["t_3"] = show_done("t_3", status="archived")
    desk = LoteDesk(shows, fallan={"t_5"})
    where = {"chat_id": "-100", "message_id": 7, "user_id": 42}
    assert av.on_button(AV_LOTE, markup["rec"], where, desk, boton_lote(markup)[0]["index"]) is True
    assert [r["task_id"] for r in desk.aprobadas] == ["t_1", "t_4", "t_5"]
    assert all(r["lote"] and r["lote_actor"] == 42 and r["lane"] == "claude-oscarhq" for r in desk.aprobadas)
    assert desk.retiradas == ["t_1", "t_4"]
    texto = desk.notifier.edits[-1][2]
    assert "2 aprobadas (t_1, t_4)" in texto and "t_2, t_3" in texto and "fallaron" in texto and "t_5" in texto
    assert not boton_lote(desk.notifier.edits[-1][3])  # el botón gastado no vuelve
    assert [p["tid"] for p in av._load()["enviadas"]] == ["t_5"]  # las decididas no cuentan para futuros lotes


def test_boton_lote_no_toca_carriles_ops_ni_indices_raros(av):
    tarjetas(av, 5)
    av.tick()
    rec = av.enviados[0][1]["rec"]
    ops = LoteDesk({}, lane=Lane(kind="ops"))
    assert av.on_button(AV_LOTE, rec, None, ops, 0) is False and not ops.aprobadas
    assert av.on_button(AV_LOTE, rec, None, LoteDesk({}), 9) is False


def test_no_avisar_de_una_tarjeta_la_saca_del_lote(av):
    tarjetas(av, 5)
    av.tick()
    markup = av.enviados[0][1]
    av.on_button(AV_TAREA, markup["rec"], None, Desk(), 0)  # 🙈 t_1
    desk = LoteDesk({f"t_{i}": show_done(f"t_{i}") for i in range(1, 6)})
    av.on_button(AV_LOTE, markup["rec"], None, desk, 0)
    assert [r["task_id"] for r in desk.aprobadas] == ["t_2", "t_3", "t_4", "t_5"]


def test_la_aprobacion_real_comenta_lote_via_bot(tmp_path):
    from agent_lanes.decisions import CallbackStore, DecisionDesk
    from tests.test_decisions import LANE, FakeTG
    comentarios = []

    class H:
        def comment(self, tid, body, author=None):
            comentarios.append((tid, body))
            return True

    desk = DecisionDesk(FakeTG(), CallbackStore(tmp_path / "cb"), lanes={LANE.name: LANE}, hermes_for=lambda b: H(),
                        spawn=lambda fn: fn())
    desk.links = None
    desk._find_pr = lambda slug, tid: (7, "https://github.com/x/y/pull/7")
    desk._edit = lambda *a, **k: None
    desk.links = type("L", (), {"repo_slug": staticmethod(lambda lane: "x/y")})()
    rec = {"task_id": "t_1", "board": LANE.board, "lane": LANE.name, "title": "T", "lote": True}
    assert desk._approve(rec, None) is True
    assert comentarios[0][1].startswith("APROBADO-OSCAR ") and " · PR https://github.com/x/y/pull/7 · lote vía bot" in comentarios[0][1]


def test_interceptar_solo_marca_bajo_riesgo_la_done_de_review_con_datos(activo):
    claude = Lane()
    interceptar("done", {"id": "t_a", "title": "Botón"}, "ok", True, lane=claude, changed_files=["src/a.tsx"], findings=[])
    interceptar("done", {"id": "t_b", "title": "Botón"}, "ok", True, lane=claude, changed_files=["a/.env"], findings=[])
    interceptar("done", {"id": "t_c", "title": "Botón"}, "ok", True, lane=claude, changed_files=["src/a.tsx"])
    interceptar("done", {"id": "t_d", "title": "Botón"}, "ok", True, lane=Lane(kind="ops"), changed_files=[], findings=[])
    interceptar("review", {"id": "t_e", "title": "Botón"}, "ok", True)
    assert {p["tid"]: p["bajo"] for p in activo._load()["pendientes"]} == {
        "t_a": True, "t_b": False, "t_c": False, "t_d": False, "t_e": False}
    assert avisos_revision.ACTIVO is activo
