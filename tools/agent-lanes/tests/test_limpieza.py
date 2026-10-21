"""Limpieza propuesta de tarjetas (specs/kanban-limpieza-propuesta.md): reglas, silencio, config, botones y dry-run."""
from datetime import datetime, timezone

from agent_lanes import limpieza
from agent_lanes.limpieza import (LIM_CLOSE, LIM_KEEP, LIM_VIEW, Ajustes, LimpiezaRunner, LimpiezaTicker, detectar,
                                  load)

NOW = 1_800_000_000.0
DAY = 86400


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def card(tid, title="Tarea", status="ready", assignee="claude-hub", created=NOW - 30 * DAY, comments=(), events=()):
    show = {"task": {"id": tid, "title": title, "status": status, "assignee": assignee, "created_at": created},
            "comments": list(comments), "events": list(events)}
    return {"board": "default", "id": tid, "title": title, "status": status, "assignee": assignee, "show": show}


def pr(number, tid, merged_at, state="MERGED"):
    return {"number": number, "state": state, "mergedAt": iso(merged_at) if state == "MERGED" else None,
            "headRefName": f"lane/{tid}", "title": "", "body": "", "url": f"http://pr/{number}"}


def com(author, ts, body="x"):
    return {"author": author, "created_at": ts, "body": body}


def sin(*reglas):
    return Ajustes(reglas={**Ajustes().reglas, **{r: False for r in reglas}})


def run(cards, hechas=(), prs=(), cfg=None):
    return detectar(cards, list(hechas), list(prs), cfg or Ajustes(), NOW)


def test_pr_mergeado_propone_con_evidencia():
    out = run([card("t_aaaaaaaa")], prs=[pr(7, "t_aaaaaaaa", NOW - 3 * DAY)])
    assert [(c.tid, c.regla, c.accion) for c in out] == [("t_aaaaaaaa", "pr_mergeado", "complete")]
    assert "PR #7" in out[0].evidencia and "mergeado" in out[0].motivo


def test_pr_mergeado_pero_reabierta_despues_no_se_propone():
    reabierta = card("t_aaaaaaaa", comments=[com("oscar-telegram", NOW - 1 * DAY, "falta algo, reabro")])
    assert run([reabierta], prs=[pr(7, "t_aaaaaaaa", NOW - 3 * DAY)]) == []
    con_evento = card("t_bbbbbbbb", events=[{"kind": "reopened", "created_at": NOW - 1 * DAY}])
    assert run([con_evento], prs=[pr(8, "t_bbbbbbbb", NOW - 3 * DAY)]) == []


def test_comentario_del_integrador_justo_tras_el_merge_no_es_reapertura():
    c = card("t_aaaaaaaa", comments=[com("reconciliacion", NOW - 3 * DAY + 60), com("integrador", NOW - 3 * DAY + 120)])
    assert [x.regla for x in run([c], prs=[pr(7, "t_aaaaaaaa", NOW - 3 * DAY)])] == ["pr_mergeado"]


def test_pr_cerrado_sin_merge_no_se_propone():
    assert run([card("t_aaaaaaaa")], prs=[pr(7, "t_aaaaaaaa", 0, state="CLOSED")]) == []


def test_hermana_hecha_mismo_titulo_normalizado():
    out = run([card("t_aaaaaaaa", title="[Hub] Arreglar el aviso")],
              hechas=[{"id": "t_dddddddd", "title": "Arreglar el  AVISO"}])
    assert [(c.regla, c.evidencia) for c in out] == [("hermana_hecha", "t_dddddddd (done)")]


def test_decidir_respondida_ultimo_comentario_de_oscar():
    c = card("t_aaaaaaaa", status="blocked", comments=[com("worker", NOW - 5 * DAY, "¿A o B?"),
                                                       com("oscar-telegram", NOW - 2 * DAY, "A")])
    out = run([c])
    assert [x.regla for x in out] == ["decidir_respondida"] and "oscar-telegram" in out[0].evidencia
    sin_resp = card("t_bbbbbbbb", status="blocked", comments=[com("oscar-telegram", NOW - 2 * DAY, "A"),
                                                              com("worker", NOW - 1 * DAY, "¿y ahora?")])
    assert run([sin_resp]) == []


def test_duplicada_propone_archivar_la_mas_nueva():
    vieja = card("t_aaaaaaaa", title="Limpieza de tarjetas resueltas en Telegram", created=NOW - 9 * DAY)
    nueva = card("t_bbbbbbbb", title="Limpieza de tarjetas resueltas por Telegram", created=NOW - 1 * DAY)
    otra = card("t_cccccccc", title="Limpieza de tarjetas resueltas en Telegram", assignee="claude-scraper")
    out = run([vieja, nueva, otra], cfg=sin("oscar_parada"))
    assert [(c.tid, c.regla, c.accion) for c in out] == [("t_bbbbbbbb", "duplicada", "archive")]
    assert "t_aaaaaaaa" in out[0].evidencia


def test_oscar_parada_respeta_el_umbral_de_dias():
    c = card("t_aaaaaaaa", assignee="oscar", created=NOW - 10 * DAY)
    assert [x.regla for x in run([c])] == ["oscar_parada"]
    assert run([c], cfg=Ajustes(dias_sin_movimiento=15)) == []
    reciente = card("t_bbbbbbbb", assignee="oscar", created=NOW - 10 * DAY, comments=[com("oscar", NOW - 2 * DAY)])
    assert run([reciente]) == []


def test_el_comentario_del_propio_job_no_cuenta_como_movimiento():
    c = card("t_aaaaaaaa", assignee="oscar", created=NOW - 10 * DAY,
             comments=[com("limpieza", NOW - 1 * DAY, "propuesta de cierre")],
             events=[{"kind": "commented", "created_at": NOW - 1 * DAY}])
    assert [x.regla for x in run([c])] == ["oscar_parada"]


def test_reglas_desactivadas_y_running_nunca():
    c = card("t_aaaaaaaa", assignee="oscar", created=NOW - 10 * DAY)
    assert run([c], cfg=sin("oscar_parada")) == []
    assert run([card("t_bbbbbbbb", status="running", assignee="oscar", created=NOW - 10 * DAY)]) == []


def test_config_yaml_valores_y_invalidos(tmp_path):
    assert load(tmp_path / "no.yaml") == Ajustes()
    f = tmp_path / "l.yaml"
    f.write_text("hora: 11\ndias_sin_movimiento: 3\ndias_silencio: -4\nsimilitud: 7\ntableros: [a, b]\n"
                 "reglas: {duplicada: false, rara: false, pr_mergeado: 'no'}\nactivo: false\n", encoding="utf-8")
    cfg = load(f)
    assert (cfg.hora, cfg.dias_sin_movimiento, cfg.dias_silencio, cfg.similitud) == (11, 3, 14, 0.85)
    assert cfg.tableros == ("a", "b") and cfg.activo is False
    assert cfg.reglas["duplicada"] is False and cfg.reglas["pr_mergeado"] is True and "rara" not in cfg.reglas
    assert load(tmp_path).hora == 9  # un directorio, no un fichero: defecto, sin lanzar


# --- runner, botones, silencio ---------------------------------------------------------------------------------

class FakeHermes:
    def __init__(self, cards):
        self.cards = {c["id"]: c for c in cards}
        self.calls = []

    def list_status_all(self, status):
        return [{"id": t, "title": c["title"]} for t, c in self.cards.items() if c["status"] == status]

    def show(self, tid):
        return {**self.cards[tid]["show"], "task": {**self.cards[tid]["show"]["task"], "status": self.cards[tid]["status"]}}

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


class Notifier:
    def __init__(self):
        self.sent = []

    def send_to(self, chat, thread, text, html=False, **kw):
        self.sent.append(text)


class Desk:
    def __init__(self):
        self.notifier = Notifier()

    def _who(self):
        return "Oscar"


def make(tmp_path, cards, prs=(), clock=lambda: NOW, yaml_text=None):
    h, sent, issued = FakeHermes(cards), [], []

    def issue(rec, spec):
        issued.append(rec)
        return "tok", {"inline_keyboard": [[{"text": b["text"], "callback_data": b["action"]} for b in spec[0]]]}

    settings = tmp_path / "l.yaml"
    if yaml_text:
        settings.write_text(yaml_text, encoding="utf-8")
    r = LimpiezaRunner(lambda b: h, ["default"], lambda: list(prs), lambda t, m=None: sent.append((t, m)), issue=issue,
                       state_path=tmp_path / "s.json", settings_path=settings, clock=clock)
    return r, h, sent, issued


def parada():
    return card("t_aaaaaaaa", title="Firmar el contrato", assignee="oscar", created=NOW - 20 * DAY)


def test_propuesta_una_linea_y_botones_cerrar_mantener_ver(tmp_path):
    r, h, sent, issued = make(tmp_path, [parada()])
    assert r.proponer() == ["t_aaaaaaaa"]
    text, markup = sent[0]
    assert "t_aaaaaaaa · Firmar el contrato · sin movimiento 20 días · asignada a oscar" in text
    assert [b["callback_data"] for b in markup["inline_keyboard"][0]] == [LIM_CLOSE, LIM_KEEP, LIM_VIEW]
    assert issued[0]["task_id"] == "t_aaaaaaaa" and issued[0]["accion"] == "complete"
    assert ("comment", "t_aaaaaaaa") == h.calls[0][:2] and h.calls[0][2].startswith("propuesta de cierre: sin movimiento")
    assert not any(c[0] in ("complete", "archive") for c in h.calls)  # proponer nunca cierra
    assert r.proponer() == []  # ya propuesta: no se repite dentro de dias_silencio


def test_mantener_silencia_dias_silencio_y_comenta(tmp_path):
    t = [NOW]
    r, h, sent, issued = make(tmp_path, [parada()], clock=lambda: t[0])
    r.proponer()
    desk = Desk()
    assert r.on_button(LIM_KEEP, issued[0], {"chat_id": 1, "thread_id": None}, desk)
    assert h.calls[-1][2].startswith("Oscar: mantener hasta") and "se mantiene hasta" in desk.notifier.sent[0]
    t[0] = NOW + 13 * DAY
    assert r.candidatas() == []  # silenciada M=14 d
    t[0] = NOW + 15 * DAY
    assert [c.tid for c in r.candidatas()] == ["t_aaaaaaaa"]


def test_cerrar_completa_con_el_motivo_en_result(tmp_path):
    r, h, sent, issued = make(tmp_path, [parada()])
    r.proponer()
    desk = Desk()
    assert r.on_button(LIM_CLOSE, issued[0], {"chat_id": 1, "thread_id": None}, desk)
    done = [c for c in h.calls if c[0] == "complete"]
    assert done and "sin movimiento 20 días" in done[0][2] and "asignada a oscar" in done[0][2]
    assert h.cards["t_aaaaaaaa"]["status"] == "done" and "cerrada" in desk.notifier.sent[0]
    assert r.on_button(LIM_CLOSE, issued[0], None, desk)  # idempotente
    assert len([c for c in h.calls if c[0] == "complete"]) == 1


def test_cerrar_duplicada_archiva_y_ver_no_toca_nada(tmp_path):
    a = card("t_aaaaaaaa", title="Limpieza de tarjetas resueltas", created=NOW - 9 * DAY)
    b = card("t_bbbbbbbb", title="Limpieza de tarjetas resueltas", created=NOW - 1 * DAY)
    r, h, sent, issued = make(tmp_path, [a, b])
    r.proponer()
    desk = Desk()
    assert r.on_button(LIM_VIEW, issued[0], {"chat_id": 1, "thread_id": None}, desk)
    assert "t_bbbbbbbb" in desk.notifier.sent[0] and h.cards["t_bbbbbbbb"]["status"] == "ready"
    assert r.on_button(LIM_CLOSE, issued[0], None, desk)
    assert ("archive", "t_bbbbbbbb") in h.calls


def test_no_cierra_una_tarjeta_en_curso(tmp_path):
    r, h, sent, issued = make(tmp_path, [parada()])
    r.proponer()
    h.cards["t_aaaaaaaa"]["status"] = "running"
    assert r.on_button(LIM_CLOSE, issued[0], None, Desk()) is False
    assert not any(c[0] == "complete" for c in h.calls)


def test_umbral_y_regla_cambian_desde_el_yaml_sin_tocar_codigo(tmp_path):
    r, *_ = make(tmp_path, [parada()], yaml_text="dias_sin_movimiento: 30\n")
    assert r.candidatas() == []
    (tmp_path / "l.yaml").write_text("dias_sin_movimiento: 7\nreglas: {oscar_parada: false}\n", encoding="utf-8")
    assert r.candidatas() == []
    (tmp_path / "l.yaml").write_text("dias_sin_movimiento: 7\n", encoding="utf-8")
    assert len(r.candidatas()) == 1


def test_ticker_una_vez_al_dia_y_respeta_activo_y_hora(tmp_path):
    t = [datetime(2026, 10, 21, 10, 0, tzinfo=limpieza_tz()).timestamp()]
    r, h, sent, _ = make(tmp_path, [card("t_aaaaaaaa", assignee="oscar", created=t[0] - 20 * DAY)], clock=lambda: t[0])
    tick = LimpiezaTicker(r, minutes=0, clock=lambda: t[0])
    assert tick.tick() == ["t_aaaaaaaa"] and tick.tick() == []  # el 2.º ya es el mismo día
    t[0] += DAY
    (tmp_path / "otro").mkdir()
    r2, *_ = make(tmp_path / "otro", [card("t_aaaaaaaa", assignee="oscar", created=t[0] - 20 * DAY)],
                  clock=lambda: t[0], yaml_text=None)
    (tmp_path / "otro" / "l.yaml").write_text("activo: false\n", encoding="utf-8")
    assert LimpiezaTicker(r2, minutes=0, clock=lambda: t[0]).tick() == []
    (tmp_path / "otro" / "l.yaml").write_text("hora: 23\n", encoding="utf-8")
    assert LimpiezaTicker(r2, minutes=0, clock=lambda: t[0]).tick() == []  # aún no es la hora


def limpieza_tz():
    from agent_lanes.resumen_carriles import MADRID
    return MADRID


def test_dry_run_imprime_sin_enviar_ni_comentar(tmp_path):
    r, h, sent, _ = make(tmp_path, [parada()])
    lines = []
    assert limpieza.main(["--dry-run"], runner=r, out=lines.append) == 0
    assert "1 candidata" in lines[0] and "[oscar_parada] t_aaaaaaaa" in lines[1]
    assert sent == [] and h.calls == [] and not (tmp_path / "s.json").exists()
