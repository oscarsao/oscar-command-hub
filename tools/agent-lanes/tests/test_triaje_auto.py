"""Triaje automático de bloqueos (t_73351cea): un test por patrón y modo, topes y lista «nunca automático". Sin red ni Hermes."""
from types import SimpleNamespace

import pytest

from agent_lanes import ajustes_hub, triaje_auto as ta


class FakeStore:
    def issue(self, rec, spec):
        self.last = (rec, spec)
        return "tok", {"inline_keyboard": [[{"text": b["text"], "callback_data": "tok:0"} for b in spec[0]]]}


class FakeH:
    def __init__(self, tasks, board="b"):
        self.tasks, self.board, self.calls, self.created = tasks, board, [], []

    def list_status_all(self, status):
        return [{"id": t, "assignee": d["assignee"]} for t, d in self.tasks.items() if d["status"] == status]

    def show(self, tid):
        d = self.tasks[tid]
        return {"task": {"status": d["status"], "title": d.get("title", "Tarea"), "assignee": d["assignee"], "body": "cuerpo"},
                "events": [{"kind": "blocked", "payload": {"reason": d["reason"], "kind": d.get("kind", "transient")}}],
                "comments": [{"body": c} for c in d.get("comments", [])]}

    def comment(self, tid, text, author=None):
        self.calls.append(("comment", tid, text))
        return True

    def unblock(self, tid):
        self.calls.append(("unblock", tid))
        return True

    def requeue_triage(self, tid):
        self.calls.append(("requeue", tid))
        return "ready"

    def archive(self, tid):
        self.calls.append(("archive", tid))
        return True

    def create(self, title, body, assignee, **kw):
        self.created.append((title, body, assignee))
        return "t_nuevo001"


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_to(self, chat, thread, text, **kw):
        self.sent.append((text, kw.get("reply_markup")))


def _hub(tasks, hub_h=None):
    h = hub_h or FakeH(tasks)
    lanes = {"claude-hub": SimpleNamespace(name="claude-hub", board="b"),
             "claude-oscarhq": SimpleNamespace(name="claude-oscarhq", board="b2")}
    hs = {"b": h, "b2": FakeH({}, "b2")}
    desk = SimpleNamespace(owner_id="1", store=FakeStore(), notifier=FakeNotifier())
    i = SimpleNamespace(lanes=lanes, hermes_for=lambda b: hs[b], desk=desk, notifier=desk.notifier, dry_run=False)
    desk.integrator = i
    i.hs = hs
    return i, h


@pytest.fixture
def modo(monkeypatch):
    def poner(m, tope=2):
        valores = {"triaje.modo": m, "triaje.tope_intentos": tope}
        monkeypatch.setattr(ajustes_hub, "valor", lambda k: valores.get(k, ajustes_hub.SETTINGS[k].default))
    return poner


def T(reason, status="blocked", **kw):
    return {"status": status, "assignee": "claude-hub", "reason": reason, **kw}


TRANS = "error: could not lock config file .git/config: File exists"
ESPACIOS = "verificación mecánica fallida: git diff --check: trailing whitespace en vendor/x.js"
REPO = "esto no está en este repo, está en oscar-hq"


def _run(tmp_path, tasks, **kw):
    i, h = _hub(tasks)
    return i, h, ta.procesar(i, log_path=tmp_path / "t.jsonl", now=1000.0)


def test_modo_por_defecto_es_aviso():
    assert ajustes_hub.SETTINGS["triaje.modo"].default == "aviso"


def test_patron1_transitorio_auto_reintenta_y_comenta(tmp_path, modo):
    modo("auto")
    i, h, out = _run(tmp_path, {"t_1": T(TRANS)})
    assert out[0]["resultado"] == "aplicado" and ("unblock", "t_1") in h.calls
    assert any(c[0] == "comment" and c[2].startswith("Triaje automático:") for c in h.calls)
    assert ta.leer(tmp_path / "t.jsonl")[0]["patron"] == "transitorio"


def test_patron1_sqlite_readonly(tmp_path, modo):
    modo("auto")
    _, h, out = _run(tmp_path, {"t_1": T("sqlite3.OperationalError: attempt to write a readonly database")})
    assert out[0]["resultado"] == "aplicado" and ("unblock", "t_1") in h.calls


def test_patron2_espacios_comenta_gitattributes_y_desbloquea(tmp_path, modo):
    modo("auto")
    _, h, out = _run(tmp_path, {"t_1": T(ESPACIOS)})
    assert out[0]["patron"] == "espacios" and out[0]["resultado"] == "aplicado"
    assert any(".gitattributes" in c[2] for c in h.calls if c[0] == "comment") and ("unblock", "t_1") in h.calls


def test_patron5_triage_con_causa_transitoria_hace_requeue(tmp_path, modo):
    modo("auto")
    _, h, out = _run(tmp_path, {"t_1": T(TRANS, status="triage")})
    assert out[0]["remedio"] == "requeue" and ("requeue", "t_1") in h.calls
    assert not any(c[0] == "unblock" for c in h.calls)


def test_patron5_triage_con_espacios_incluye_instruccion(tmp_path, modo):
    modo("auto")
    _, h, out = _run(tmp_path, {"t_1": T(ESPACIOS, status="triage")})
    assert ("requeue", "t_1") in h.calls and any(".gitattributes" in c[2] for c in h.calls if c[0] == "comment")


def test_patron3_repo_equivocado_nunca_mueve_solo_ni_en_auto(tmp_path, modo):
    modo("auto")
    i, h, out = _run(tmp_path, {"t_1": T(REPO)})
    assert out[0]["resultado"] == "propuesto"
    assert h.calls == [] and i.hs["b2"].created == []
    texto, markup = i.desk.notifier.sent[0]
    assert "claude-oscarhq" in texto
    assert [b["action"] for b in i.desk.store.last[1][0]] == [ta.APLICAR, ta.DEJAR]


def test_patron3_boton_recrea_y_archiva(tmp_path, modo, monkeypatch):
    modo("aviso")
    monkeypatch.setattr(ta, "LOG_PATH", tmp_path / "t.jsonl")
    i, h = _hub({"t_1": T(REPO, title="Mover cosa")})
    ta.procesar(i, log_path=tmp_path / "t.jsonl")
    rec = i.desk.store.last[0]
    dichos = []
    desk = i.desk
    desk.notifier.send_to = lambda c, t, text, **kw: dichos.append(text)
    assert ta.on_button(ta.APLICAR, rec, {"chat_id": 1}, desk) is True
    assert i.hs["b2"].created[0][0] == "Mover cosa" and i.hs["b2"].created[0][2] == "claude-oscarhq"
    assert ("archive", "t_1") in h.calls


def test_boton_dejar_no_toca_nada(tmp_path, modo, monkeypatch):
    modo("aviso")
    monkeypatch.setattr(ta, "LOG_PATH", tmp_path / "t.jsonl")
    i, h = _hub({"t_1": T(REPO)})
    ta.procesar(i, log_path=tmp_path / "t.jsonl")
    assert ta.on_button(ta.DEJAR, i.desk.store.last[0], None, i.desk) is True
    assert h.calls == [] and ta.leer(tmp_path / "t.jsonl")[-1]["resultado"] == "dejada"


def test_repo_migrateam_no_se_propone(tmp_path, modo):
    modo("auto")
    _, h, out = _run(tmp_path, {"t_1": T("esto no está en este repo, está en migrateam")})
    assert out == [] and h.calls == [] and _hub({})[0].desk.notifier.sent == []  # MigraTeam: nunca automático


def test_modo_aviso_solo_propone(tmp_path, modo):
    modo("aviso")
    i, h, out = _run(tmp_path, {"t_1": T(TRANS)})
    assert out[0]["resultado"] == "propuesto" and h.calls == [] and len(i.desk.notifier.sent) == 1


def test_modo_apagado_no_hace_nada(tmp_path, modo):
    modo("apagado")
    i, h, out = _run(tmp_path, {"t_1": T(TRANS)})
    assert out == [] and h.calls == [] and not (tmp_path / "t.jsonl").exists()


def test_fuera_de_lista_blanca_se_deja_para_oscar_y_se_anota(tmp_path, modo):
    modo("auto")
    i, h, out = _run(tmp_path, {"t_1": T("algo rarísimo que nadie esperaba")})
    assert out[0]["resultado"] == "no_casa" and h.calls == [] and i.desk.notifier.sent == []


@pytest.mark.parametrize("reason,extra", [
    (TRANS + " en producción", {}),
    (TRANS + " alembic upgrade head", {}),
    (TRANS + " leyendo el .env", {}),
    (TRANS + " delete de la rama", {}),
    (TRANS, {"assignee": "claude-migrateam"}),
    (TRANS, {"kind": "needs_input"}),            # decisión de Oscar pendiente
    (TRANS, {"comments": ["hay que desplegar antes"]}),
])
def test_nunca_automatico(tmp_path, modo, reason, extra):
    modo("auto")
    tasks = {"t_1": {**T(reason), **extra}}
    i, h = _hub(tasks)
    i.lanes["claude-migrateam"] = SimpleNamespace(name="claude-migrateam", board="b")
    assert ta.procesar(i, log_path=tmp_path / "t.jsonl") == []
    assert h.calls == [] and i.desk.notifier.sent == []


def test_tope_de_intentos_impide_bucles(tmp_path, modo):
    modo("auto", tope=2)
    i, h = _hub({"t_1": T(TRANS)})
    log = tmp_path / "t.jsonl"
    resultados = []
    for n in range(4):  # el worker vuelve a bloquearse cada vez con un motivo distinto -> firma nueva
        h.tasks["t_1"]["reason"] = f"{TRANS} intento {n}x"
        resultados += [r["resultado"] for r in ta.procesar(i, log_path=log)]
    assert resultados == ["aplicado", "aplicado", "tope", "tope"]
    assert sum(1 for c in h.calls if c[0] == "unblock") == 2


def test_misma_firma_no_se_repite(tmp_path, modo):
    modo("aviso")
    i, h = _hub({"t_1": T(TRANS)})
    ta.procesar(i, log_path=tmp_path / "t.jsonl")
    assert ta.procesar(i, log_path=tmp_path / "t.jsonl") == [] and len(i.desk.notifier.sent) == 1


def test_retro_cuenta_patrones_sin_remedio_y_propone_tarjeta(tmp_path):
    log = tmp_path / "t.jsonl"
    for n in range(3):
        ta.registrar(f"t_{n}", "-", "-", "no_casa", motivo=f"fallo raro número {n}", path=log, now=1000.0)
    ta.registrar("t_9", "-", "-", "no_casa", motivo="otro distinto", path=log, now=1000.0)
    assert ta.sin_patron(1500.0, path=log) == [("fallo raro número …", 3)]
    assert "3× fallo raro" in ta.retro_texto(1500.0, path=log)
    i, h = _hub({})
    creada = ta.proponer_ampliacion(i, "2026-W44", 1500.0, path=log)
    assert creada == "t_nuevo001" and h.created[0][2] == "oscar" and "Recomendada" in h.created[0][1]
    assert ta.sin_patron(1000.0 + 9 * 86400, path=log) == []  # fuera de la ventana de 7 días


def test_remedio_fuera_de_lista_blanca_se_rechaza():
    ok, que = ta.aplicar(FakeH({}), "t_1", {"remedio": "borrar", "patron": "x"}, {"status": "blocked", "title": "", "assignee": ""},
                         ta.DEFAULTS)
    assert not ok and "lista blanca" in que
