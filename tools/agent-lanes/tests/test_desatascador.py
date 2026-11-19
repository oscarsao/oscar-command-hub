"""Desatascador: tarjetas simuladas (Rama-origen, transitorio, espera de SHA, bucle). Sin red, sin Hermes, sin Telegram."""
from types import SimpleNamespace

from agent_lanes import desatascador as d

NOW = 1_800_000_000.0
LANE = SimpleNamespace(name="claude-x", board="b", kind="implement", repo="R", remote="origin", base="main")
CFG = d.Ajustes(activo=True, seco=False)


class FakeKanban:
    """Doble mínimo del kanban: una tarjeta por id con comentarios; registra cada llamada de escritura."""

    def __init__(self, cards):
        self.cards, self.calls = cards, []

    def list_status_all(self, status):
        return [{"id": t, "assignee": c["assignee"], "status": c["status"]} for t, c in self.cards.items()
                if c["status"] == status]

    def show(self, tid):
        c = self.cards[tid]
        return {"task": {"id": tid, "assignee": c["assignee"], "status": c["status"], "body": c["body"]},
                "comments": c["comments"], "events": c.get("events", []), "parents": c.get("parents", [])}

    def comment(self, tid, text, author=None):
        self.calls.append(("comment", tid, text))
        self.cards[tid]["comments"].append({"author": author, "body": text, "created_at": NOW})
        return True

    def reassign(self, tid, profile, reason):
        self.calls.append(("reassign", tid, profile))
        return True

    def unblock(self, tid):
        self.calls.append(("unblock", tid))
        self.cards[tid]["status"] = "ready"
        return True

    def append_body_line(self, tid, line):
        self.calls.append(("body", tid, line))
        self.cards[tid]["body"] += "\n" + line
        return True

    def schedule(self, tid):
        self.calls.append(("schedule", tid))
        self.cards[tid]["status"] = "scheduled"
        return True


class FakeGit:
    def __init__(self, existe=True, en_base=False):
        self.existe, self.en_base = existe, en_base

    def rama_existe(self, lane, rama):
        return self.existe

    def padre_en_base(self, lane, rama, base):
        return self.en_base


def card(reason, *, body="Haz algo", t=NOW - 3600, comments=(), status="blocked", parents=(), kind=None):
    """`kind` = kind del evento `blocked` (transient | needs_input); sin él no hay evento y el texto no basta."""
    ev = [{"kind": "blocked", "payload": {"reason": reason, "kind": kind}, "created_at": t}] if kind else []
    return {"assignee": "claude-x", "status": status, "body": body, "parents": list(parents), "events": ev,
            "comments": [{"author": "w", "body": f"BLOCKED: {reason}", "created_at": t}, *comments]}


def make(tmp_path, cards, git=None, now=NOW):
    kb = FakeKanban(cards)
    sent = []
    des = Desatascador = d.Desatascador({"claude-x": LANE}, lambda b: kb, send=sent.append, git=git or FakeGit(),
                                        state_path=tmp_path / "s.json", clock=lambda: now)
    return des, kb, sent


def settings(tmp_path, **kw):
    import yaml
    p = tmp_path / "lanes.yaml"
    p.write_text(yaml.safe_dump({"desatascador": kw}), encoding="utf-8")
    return p


def test_apagado_por_defecto_no_hace_nada(tmp_path):
    des, kb, _ = make(tmp_path, {"t_aaaaaaaa": card("el worker no terminó: timed out", kind="transient")})
    des.settings_path = tmp_path / "no-existe.yaml"
    assert des.ejecutar() == [] and kb.calls == []
    assert d.load(tmp_path / "no-existe.yaml") == d.Ajustes()
    assert d.Ajustes().activo is False and d.Ajustes().seco is True


def test_seco_solo_informa_sin_modificar(tmp_path):
    des, kb, sent = make(tmp_path, {"t_aaaaaaaa": card("timed out", kind="transient")})
    des.settings_path = settings(tmp_path, enabled=True, seco=True)
    assert des.ejecutar() == [] and kb.calls == [] and sent == []
    assert d._State(tmp_path / "s.json").data["informe_seco"][0]["regla"] == "reintento"


def test_rama_origen_la_anade_y_reencola_reasignando_antes(tmp_path):
    msg = "la tarjeta debe llevar una línea `Rama-origen: lane/t_1234abcd` para traer ese código"
    des, kb, _ = make(tmp_path, {"t_aaaaaaaa": card(msg)})
    hechas = des.ejecutar(CFG)
    assert [a.regla for a in hechas] == ["rama_origen"]
    tipos = [c[0] for c in kb.calls]
    assert tipos == ["body", "comment", "reassign", "unblock"]  # reasigna ANTES de desbloquear
    assert kb.calls[0][2] == "Rama-origen: lane/t_1234abcd"
    assert "Desatascador:" in kb.calls[1][2]


def test_rama_origen_no_valida_o_inexistente_o_repetida_no_se_toca(tmp_path):
    cards = {
        "t_00000001": card("falta `Rama-origen: main`"),
        "t_00000002": card("falta `Rama-origen: lane/t_1234abcd`", body="x\nRama-origen: lane/t_9999aaaa"),
        "t_00000003": card("falta `Rama-origen: lane/t_00000003`"),
    }
    des, kb, _ = make(tmp_path, cards)
    assert des.ejecutar(CFG) == [] and kb.calls == []
    des2, kb2, _ = make(tmp_path, {"t_00000004": card("falta `Rama-origen: lane/t_1234abcd`")}, git=FakeGit(existe=False))
    assert des2.ejecutar(CFG) == [] and kb2.calls == []


def test_fuera_del_repo_y_estructural_no_se_reencolan(tmp_path):
    cards = {
        "t_00000001": card("LIMITACION-GUARD: toca producción o variables de entorno; timed out"),
        "t_00000002": card("toca rutas vetadas"),
        "t_00000003": card("hace falta aplicar la migración alembic"),
        "t_00000004": card("falta `Rama-origen: lane/t_1234abcd` y desplegar a producción"),
    }
    des, kb, _ = make(tmp_path, cards)
    assert des.ejecutar(CFG) == [] and kb.calls == []


def test_transitorio_espera_creciente(tmp_path):
    # bloqueada hace 5 min: la primera espera es 10 min -> todavía no
    des, kb, _ = make(tmp_path, {"t_aaaaaaaa": card("Tiempo agotado: timed out", t=NOW - 300, kind="transient")})
    assert des.ejecutar(CFG) == [] and kb.calls == []
    # hechos=1 -> espera 20 min; bloqueada hace 15 min -> no; hace 25 -> sí
    prev = [{"author": "d", "body": "Desatascador: reintento 1/2 del fallo transitorio; reencolada", "created_at": NOW - 4000}]
    des, kb, _ = make(tmp_path, {"t_aaaaaaaa": card("timed out", t=NOW - 900, comments=prev, kind="transient")})
    assert des.ejecutar(CFG) == []
    des, kb, _ = make(tmp_path, {"t_aaaaaaaa": card("timed out", t=NOW - 1500, comments=prev, kind="transient")})
    assert [a.regla for a in des.ejecutar(CFG)] == ["reintento"]
    assert "reintento 2/2" in kb.calls[0][2]


def test_ninguna_se_reencola_mas_de_dos_veces_y_avisa_una_sola_vez(tmp_path):
    """Bucle simulado: el worker vuelve a bloquear tras cada reintento. 10 pasadas -> 2 reintentos y 1 aviso."""
    cards = {"t_aaaaaaaa": card("runner reiniciado", t=NOW - 10_000, kind="transient")}
    reloj = [NOW]
    kb = FakeKanban(cards)
    sent = []
    des = d.Desatascador({"claude-x": LANE}, lambda b: kb, send=sent.append, git=FakeGit(),
                         state_path=tmp_path / "s.json", clock=lambda: reloj[0])
    for _ in range(10):
        des.ejecutar(CFG)
        c = cards["t_aaaaaaaa"]
        if c["status"] == "ready":  # el worker la vuelve a bloquear por lo mismo, mucho tiempo después
            reloj[0] += 100_000
            c["status"] = "blocked"
            c["comments"].append({"author": "w", "body": "BLOCKED: runner reiniciado", "created_at": reloj[0] - 10})
            c["events"] = [{"kind": "blocked", "payload": {"kind": "transient"}, "created_at": reloj[0] - 10}]
        else:
            reloj[0] += 100_000
    reintentos = [x for x in kb.calls if x[0] == "unblock"]
    assert len(reintentos) == 2
    textos = [x[2] for x in kb.calls if x[0] == "comment"]
    assert any("reintento 1/2" in t for t in textos) and any("reintento 2/2" in t for t in textos)
    assert sum("reintentos agotados" in t for t in textos) == 1 and len(sent) == 1


def test_espera_de_sha_programa_y_reencola_al_cumplirse(tmp_path):
    cards = {"t_bbbbbbbb": card("espera a que lane/t_cccccccc llegue a main (rama sin fusionar)")}
    git = FakeGit(en_base=False)
    des, kb, _ = make(tmp_path, cards, git=git)
    assert [a.regla for a in des.ejecutar(CFG)] == ["espera_sha"]
    assert ("schedule", "t_bbbbbbbb") in kb.calls and cards["t_bbbbbbbb"]["status"] == "scheduled"
    n = len(kb.calls)
    assert des.ejecutar(CFG) == [] and len(kb.calls) == n  # sigue esperando, sin repetir comentario
    git.en_base = True
    assert [a.regla for a in des.ejecutar(CFG)] == ["espera_cumplida"]
    assert kb.calls[-1] == ("unblock", "t_bbbbbbbb") and kb.calls[-2][0] == "reassign"
    assert "t_bbbbbbbb" not in d._State(tmp_path / "s.json").data["esperas"]


def test_tope_de_acciones_por_pasada(tmp_path):
    cards = {f"t_0000000{i}": card("timed out", t=NOW - 10_000, kind="transient") for i in range(8)}
    des, kb, _ = make(tmp_path, cards)
    assert len(des.ejecutar(d.Ajustes(activo=True, seco=False, max_por_pasada=3))) == 3


def test_resumen_de_la_manana(tmp_path):
    des, kb, sent = make(tmp_path, {"t_aaaaaaaa": card("timed out", t=NOW - 10_000, kind="transient")})
    des.ejecutar(CFG)
    txt = des.resumen()
    assert txt and "t_aaaaaaaa" in txt and "reintento" in txt
    assert make(tmp_path / "otra", {})[0].resumen() is None


def test_dry_run_cli_no_modifica_nada(tmp_path, capsys):
    des, kb, sent = make(tmp_path, {"t_aaaaaaaa": card("timed out", t=NOW - 10_000, kind="transient")})
    out = []
    assert d.main(["--dry-run"], des=des, out=out.append) == 0
    assert "t_aaaaaaaa" in out[1] and kb.calls == [] and sent == []
    assert not (tmp_path / "s.json").exists()


def test_ajustes_invalidos_valen_el_defecto(tmp_path):
    p = settings(tmp_path, enabled="si", seco=3, minutos=-1, transitorios="(", resumen_hora=99)
    cfg = d.load(p)
    assert cfg == d.Ajustes()


# ---- t_c234ad00: los tres falsos positivos del dry-run real (fixtures con el id de cada tarjeta) ----

class GitRamas(FakeGit):
    """padre_en_base por rama: {rama: True|False|None}."""

    def __init__(self, ramas):
        super().__init__()
        self.ramas = ramas

    def padre_en_base(self, lane, rama, base):
        return self.ramas.get(rama)


def test_t_62a1137b_decision_ya_tomada_no_es_transitorio(tmp_path):
    """El texto del worker cita «Timeout waiting for worker», pero el bloqueo es needs_input: no se reencola."""
    motivo = "Decisión de Oscar ya tomada: opción B. Timeout waiting for worker no es el fallo; falta confirmar el alcance"
    des, kb, _ = make(tmp_path, {"t_62a1137b": card(motivo, kind="needs_input", t=NOW - 99_999)})
    assert des.planificar(CFG) == [] and des.ejecutar(CFG) == [] and kb.calls == []
    # sin evento (kind desconocido) tampoco: el texto solo no basta
    des, kb, _ = make(tmp_path, {"t_62a1137b": card(motivo, t=NOW - 99_999)})
    assert des.planificar(CFG) == []
    # el mismo texto marcado como transient sí es reintento (el kind manda)
    des, _, _ = make(tmp_path, {"t_62a1137b": card(motivo, kind="transient", t=NOW - 99_999)})
    assert [a.regla for a in des.planificar(CFG)] == ["reintento"]


def test_t_1d664dd7_espera_a_dos_ramas_solo_al_cumplirse_ambas(tmp_path):
    motivo = "Espero a que se fusionen las ramas padre lane/t_11111111 (OH-12) y lane/t_22222222 (OH-09)"

    def cards():
        return {"t_1d664dd7": card(motivo, status="blocked")}
    # llega la primera, la segunda no: sigue esperando (no espera_cumplida)
    git = GitRamas({"lane/t_11111111": True, "lane/t_22222222": False})
    des, kb, _ = make(tmp_path, cards(), git=git)
    plan = des.planificar(CFG)
    assert [a.regla for a in plan] == ["espera_sha"] and plan[0].espera["rama"] == "lane/t_22222222"
    assert plan[0].espera["ramas"] == ["lane/t_11111111", "lane/t_22222222"]
    # ambas en la base: ahora sí
    git = GitRamas({"lane/t_11111111": True, "lane/t_22222222": True})
    des, kb, _ = make(tmp_path, cards(), git=git)
    plan = des.planificar(CFG)
    assert [a.regla for a in plan] == ["espera_cumplida"] and "t_11111111" in plan[0].texto and "t_22222222" in plan[0].texto
    # una no se puede comprobar: no actúa
    git = GitRamas({"lane/t_11111111": True, "lane/t_22222222": None})
    des, _, _ = make(tmp_path, cards(), git=git)
    assert des.planificar(CFG) == []


def test_t_1d664dd7_referencia_sin_rama_identificable_no_actua(tmp_path):
    """Solo una `lane/t_` en el motivo, pero cita OH-09 sin rama: la lista está incompleta, no se actúa."""
    motivo = "Espero a OH-12 (lane/t_11111111) y a OH-09, ramas sin fusionar"
    des, kb, _ = make(tmp_path, {"t_1d664dd7": card(motivo)}, git=GitRamas({"lane/t_11111111": True}))
    assert des.planificar(CFG) == []


def test_t_f027e7a3_las_otras_ramas_padre_no_basta_la_primera(tmp_path):
    motivo = "Espera a que lane/t_48bd200c y las otras ramas padre estén en main (sin fusionar)"
    git = GitRamas({"lane/t_48bd200c": True})
    # con un solo padre identificable el motivo habla de varias: no se da por cumplida
    des, _, _ = make(tmp_path, {"t_f027e7a3": card(motivo)}, git=git)
    assert des.planificar(CFG) == []
    # con los padres enlazados completos se comprueban todos
    git = GitRamas({"lane/t_48bd200c": True, "lane/t_33333333": False})
    des, _, _ = make(tmp_path, {"t_f027e7a3": card(motivo, parents=["t_48bd200c", "t_33333333"])}, git=git)
    assert [a.regla for a in des.planificar(CFG)] == ["espera_sha"]
    git = GitRamas({"lane/t_48bd200c": True, "lane/t_33333333": True})
    des, _, _ = make(tmp_path, {"t_f027e7a3": card(motivo, parents=["t_48bd200c", "t_33333333"])}, git=git)
    assert [a.regla for a in des.planificar(CFG)] == ["espera_cumplida"]


def test_ticker_corre_en_su_propio_hilo_y_no_bloquea(tmp_path):
    import threading
    import time
    entra, sale = threading.Event(), threading.Event()

    class Lento(d.Desatascador):
        def ejecutar(self, cfg=None):
            entra.set()
            sale.wait(5)
            return []

    kb = FakeKanban({})
    des = Lento({"claude-x": LANE}, lambda b: kb, state_path=tmp_path / "s.json",
                settings_path=settings(tmp_path, enabled=True, seco=True, minutos=1))
    t = d.DesatascadorTicker(des)
    t0 = time.monotonic()
    assert t.tick() == []
    assert time.monotonic() - t0 < 1 and entra.wait(2)       # volvió al instante y la pasada está en marcha
    assert t._hilo.is_alive() and t._hilo is not threading.current_thread()
    hilo = t._hilo
    t.tick()                                                 # con una pasada viva no lanza otra
    assert t._hilo is hilo
    sale.set()
    hilo.join(2)
    assert not hilo.is_alive()


def test_ticker_apagado_no_lanza_hilo(tmp_path):
    des = d.Desatascador({"claude-x": LANE}, lambda b: FakeKanban({}), state_path=tmp_path / "s.json",
                         settings_path=settings(tmp_path, enabled=False))
    t = d.DesatascadorTicker(des)
    assert t.tick() == [] and t._hilo is None


def test_tope_de_tiempo_corta_la_pasada(tmp_path):
    cards = {f"t_0000000{i}": card("timed out", t=NOW - 99_999, kind="transient") for i in range(3)}
    des, kb, _ = make(tmp_path, cards)
    assert des.planificar(CFG, limite=0.0) == []             # el límite ya pasó: no procesa ninguna
    assert len(des.planificar(CFG)) == 3
    assert d.load(settings(tmp_path, tope_pasada_s=30)).tope_pasada_s == 30
    assert d.load(settings(tmp_path, tope_pasada_s="x")).tope_pasada_s == d.Ajustes().tope_pasada_s
