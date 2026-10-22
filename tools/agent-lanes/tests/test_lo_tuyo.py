"""/pendiente («Lo tuyo»), `lanes.py pendiente` y el backlog personal (/personal): lógica pura + botones por línea."""
from __future__ import annotations

import json

from agent_lanes import lo_tuyo as lt
from agent_lanes import personal
from tests.test_commands import command, press, texts
from tests.test_titulos_vistas import _all_text, _card, _center

NOW = 1_000_000.0
AJ = lt.Ajustes()


def _it(kind, tid, **kw):
    return lt.item(kind, tid, kw.pop("title", f"título {tid}"), **kw)


def test_groups_by_priority_and_numbers_the_lines():
    items = [_it("tuyo", "t_00000001", prio=-1, since=NOW - 3600), _it("pregunta", "t_00000002", since=NOW - 3600),
             _it("atascada", "t_00000003", since=NOW - 3600), _it("aprobar", "t_00000004", prio=20, since=NOW)]
    text, shown = lt.render(items, AJ, NOW)
    assert text.index("🔴 Urgente") < text.index("🟡 Normal") < text.index("⚪ Cuando puedas")
    assert [it["tid"] for it in shown] == ["t_00000004", "t_00000003", "t_00000002", "t_00000001"]
    assert "1. ✅ t_00000004" in text and "4. 👁 t_00000001" in text


def test_waiting_more_than_a_day_escalates_to_urgent_and_empty_is_a_celebration():
    assert lt.grupo_de(_it("pregunta", "t_1", since=NOW - 25 * 3600), AJ, NOW) == lt.URGENTE
    assert lt.grupo_de(_it("pregunta", "t_1", since=NOW - 3600), AJ, NOW) == lt.NORMAL
    assert lt.render([], AJ, NOW) == ("Nada pendiente de ti 🎉", [])


def test_more_than_the_limit_is_counted_not_listed_and_duplicates_collapse():
    items = [_it("tuyo", f"t_{i:08x}", since=NOW - i) for i in range(30)] + [_it("tuyo", "t_00000000")]
    text, shown = lt.render(items, lt.Ajustes(max_lineas=5), NOW)
    assert len(shown) == 5 and "30 pendientes" in text and "➕ 25 más" in text


def test_sources_to_items():
    ext = {"hub#7": {"repo": "hub", "slug": "o/r", "pr": 7, "title": "PR", "sha": "a" * 40, "queued_at": 5, "ok_sha": None},
           "hub#8": {"repo": "hub", "slug": "o/r", "pr": 8, "title": "OK", "sha": "b" * 40, "ok_sha": "b" * 40}}
    [only] = lt.de_externos(ext)
    assert only["url"] == "https://github.com/o/r/pull/7"
    est = {"actualizado": 1, "pasos": [
        {"paso": "Fusionar A", "repo": "hub", "pr": "3", "sha": "", "estado": "espera OK de Oscar", "siguiente": "fusionar"},
        {"paso": "Fusionar B", "repo": "", "pr": "", "sha": "", "estado": "hecho", "siguiente": ""},
        {"paso": "Fusionar A", "repo": "hub", "pr": "3", "sha": "", "estado": "hecho", "siguiente": ""}]}
    assert lt.de_estado(est) == []   # el último bloque de cada paso manda
    est["pasos"].pop()
    assert [i["title"] for i in lt.de_estado(est)] == ["Fusionar A"]
    q = {"t_00000009": {"tid": "t_00000009", "title": "x", "lane": "claude-hub", "queued_at": 3, "need": "tocar hooks"}}
    [c] = lt.de_cola_guard(q, lambda lane: "hub")
    assert c["kind"] == "cola" and c["board"] == "hub" and lt.grupo_de(c, AJ, NOW) == lt.URGENTE


def test_ajustes_ignore_invalid_values(tmp_path):
    f = tmp_path / "lo_tuyo.yaml"
    f.write_text("max_lineas: 500\nurgente_horas: -3\nurgente_prioridad: x\nmax_texto: 100\n", encoding="utf-8")
    aj = lt.load_ajustes(f)
    assert aj.max_lineas == 40 and aj.urgente_horas == 24 and aj.urgente_prioridad == 10 and aj.max_texto == 3500
    assert lt.load_ajustes(tmp_path / "no.yaml") == lt.Ajustes()


def test_pendiente_command_one_message_with_one_button_per_line_that_opens_the_card(tmp_path):
    shows = {"t_00000001": _card("t_00000001", "[Personal] Renovar DNI · tuyo"),
             "t_00000002": _card("t_00000002", "[Signal] Elegir · decidir"),
             "t_00000003": _card("t_00000003", "[Hub] Roadmap · épica")}
    cc, desk, bot = _center(tmp_path, shows)
    command(desk, "/pendiente")
    [msg] = bot.sent
    assert "Lo tuyo" in msg["text"] and "Renovar DNI" in msg["text"] and "Elegir" in msg["text"]
    assert "Roadmap" not in msg["text"]
    assert len(texts(msg["markup"])) == 2 and all("👁 Ver" in t for t in texts(msg["markup"]))
    press(desk, msg, 0)   # abre la ficha de esa tarjeta, con sus botones de siempre; el resto de botones siguen vivos
    assert len(bot.sent) == 2 and ("Renovar DNI" in bot.sent[1]["text"] or "Elegir" in bot.sent[1]["text"])
    press(desk, msg, 1, cid="cq2")
    assert len(bot.sent) == 3


def test_pendiente_is_not_for_brand_restricted_operators():
    from agent_lanes.commands import SOLO_TODAS_LAS_MARCAS
    assert "pendiente" in SOLO_TODAS_LAS_MARCAS and "personal" in SOLO_TODAS_LAS_MARCAS


# --- backlog personal ----------------------------------------------------------------------------------------

def _c(tid, title, *, status="ready", assignee="oscar", board="default", since=10.0):
    return {"board": board, "since": since, "task": {"id": tid, "title": title, "status": status, "assignee": assignee,
                                                       "body": "cuerpo"}}


def test_candidates_exclude_lane_decisions_other_owners_and_discarded():
    aj = personal.Ajustes()
    cards = [_c("t_00000001", "[Personal] Renovar DNI · tuyo"), _c("t_00000002", "[DECISIÓN] [Hub] Elegir · decidir"),
             _c("t_00000003", "[Hub] Cosa · implementar", assignee="claude-hub"), _c("t_00000004", "Sin paso"),
             _c("t_00000005", "[Hub] Roadmap · épica"), _c("t_00000006", "x · tuyo", board="personal"),
             _c("t_00000007", "[Personal] Otra · tuyo", status="running"), _c("t_00000008", "[Personal] Dejada · tuyo")]
    got = personal.candidatas(cards, aj, NOW, excluidas={"t_00000008"})
    assert [c["task"]["id"] for c in got] == ["t_00000001", "t_00000004"]


def test_move_archives_the_original_only_if_the_new_card_exists():
    class H:
        def __init__(self, fail=False):
            self.fail, self.calls = fail, []

        def create(self, title, body, assignee, *, priority=None):
            if self.fail:
                raise RuntimeError("board no existe")
            self.calls.append(("create", title, assignee))
            return "t_aaaaaaaa"

        def comment(self, tid, text, author=None):
            self.calls.append(("comment", tid))
            return True

        def archive(self, tid):
            self.calls.append(("archive", tid))
            return True

    card, aj = _c("t_00000001", "[Personal] Renovar DNI · tuyo"), personal.Ajustes()
    dest, src = H(), H()
    ok, new = personal.mover(card, lambda b: dest if b == "personal" else src, aj)
    assert ok and new == "t_aaaaaaaa" and ("archive", "t_00000001") in src.calls and dest.calls[0][2] == "oscar"
    bad, src2 = H(fail=True), H()
    ok, why = personal.mover(card, lambda b: bad if b == "personal" else src2, aj)
    assert not ok and "personal" in why and src2.calls == []   # la original queda intacta


def test_personal_command_proposes_and_buttons_confirm_or_discard(tmp_path):
    shows = {"t_00000001": _card("t_00000001", "[Personal] Renovar DNI · tuyo"),
             "t_00000002": _card("t_00000002", "[DECISIÓN] [Hub] Elegir · decidir")}
    cc, desk, bot = _center(tmp_path, shows)
    cc.personal_state = tmp_path / "personal_descartadas.json"
    command(desk, "/personal")
    [msg] = bot.sent
    assert "t_00000001" in msg["text"] and "t_00000002" not in msg["text"] and "No se mueve nada" in msg["text"]
    assert texts(msg["markup"])[0].startswith("✅ Mover 1") and "Dejarlas" in texts(msg["markup"])[1]
    press(desk, msg, 1)
    assert json.loads(cc.personal_state.read_text(encoding="utf-8")).keys() == {"t_00000001"}
    assert "no vuelvo a proponerlas" in _all_text(bot)
    command(desk, "/personal", uid=2)
    assert "no hay tarjetas tuyas que mover" in bot.sent[-1]["text"]


def test_personal_move_button_reports_when_the_target_board_is_missing(tmp_path):
    shows = {"t_00000001": _card("t_00000001", "[Personal] Renovar DNI · tuyo")}
    cc, desk, bot = _center(tmp_path, shows)
    command(desk, "/personal")
    press(desk, bot.sent[0], 0)   # el fake no sabe crear: nada se mueve y se dice claro
    assert "No he movido nada" in bot.sent[-1]["text"] and "personal" in bot.sent[-1]["text"]


def test_cli_text_and_json_are_read_only(tmp_path, capsys):
    from agent_lanes.config import Lane

    class H:
        def list_status(self, assignee, status, sort="priority"):
            if assignee == "oscar" and status == "ready":
                return [{"id": "t_00000001", "title": "[Personal] Renovar DNI · tuyo", "priority": 0}]
            return []

    lanes = {"claude-hub": Lane(name="claude-hub", board="hub", repo="C:/h", base="main")}
    (tmp_path / "integrador_cola.json").write_text(json.dumps(
        {"entries": {"t_00000009": {"tid": "t_00000009", "title": "bloqueo", "lane": "claude-hub", "queued_at": 5}}}),
        encoding="utf-8")
    assert lt.cli([], lanes, lambda b: H(), tmp_path, now=NOW) == 0
    out = capsys.readouterr().out
    assert "Renovar DNI" in out and "bloqueo" in out and "/pendiente" in out
    assert lt.cli(["--json"], lanes, lambda b: H(), tmp_path, now=NOW) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["grupo"] == "urgente" and {d["tipo"] for d in data} == {"cola", "tuyo"}
