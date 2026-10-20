"""Reconciliación (specs/reconciliacion.md): cierre por PR, obsolescencia, próximo paso/responsable y registro."""
import json

from agent_lanes import reconcile
from agent_lanes.decisions import TO_INTEGRADOR, keyboard_spec
from agent_lanes.notices import next_step, render
from agent_lanes.reconcile import REC_ARCHIVE, REC_KEEP, Reconciler, pr_refs, record_decision

NOW = 1_800_000_000.0


class FakeHermes:
    def __init__(self, cards):
        self.cards = cards  # tid -> {status, title, body, comments, events}
        self.calls = []

    def show(self, tid):
        c = self.cards[tid]
        return {"task": {"id": tid, "status": c["status"], "title": c.get("title", ""), "body": c.get("body", ""),
                         "created_at": c.get("created_at", NOW)},
                "comments": c.get("comments", []), "events": c.get("events", [])}

    def list_status_all(self, status):
        return [{"id": t} for t, c in self.cards.items() if c["status"] == status]

    def comment(self, tid, text, author=None):
        self.calls.append(("comment", tid, text, author))
        return True

    def complete(self, tid, result, metadata):
        self.calls.append(("complete", tid, result))
        self.cards[tid]["status"] = "done"
        return True, ""

    def archive(self, tid):
        self.calls.append(("archive", tid))
        self.cards[tid]["status"] = "archived"
        return True

    def assign(self, tid, profile):
        self.calls.append(("assign", tid, profile))
        return True


def pr(number, state="MERGED", branch="", title="", body=""):
    return {"number": number, "state": state, "mergedAt": "x" if state == "MERGED" else None,
            "headRefName": branch, "title": title, "body": body, "url": f"http://pr/{number}"}


def make(tmp_path, cards, prs, clock=lambda: NOW):
    h, sent = FakeHermes(cards), []
    issued = []

    def issue(rec, spec):
        issued.append((rec, spec))
        return "tok", {"inline_keyboard": [[{"text": "x"}]]}

    rec = Reconciler(lambda b: h, ["default"], lambda: prs, lambda t, m=None: sent.append((t, m)), issue=issue,
                     state_path=tmp_path / "rec.json", clock=clock)
    return rec, h, sent, issued


# --- (a) cierre automático -------------------------------------------------------------------------

def test_vinculos_fuertes_y_el_id_suelto_del_cuerpo_no_cierra():
    p = pr(1, branch="lane/t_aaaaaaaa", title="algo (t_bbbbbbbb)", body="Se apoya en t_cccccccc.\nCierra: t_dddddddd")
    assert pr_refs(p) == ["t_aaaaaaaa", "t_bbbbbbbb", "t_dddddddd"]


def test_pr_mergeado_completa_la_tarjeta_y_avisa_una_vez(tmp_path):
    cards = {"t_aaaaaaaa": {"status": "review", "title": "Mi tarea"}, "t_cccccccc": {"status": "ready"}}
    rec, h, sent, _ = make(tmp_path, cards, [pr(7, branch="lane/t_aaaaaaaa", body="se apoya en t_cccccccc")])
    assert rec.close_from_prs() == ["t_aaaaaaaa"]
    assert ("complete", "t_aaaaaaaa", "Resuelta en PR #7 (mergeado)") in h.calls
    assert cards["t_cccccccc"]["status"] == "ready"
    assert len(sent) == 1 and "Resuelta en PR #7" in sent[0][0] and "Próximo paso" in sent[0][0]
    assert rec.close_from_prs() == [] and len(sent) == 1  # una sola vez, también con estado recargado
    again = Reconciler(lambda b: h, ["default"], lambda: [pr(7, branch="lane/t_aaaaaaaa")], lambda *a: sent.append(a),
                       state_path=tmp_path / "rec.json", clock=lambda: NOW)
    assert again.close_from_prs() == [] and len(sent) == 1


def test_pr_cerrado_sin_merge_propone_con_botones_y_no_archiva(tmp_path):
    cards = {"t_aaaaaaaa": {"status": "blocked"}, "t_bbbbbbbb": {"status": "running"}, "t_cccccccc": {"status": "done"}}
    prs = [pr(2, "CLOSED", branch="lane/t_aaaaaaaa"), pr(3, "MERGED", branch="lane/t_bbbbbbbb"),
           pr(4, "MERGED", branch="lane/t_cccccccc"), pr(5, "OPEN", branch="lane/t_aaaaaaaa")]
    rec, h, sent, issued = make(tmp_path, cards, prs)
    assert rec.close_from_prs() == ["t_aaaaaaaa"]
    assert not [c for c in h.calls if c[0] in ("archive", "complete")]
    assert cards["t_aaaaaaaa"]["status"] == "blocked" and cards["t_bbbbbbbb"]["status"] == "running"
    assert any(c[0] == "comment" and c[1] == "t_aaaaaaaa" for c in h.calls)
    assert len(sent) == 1 and "cerrado sin merge" in sent[0][0] and sent[0][1] is not None
    assert [a["action"] for a in issued[0][1][0]] == [REC_ARCHIVE, REC_KEEP]
    assert rec.obsolete_pass() == []  # ya propuesta: la pasada diaria no la repite


# --- (b) obsolescencia -----------------------------------------------------------------------------

def test_obsolescencia_propone_con_boton_y_nunca_archiva_sola(tmp_path):
    old = NOW - 80 * 3600
    cards = {
        "t_aaaaaaaa": {"status": "blocked", "created_at": NOW - 3600},  # su PR está mergeado
        "t_bbbbbbbb": {"status": "ready", "body": "cubierta por t_dddddddd", "created_at": NOW - 3600},
        "t_cccccccc": {"status": "blocked", "created_at": old},  # >72 h
        "t_dddddddd": {"status": "done"},
        "t_eeeeeeee": {"status": "ready", "created_at": NOW - 3600},  # al día
    }
    rec, h, sent, issued = make(tmp_path, cards, [pr(9, branch="lane/t_aaaaaaaa")])
    assert sorted(rec.obsolete_pass()) == ["t_aaaaaaaa", "t_bbbbbbbb", "t_cccccccc"]
    assert not [c for c in h.calls if c[0] in ("archive", "complete")]
    texts = " ".join(t for t, _ in sent)
    assert "PR #9 está mergeado" in texts and "t_dddddddd (hecha)" in texts and "80 h sin movimiento" in texts
    assert all("Lo da: Oscar" in t for t, _ in sent)
    assert [a["action"] for a in issued[0][1][0]] == [REC_ARCHIVE, REC_KEEP]
    assert rec.obsolete_pass() == []  # ya propuestas: no se repiten


def test_boton_archivar_y_mantener(tmp_path):
    class Desk:
        edits = []
        def _who(self): return "Oscar"
        def _edit_text(self, where, text): self.edits.append(text)

    cards = {"t_aaaaaaaa": {"status": "blocked", "created_at": NOW - 90 * 3600}}
    rec, h, _, _ = make(tmp_path, cards, [])
    r = {"task_id": "t_aaaaaaaa", "board": "default"}
    assert rec.on_button(REC_KEEP, r, None, Desk()) and rec.obsolete_pass() == []
    assert rec.on_button(REC_ARCHIVE, r, None, Desk()) and cards["t_aaaaaaaa"]["status"] == "archived"


def test_ticker_una_pasada_diaria(tmp_path):
    cards = {"t_aaaaaaaa": {"status": "blocked", "created_at": NOW - 90 * 3600}}
    rec, _, sent, _ = make(tmp_path, cards, [])
    t = reconcile.ReconcileTicker(rec, minutes=10, hour=0, clock=lambda: NOW)
    t.tick()
    assert len(sent) == 1
    t._last = 0
    t.tick()
    assert len(sent) == 1


# --- (c) próximo paso y responsable ----------------------------------------------------------------

def test_todo_estado_con_aviso_dice_paso_y_responsable():
    for state in ("needs_input", "blocked", "stuck", "done", "review", "running", "changes"):
        assert "Lo da:" in next_step(state)


def test_si_oscar_no_puede_actuar_lo_da_el_integrador_con_boton():
    for kind in ("console", "config"):
        assert "Lo da: el integrador" in next_step("blocked", kind)
        spec = keyboard_spec("blocked", block_kind=kind)
        assert TO_INTEGRADOR in [b["action"] for row in spec for b in row]
    assert "Lo da: Oscar" in next_step("needs_input")
    assert TO_INTEGRADOR not in [b["action"] for row in keyboard_spec("blocked", block_kind="transient") for b in row]


def test_render_incluye_el_proximo_paso():
    text = render("blocked", "t_aaaaaaaa", "T", "claude-hub", "bloqueada", next_step=next_step("blocked", "console"))
    assert "➡️ Próximo paso: " in text and "Lo da: el integrador (oscar-e2)" in text


# --- (d) registro del integrador -------------------------------------------------------------------

def test_registro_de_decisiones_audita_con_autor(tmp_path):
    h = FakeHermes({"t_aaaaaaaa": {"status": "blocked"}})
    audit = tmp_path / "audit.jsonl"
    ok, _ = record_decision(h, "archivar", "t_aaaaaaaa", "oscar-e2", "decidido en el chat", audit=audit, clock=lambda: 5)
    assert ok and ("archive", "t_aaaaaaaa") in h.calls
    assert h.calls[-1][3] == "oscar-e2" and "REGISTRO por oscar-e2: archivar" in h.calls[-1][2]
    ok, _ = record_decision(h, "reasignar", "t_aaaaaaaa", "oscar-e2", "", profile="oscar", audit=audit)
    assert ok and ("assign", "t_aaaaaaaa", "oscar") in h.calls
    ok, _ = record_decision(h, "comentar", "t_aaaaaaaa", "oscar-e2", "nota", audit=audit)
    ok2, _ = record_decision(h, "resuelta", "t_aaaaaaaa", "oscar-e2", "hecha en #12", audit=audit)
    lines = [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines()]
    assert [l["action"] for l in lines] == ["archivar", "reasignar", "comentar", "resuelta"]
    assert all(l["author"] == "oscar-e2" and l["task"] == "t_aaaaaaaa" for l in lines) and ok and ok2


def test_registro_comentar_falla_si_el_kanban_rechaza_el_comentario(tmp_path):
    class NoComment(FakeHermes):
        def comment(self, tid, text, author=None):
            self.calls.append(("comment", tid, text, author))
            return False

    h, audit = NoComment({"t_aaaaaaaa": {"status": "ready"}}), tmp_path / "audit.jsonl"
    ok, msg = record_decision(h, "comentar", "t_aaaaaaaa", "oscar-e2", "nota", audit=audit)
    assert ok is False and "rechazó" in msg and not audit.exists()
    assert len([c for c in h.calls if c[0] == "comment"]) == 1  # sin segundo intento


def test_registro_rechaza_sin_autor_o_id_o_datos(tmp_path):
    h = FakeHermes({})
    audit = tmp_path / "audit.jsonl"
    for args in (("archivar", "t_aaaaaaaa", ""), ("archivar", "malo", "x"), ("reasignar", "t_aaaaaaaa", "x"),
                 ("comentar", "t_aaaaaaaa", "x")):
        kw = {"profile": None} if args[0] == "reasignar" else {}
        text = "" if args[0] == "comentar" else "x"
        assert record_decision(h, args[0], args[1], args[2], text, audit=audit, **kw)[0] is False
    assert not h.calls and not audit.exists()


def test_cli_tarjeta(tmp_path, monkeypatch):
    monkeypatch.setattr(reconcile, "AUDIT_PATH", tmp_path / "a.jsonl")
    h, out = FakeHermes({"t_aaaaaaaa": {"status": "ready"}}), []
    code = reconcile.main(["comentar", "t_aaaaaaaa", "--autor", "oscar-e2", "visto", "con", "Oscar"],
                          hermes_for=lambda b: h, out=out.append)
    assert code == 0 and h.calls[0][2].endswith("visto con Oscar") and "registrado" in out[0]
