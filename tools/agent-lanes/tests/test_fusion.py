"""Botones de solape/choque (t_ee6ad56c): fusionar, encadenar, se complementan, duplicada, pausar y «Fusionar con…»."""
from types import SimpleNamespace

from agent_lanes import fusion as F
from agent_lanes.choques import Ajustes, Choque, ChoquesTicker, Unidad, registro
from agent_lanes.decisions import BRANDS, DecisionDesk
from agent_lanes.hermes import OSCAR_AUTHOR
from agent_lanes.solapes import load_descartes

LANES = {
    "claude-hub": SimpleNamespace(name="claude-hub", board="default", kind="implement", repo="C:/x/hub"),
    "claude-otro": SimpleNamespace(name="claude-otro", board="default", kind="implement", repo="C:/x/otro"),
}


class Board:
    def __init__(self, cards):
        self.cards = {c["id"]: dict(c) for c in cards}
        self.calls, self.created, self.waits = [], [], {}
        self.link_ok = True

    def list_status_all(self, status):
        return [c for c in self.cards.values() if c["status"] == status]

    def show(self, tid):
        comments = [{"body": f"ESPERA-A {p}: x"} for p in self.waits.get(tid, [])]
        return {"task": self.cards[tid], "comments": comments}

    def create(self, title, body, assignee, *, key=None, **kw):
        self.created.append({"title": title, "body": body, "assignee": assignee, "key": key})
        return "t_00000099"

    def comment(self, tid, text, author=None):
        self.calls.append(("comment", tid, text, author))
        return True

    def archive(self, tid):
        self.calls.append(("archive", tid))
        self.cards[tid]["status"] = "archived"
        return True

    def block(self, tid, kind, reason):
        self.calls.append(("block", tid, kind, reason))
        self.cards[tid]["status"] = "blocked"
        return True

    def link(self, parent, child):
        self.calls.append(("link", parent, child))
        return self.link_ok

    def did(self, name):
        return [c for c in self.calls if c[0] == name]


def card(tid, status="ready", title=None, assignee="claude-hub", body=""):
    return {"id": tid, "status": status, "title": title or f"Tarea {tid}", "assignee": assignee, "body": body or f"cuerpo {tid}"}


class FakeDesk:
    def __init__(self, who="Oscar"):
        self.edits, self.subs, self.who = [], [], who
        self.notifier = SimpleNamespace(edit=lambda chat, msg, text, **kw: self.subs.append((text, kw["reply_markup"])))
        self.store = SimpleNamespace(issue=self.issue)
        self.issued = []

    def _who(self):
        return self.who

    def _edit_text(self, where, text):
        self.edits.append(text)

    def issue(self, rec, spec):
        self.issued.append((rec, spec))
        return "tok", {"inline_keyboard": [[{"text": b["text"], "callback_data": "tok:0"} for b in r] for r in spec]}


WHERE = {"chat_id": "1", "message_id": 5}


def setup(tmp_path, *cards):
    b = Board(cards or (card("t_00000001"), card("t_00000002")))
    f = F.Fusion(LANES, lambda board: b, descartes_path=tmp_path / "d.json")
    rec = F.registro_tarjetas([{"tid": t, "board": "default", "lane": "claude-hub"} for t in b.cards])
    return b, f, rec, FakeDesk()


def test_teclado_tiene_los_cinco_botones():
    textos = [x["text"] for r in F.teclado_solape() for x in r]
    assert textos == ["🔗 Fusionar en una", "⛓ Encadenar", "✅ Se complementan, seguir", "🗂 Duplicada", "⏸ Pausar una"]


def test_fusionar_crea_una_tarjeta_archiva_las_originales_y_firma(tmp_path):
    b, f, rec, desk = setup(tmp_path)
    desk.who = "Linda"
    assert f.on_button(F.FUS_MERGE, rec, WHERE, desk) is True
    assert len(b.created) == 1 and b.created[0]["key"].startswith("fusion-")
    cuerpo = b.created[0]["body"]
    assert "cuerpo t_00000001" in cuerpo and "cuerpo t_00000002" in cuerpo and "Rama-origen" not in cuerpo
    assert [c[1] for c in b.did("archive")] == ["t_00000001", "t_00000002"]
    enlaces = [c for c in b.did("comment") if c[1] == "t_00000001"]
    assert "t_00000099" in enlaces[0][2] and "Linda" in enlaces[0][2]
    assert "t_00000099" in desk.edits[-1]


def test_fusionar_con_una_en_curso_pide_segundo_toque_y_conserva_la_rama(tmp_path):
    b, f, rec, desk = setup(tmp_path, card("t_00000001"), card("t_00000002", "running"))
    assert f.on_button(F.FUS_MERGE, rec, WHERE, desk) is True
    assert b.created == [] and b.did("archive") == []          # primer toque: solo confirma
    assert "en curso" in desk.subs[-1][0]
    assert [b["text"] for r in desk.issued[-1][1] for b in r][0] == "✅ Sí, fusionar"
    assert f.on_button(F.FUS_MERGE_YES, desk.issued[-1][0], WHERE, desk) is True
    assert "Rama-origen: lane/t_00000002" in b.created[0]["body"] and len(b.did("archive")) == 2


def test_fusionar_es_idempotente_si_ya_estan_archivadas(tmp_path):
    b, f, rec, desk = setup(tmp_path)
    f.on_button(F.FUS_MERGE, rec, WHERE, desk)
    b.created.clear()
    assert f.on_button(F.FUS_MERGE, rec, WHERE, desk) is True
    assert b.created == [] and "ya no está" in desk.edits[-1]


def test_fusionar_no_archiva_si_falla_la_creacion(tmp_path):
    b, f, rec, desk = setup(tmp_path)
    b.create = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("hermes caído"))
    assert f.on_button(F.FUS_MERGE, rec, WHERE, desk) is False
    assert b.did("archive") == []


def test_encadenar_la_segunda_espera_a_la_primera(tmp_path):
    b, f, rec, desk = setup(tmp_path, card("t_00000001", "todo"), card("t_00000002", "ready"))
    assert f.on_button(F.FUS_CHAIN, rec, WHERE, desk) is True
    assert b.did("link") == [("link", "t_00000002", "t_00000001")]   # la lista (ready) va antes: es el padre
    espera = [c for c in b.did("comment") if c[1] == "t_00000001"][0]
    assert espera[2].startswith("ESPERA-A t_00000002") and espera[3] == OSCAR_AUTHOR


def test_encadenar_no_repite_ni_encadena_una_en_curso(tmp_path):
    b, f, rec, desk = setup(tmp_path, card("t_00000001", "ready"), card("t_00000002", "ready"))
    b.waits["t_00000002"] = ["t_00000001"]
    f.on_button(F.FUS_CHAIN, rec, WHERE, desk)
    assert b.did("link") == [] and "ya esperaba" in desk.edits[-1]
    b, f, rec, desk = setup(tmp_path, card("t_00000001", "ready"), card("t_00000002", "ready"))
    b.cards["t_00000001"]["status"] = "todo"
    b.cards["t_00000002"]["status"] = "running"
    f.on_button(F.FUS_CHAIN, rec, WHERE, desk)   # la que corre es la primera: la otra espera
    assert b.did("link") == [("link", "t_00000002", "t_00000001")]


def test_se_complementan_marca_la_pareja_y_no_toca_las_tarjetas(tmp_path):
    b, f, rec, desk = setup(tmp_path)
    assert f.on_button(F.FUS_OK, rec, WHERE, desk) is True
    assert load_descartes(tmp_path / "d.json") == {"t_00000001|t_00000002"}
    assert b.calls == [] and "se complementan" in desk.edits[-1]
    assert f.on_button(F.FUS_OK, rec, WHERE, desk) is True   # idempotente
    assert load_descartes(tmp_path / "d.json") == {"t_00000001|t_00000002"}


def test_duplicada_pregunta_cual_y_archiva_con_enlace(tmp_path):
    b, f, rec, desk = setup(tmp_path)
    assert f.on_button(F.FUS_DUP, rec, WHERE, desk) is True
    spec = desk.issued[-1][1]
    assert [x.get("index") for r in spec for x in r][:2] == [0, 1] and b.did("archive") == []
    assert f.on_button(F.FUS_DUP_PICK, rec, WHERE, desk, 1) is True
    assert b.did("archive") == [("archive", "t_00000002")]
    assert "duplicada de t_00000001" in [c for c in b.did("comment") if c[1] == "t_00000002"][0][2]
    assert "t_00000002" in desk.edits[-1]


def test_duplicada_en_curso_pide_confirmacion(tmp_path):
    b, f, rec, desk = setup(tmp_path, card("t_00000001"), card("t_00000002", "running"))
    f.on_button(F.FUS_DUP_PICK, rec, WHERE, desk, 1)
    assert b.did("archive") == [] and "en curso" in desk.subs[-1][0]
    yes_rec = desk.issued[-1][0]
    assert yes_rec["pick"] == 1
    f.on_button(F.FUS_DUP_YES, yes_rec, WHERE, desk)
    assert b.did("archive") == [("archive", "t_00000002")]


def test_pausar_bloquea_la_elegida_y_respeta_la_que_corre(tmp_path):
    b, f, rec, desk = setup(tmp_path, card("t_00000001"), card("t_00000002", "running"))
    f.on_button(F.FUS_PAUSE, rec, WHERE, desk)
    assert "pausar" in desk.subs[-1][0]
    f.on_button(F.FUS_PAUSE_PICK, rec, WHERE, desk, 1)
    assert b.did("block") == [] and "en curso" in desk.edits[-1]
    f.on_button(F.FUS_PAUSE_PICK, rec, WHERE, desk, 0)
    assert b.did("block")[0][:3] == ("block", "t_00000001", "needs_input") and "Oscar" in b.did("block")[0][3]
    f.on_button(F.FUS_PAUSE_PICK, rec, WHERE, desk, 0)   # idempotente: ya está bloqueada
    assert len(b.did("block")) == 1


def test_cancelar_no_toca_nada(tmp_path):
    b, f, rec, desk = setup(tmp_path)
    assert f.on_button(F.FUS_NO, rec, WHERE, desk) is True and b.calls == []


def test_ficha_boton_solo_en_abiertas_de_carril_con_repo(tmp_path):
    b, f, rec, desk = setup(tmp_path)
    fila = f.boton_ficha(b.cards["t_00000001"], "default", desk.issue)
    assert [x["text"] for x in fila] == ["🔗 Fusionar con…"]
    assert f.boton_ficha({**b.cards["t_00000001"], "status": "done"}, "default", desk.issue) is None
    assert f.boton_ficha({**b.cards["t_00000001"], "assignee": "oscar"}, "default", desk.issue) is None


def test_fusionar_con_lista_solo_las_del_mismo_repo_y_fusiona_la_elegida(tmp_path):
    b, f, _, desk = setup(tmp_path, card("t_00000001"), card("t_00000002"), card("t_00000003", assignee="claude-otro"),
                          card("t_00000004", "done"))
    f.boton_ficha(b.cards["t_00000001"], "default", desk.issue)
    rec = desk.issued[-1][0]
    assert f.on_button(F.FUS_WITH, rec, WHERE, desk) is True
    cands = desk.issued[-1][0]["candidates"]
    assert [c["tid"] for c in cands] == ["t_00000002"]            # ni otro repo ni cerrada
    assert f.on_button(F.FUS_WITH_PICK, desk.issued[-1][0], WHERE, desk, 0) is True
    assert len(b.created) == 1 and len(b.did("archive")) == 2


def test_fusionar_con_sin_candidatas_lo_dice(tmp_path):
    b, f, _, desk = setup(tmp_path, card("t_00000001"))
    f.boton_ficha(b.cards["t_00000001"], "default", desk.issue)
    f.on_button(F.FUS_WITH, desk.issued[-1][0], WHERE, desk)
    assert "no hay otras" in desk.edits[-1]


def test_despachador_y_permisos_por_marca():
    """Los fus_* los atiende `Desk.fusion` y el despachador rechaza antes a quien no tiene la marca (rec_marcas)."""
    rec = F.registro_tarjetas([{"tid": "t_00000001", "board": "default", "lane": "claude-hub"},
                               {"tid": "t_00000002", "board": "default", "lane": "claude-scraper"}])
    marcas = DecisionDesk.rec_marcas(rec)
    assert marcas == {BRANDS.get("claude-hub"), BRANDS["claude-scraper"]}   # cada tarjeta aporta su marca al permiso
    assert all(a.startswith("fus_") for a in F.FUS_ACTIONS)


def test_aviso_de_choque_entre_dos_tarjetas_lleva_botones(tmp_path):
    a = Unidad("t_00000001", "t_00000001", board="default", lane="claude-hub")
    bb = Unidad("t_00000002", "t_00000002", board="default", lane="claude-hub")
    ch = Choque("c:/x/hub", "compatible", a, bb, ["archivo x.py"], "fusionar")
    assert [c["tid"] for c in registro(ch)["cards"]] == ["t_00000001", "t_00000002"]
    rama = Unidad("lane/t_00000003", "rama", rama="lane/t_00000003", origen="lane")
    assert registro(Choque("c:/x/hub", "conflicto", a, rama, [], "")) is None   # una rama no es una tarjeta
    sent = []
    t = ChoquesTicker(LANES, lambda b: None, send=lambda text, markup=None: sent.append(markup),
                      state_path=tmp_path / "s.json", issue=FakeDesk().issue)
    t._enviar(ch, Ajustes())
    assert sent[0]["inline_keyboard"][0][0]["text"] == "🔗 Fusionar en una"
